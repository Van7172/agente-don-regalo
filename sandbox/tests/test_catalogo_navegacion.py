"""Taxonomía real (`/catalogo/navegacion`) y búsqueda estructurada por slug.

El bot debe ofrecer solo lo que existe en la web y buscar con los slugs reales
(categoría, filtro, landing). Aquí se verifica el mapeo de parámetros y el uso de
caché, sin salir a la red.
"""
from __future__ import annotations

import pytest

from app.tools import catalog

NAV_PAYLOAD = {
    "success": True,
    "data": {
        "categorias": [{"nombre_categoria": "Desayunos", "url_categoria": "desayunos"}],
        "filtros": [{"nombre_filtro": "Para Hombre", "url_filtro": "para-hombre"}],
    },
}


@pytest.mark.asyncio
async def test_explorar_catalogo_usa_cache_por_defecto(monkeypatch):
    llamadas = {"cache": 0, "directo": 0}

    async def fake_cached(client, url):
        llamadas["cache"] += 1
        assert url.endswith("/catalogo/navegacion")
        return NAV_PAYLOAD

    async def fake_get(client, url, params=None):
        llamadas["directo"] += 1
        return NAV_PAYLOAD

    monkeypatch.setattr(catalog, "_cached_get", fake_cached)
    monkeypatch.setattr(catalog, "get", fake_get)

    out = await catalog.explorar_catalogo(None, {})
    assert out is NAV_PAYLOAD
    assert llamadas == {"cache": 1, "directo": 0}


@pytest.mark.asyncio
async def test_explorar_catalogo_con_temporales_no_usa_cache(monkeypatch):
    capturado = {}

    async def fake_get(client, url, params=None):
        capturado["url"] = url
        capturado["params"] = params
        return NAV_PAYLOAD

    async def fake_cached(client, url):
        raise AssertionError("con temporales no debe usar la caché base")

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog, "_cached_get", fake_cached)

    await catalog.explorar_catalogo(None, {"incluir_temporales": True})
    assert capturado["params"] == {"incluir_temporales": "true"}


@pytest.mark.asyncio
async def test_buscar_productos_pasa_slugs_reales(monkeypatch):
    capturado = {}

    async def fake_get(client, url, params=None):
        capturado["url"] = url
        capturado["params"] = params
        return {"data": [{"id_producto": 1, "nombre": "X", "precio": 10}], "total": 1}

    async def fake_rate(client):
        return 3.5

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", fake_rate)

    await catalog.buscar_productos(
        None,
        {"categoria": "desayunos", "filtro": "para-hombre", "landing": "desayunos-de-cumpleanos", "q": "algo"},
    )

    assert capturado["url"].endswith("/productos/buscar")
    assert capturado["params"]["categoria"] == "desayunos"
    assert capturado["params"]["filtro"] == "para-hombre"
    assert capturado["params"]["landing"] == "desayunos-de-cumpleanos"
    assert capturado["params"]["q"] == "algo"


@pytest.mark.asyncio
async def test_buscar_productos_estampa_categoria_como_default_slug(monkeypatch):
    """Con `categoria` (y sin landing), los productos salen marcados con ese slug."""
    async def fake_get(client, url, params=None):
        return {"data": [{"id_producto": 1, "nombre": "Desayuno Criollo", "precio": 20}], "total": 1}

    async def fake_rate(client):
        return 3.5

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", fake_rate)

    out = await catalog.buscar_productos(None, {"categoria": "desayunos"})
    assert out["data"][0]["categoria_slug"] == "desayunos"


def test_explorar_catalogo_en_el_toolset_del_catalogo():
    from app.harness.registry import AGENTS

    assert "explorar_catalogo" in AGENTS["catalog"].tool_names
