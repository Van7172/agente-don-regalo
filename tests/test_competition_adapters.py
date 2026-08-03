"""Adaptadores de competencia: parsean catálogo público sin inventar precios."""
from __future__ import annotations

import json

import httpx
import pytest

from app.services import competition_adapters as adapters


class FakeResponse:
    def __init__(self, status_code: int, payload, *, text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (
            payload if isinstance(payload, str) else json.dumps(payload)
        )

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


@pytest.mark.asyncio
async def test_shopify_parsea_precio_y_url():
    calls: list[str] = []

    async def fetch(url: str):
        calls.append(url)
        if url.endswith("robots.txt"):
            return FakeResponse(200, "User-agent: *\nAllow: /\n")
        if "page=1" in url:
            return FakeResponse(
                200,
                {
                    "products": [
                        {
                            "id": 11,
                            "title": "Caja Magia Love",
                            "handle": "caja-magia-love",
                            "variants": [
                                {"price": "135.00", "compare_at_price": "150.00"}
                            ],
                        }
                    ]
                },
            )
        return FakeResponse(200, {"products": []})

    items = await adapters.crawl_shopify(
        fetch, base="https://magia.pe", max_products=10
    )
    assert len(items) == 1
    assert items[0].nombre == "Caja Magia Love"
    assert items[0].url == "https://magia.pe/products/caja-magia-love"
    assert items[0].precio_sol == 135.0
    assert items[0].precio_tachado_sol == 150.0
    assert any("products.json" in u for u in calls)


@pytest.mark.asyncio
async def test_shopify_respeta_robots_disallow():
    async def fetch(url: str):
        if url.endswith("robots.txt"):
            return FakeResponse(200, "User-agent: *\nDisallow: /products.json\n")
        raise AssertionError("no debía pedir el catálogo")

    # Levanta en vez de devolver []: un bloqueo de robots y un catálogo vacío
    # se veían igual desde el resumen del crawl, y esa confusión costó una
    # tarde el 03-08 con magia.pe y sorprendelima en cero y `errores` vacío.
    with pytest.raises(adapters.CrawlBlocked, match="robots"):
        await adapters.crawl_shopify(fetch, base="https://magia.pe", max_products=5)


@pytest.mark.asyncio
async def test_vtex_arma_url_de_storefront():
    async def fetch(url: str):
        if url.endswith("robots.txt"):
            return FakeResponse(404, "missing")
        return FakeResponse(
            206,
            [
                {
                    "productId": "873",
                    "productName": "Ramo Rosas Rojas",
                    "linkText": "ramo-rosas-rojas",
                    "items": [
                        {
                            "sellers": [
                                {
                                    "commertialOffer": {
                                        "Price": 109.0,
                                        "ListPrice": 109.0,
                                    }
                                }
                            ]
                        }
                    ],
                }
            ],
        )

    items = await adapters.crawl_rosatel_vtex(fetch, max_products=5)
    assert len(items) == 1
    assert items[0].clave_externa == "873"
    assert items[0].url == "https://www.rosatel.pe/ramo-rosas-rojas/p"
    assert items[0].precio_sol == 109.0
    assert items[0].precio_tachado_sol is None


@pytest.mark.asyncio
async def test_match_sin_qdrant_no_inventa_hueco(monkeypatch):
    from app.services import competition_match
    from app.tools import search as search_tool

    monkeypatch.setattr(search_tool, "get_qdrant", lambda: None)
    result = await competition_match.match_product("Ramo de girasoles")
    assert result.es_hueco is False
    assert result.match_score is None


@pytest.mark.asyncio
async def test_un_403_en_la_primera_pagina_no_se_confunde_con_catalogo_vacio():
    """El caso vivo del 03-08: magia.pe y sorprendelima, `scraped: 0`, `errores: []`.

    Shopify corta las peticiones de IPs de datacenter, y el agente vive en una.
    Desde mi máquina los mismos endpoints devolvían 200 con datos, así que el
    resumen decía "0 productos" sobre un catálogo que existe y está lleno.
    """
    async def fetch(url: str):
        if url.endswith("robots.txt"):
            return FakeResponse(200, "User-agent: *\nAllow: /\n")
        return FakeResponse(403, "Forbidden")

    with pytest.raises(adapters.CrawlBlocked, match="403"):
        await adapters.crawl_shopify(fetch, base="https://magia.pe", max_products=5)


@pytest.mark.asyncio
async def test_una_pagina_de_bloqueo_en_html_tampoco_es_un_catalogo_vacio():
    """Un 200 con HTML (captcha, interstitial) es el bloqueo que más engaña."""
    async def fetch(url: str):
        if url.endswith("robots.txt"):
            return FakeResponse(200, "User-agent: *\nAllow: /\n")
        return FakeResponse(200, "<html>Just a moment...</html>")

    with pytest.raises(adapters.CrawlBlocked, match="JSON"):
        await adapters.crawl_shopify(fetch, base="https://magia.pe", max_products=5)


@pytest.mark.asyncio
async def test_un_fallo_a_media_paginacion_conserva_lo_ya_scrapeado():
    """Distinto del anterior: aquí SÍ hay catálogo y cortar es lo correcto.

    Levantar en la página cinco tiraría 200 productos buenos por un fallo
    tardío del sitio.
    """
    llamadas = {"n": 0}

    async def fetch(url: str):
        if url.endswith("robots.txt"):
            return FakeResponse(200, "User-agent: *\nAllow: /\n")
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return FakeResponse(200, {"products": [
                {"id": i, "handle": f"p{i}", "title": f"Ramo {i}",
                 "variants": [{"price": "120.00"}]}
                for i in range(50)
            ]})
        return FakeResponse(500, "boom")

    items = await adapters.crawl_shopify(
        fetch, base="https://magia.pe", max_products=200
    )
    assert len(items) == 50, "lo ya obtenido no se tira por un fallo posterior"
