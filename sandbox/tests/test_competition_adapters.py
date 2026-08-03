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

    items = await adapters.crawl_shopify(
        fetch, base="https://magia.pe", max_products=5
    )
    assert items == []


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
