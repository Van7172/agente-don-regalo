"""Fallbacks de catálogo: API LIKE vacía → Qdrant semántico."""
from __future__ import annotations

import json

import pytest

from app.tools import executor as ex


def test_infer_terrarios_not_plantas_generica():
    assert ex._infer_categoria_slug({"q": "terrarios"}) == "terrarios"
    assert ex._infer_categoria_slug({"q": "quiero un terrario de suculentas"}) == "terrarios"


def test_ositos_panda_no_fuerza_peluches():
    # Sin slug previo: no inferir peluches por "ositos"
    assert ex._infer_categoria_slug({"q": "ositos panda"}) is None
    assert ex._wants_broad_theme("ositos panda")


def test_result_product_count():
    assert ex.result_product_count({"data": [1, 2, 3], "total": 3}) == 3
    assert ex.result_product_count({"data": [], "total": 0}) == 0
    assert ex.result_product_count({"error": "x"}) == 0


@pytest.mark.asyncio
async def test_buscar_productos_vacio_cae_a_semantico(monkeypatch):
    async def fake_like(client, args):
        return {"data": [], "total": 0}

    async def fake_sem(client, args):
        assert args.get("q") == "ositos panda"
        assert "categoria_slug" not in args or not args.get("categoria_slug")
        return {
            "data": [
                {
                    "id_producto": 99,
                    "nombre": "Terrario de suculentas Familia Panditas",
                    "precio": 35,
                }
            ],
            "total": 1,
            "fuente": "semantico",
        }

    monkeypatch.setattr(ex.catalog, "buscar_productos", fake_like)
    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)

    raw = await ex.execute_tool("buscar_productos", {"q": "ositos panda"})
    data = json.loads(raw)
    assert data["total"] == 1
    assert "Panditas" in data["data"][0]["nombre"]
    assert data.get("fallback") == "api_like_vacia"


@pytest.mark.asyncio
async def test_buscar_semantico_peluches_se_amplia_con_panda(monkeypatch):
    calls = []

    async def fake_sem(client, args):
        calls.append(dict(args))
        # Primera (sin peluches ya quitado) o con amplia
        if args.get("categoria_slug") == "peluches":
            return {"data": [], "total": 0, "fuente": "semantico"}
        return {
            "data": [{"id_producto": 1, "nombre": "Familia Panditas"}],
            "total": 1,
            "fuente": "semantico",
        }

    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)
    raw = await ex.execute_tool(
        "buscar_semantico",
        {"q": "ositos panda", "categoria_slug": "peluches"},
    )
    data = json.loads(raw)
    assert data["total"] == 1
    # No debe haberse quedado filtrado en peluches
    assert all(c.get("categoria_slug") != "peluches" for c in calls)


@pytest.mark.asyncio
async def test_catalogo_terrarios_vacio_cae_a_semantico(monkeypatch):
    async def fake_cat(client, args):
        return {"data": [], "total": 0}

    async def fake_sem(client, args):
        assert "terrario" in (args.get("q") or "")
        return {
            "data": [
                {"id_producto": 2, "nombre": "Terrario Bambi"},
                {"id_producto": 3, "nombre": "Terrario Familia Panditas"},
            ],
            "total": 2,
            "fuente": "semantico",
        }

    monkeypatch.setattr(ex.catalog, "catalogo_categoria", fake_cat)
    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)

    raw = await ex.execute_tool("catalogo_categoria", {"slug": "terrarios"})
    data = json.loads(raw)
    assert data["total"] >= 2
    assert data.get("fallback") == "catalogo_categoria_vacia"


@pytest.mark.asyncio
async def test_semantico_con_categoria_sin_hits_amplia(monkeypatch):
    calls = []

    async def fake_sem(client, args):
        calls.append(dict(args))
        if args.get("categoria_slug") == "terrarios":
            return {"data": [], "total": 0, "fuente": "semantico"}
        return {
            "data": [{"id_producto": 7, "nombre": "Terrario de piedras Mágicas"}],
            "total": 1,
            "fuente": "semantico",
        }

    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)
    raw = await ex.execute_tool(
        "buscar_semantico",
        {"q": "terrarios", "categoria_slug": "terrarios"},
    )
    data = json.loads(raw)
    assert data["total"] == 1
    assert any(not c.get("categoria_slug") for c in calls)
