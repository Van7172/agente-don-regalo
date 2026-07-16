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
async def test_categoria_lista_como_el_sitio_expandiendo_hijas(monkeypatch):
    """`/categorias/{slug}/productos` filtra por id_categoria EXACTA: para un padre
    devolvía 2 desayunos cuando el sitio muestra 20 (el resto vive en Criollos,
    Light, de Amor, Temáticos). Hay que listar con `/productos/buscar?categoria=`,
    que expande a las hijas (CATALOGO.md §4.3).
    """
    from app.tools import catalog

    llamadas = []

    async def fake_get(client, url, params=None):
        llamadas.append((url, dict(params or {})))
        return {"data": [{"id_producto": 1, "nombre_producto": "Gustito Criollo",
                          "url_categoria": "desayunos-criollos", "precio_producto": 30}]}

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", lambda c: _rate())

    await catalog.catalogo_categoria(None, {"slug": "desayunos"})

    url, params = llamadas[0]
    assert url.endswith("/productos/buscar"), "debe listar como el sitio, no por id exacta"
    assert params.get("categoria") == "desayunos"


async def _rate():
    return 3.4


@pytest.mark.asyncio
async def test_categoria_funebre_reintenta_incluyendo_funebre(monkeypatch):
    """`buscar` excluye los fúnebres salvo `incluir_funebre`, así que pedir la
    categoría fúnebre devolvía 0. Pedirla explícitamente ES el permiso.
    """
    from app.tools import catalog

    llamadas = []

    async def fake_get(client, url, params=None):
        llamadas.append(dict(params or {}))
        if not (params or {}).get("incluir_funebre"):
            return {"data": []}  # como responde la API sin el permiso
        return {"data": [{"id_producto": 535, "nombre_producto": "Lágrima Fúnebre Blanco",
                          "url_categoria": "lagrimas-funebres", "precio_producto": 35}]}

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", lambda c: _rate())

    result = await catalog.catalogo_categoria(None, {"slug": "arreglos-funebres"})

    assert len(llamadas) == 2, "sin resultados debe reintentar con incluir_funebre"
    assert llamadas[1].get("incluir_funebre") == "1"
    assert result["data"][0]["nombre"] == "Lágrima Fúnebre Blanco"


@pytest.mark.asyncio
async def test_semantico_sin_hits_pregunta_a_la_api_antes_que_ampliar(monkeypatch):
    """Si Qdrant no encuentra en la categoría, manda la API — no se amplía a ciegas.

    Antes se soltaba el filtro de categoría y se rellenaba con lo que fuera: así es
    como un arreglo floral acabó en una lista de desayunos.
    """
    async def fake_sem(client, args):
        return {"data": [], "total": 0, "fuente": "semantico"}

    async def fake_cat(client, args):
        return {
            "data": [
                {"id_producto": 7, "nombre": "Terrario Mágico", "categoria_slug": "terrarios"}
            ],
            "total": 1,
        }

    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)
    monkeypatch.setattr(ex.catalog, "catalogo_categoria", fake_cat)

    data = json.loads(
        await ex.execute_tool("buscar_semantico", {"q": "terrarios", "categoria_slug": "terrarios"})
    )

    assert data["total"] == 1
    assert data["data"][0]["categoria_slug"] == "terrarios"
    assert not data.get("aproximado"), "es de la categoría pedida: no es una alternativa"


@pytest.mark.asyncio
async def test_si_ni_la_api_tiene_la_categoria_los_parecidos_van_marcados(monkeypatch):
    """Recién ahí entra el vectorial, y el cliente debe saber que son alternativas."""
    async def fake_sem(client, args):
        if args.get("categoria_slug"):
            return {"data": [], "total": 0, "fuente": "semantico"}
        return {
            "data": [{"id_producto": 9, "nombre": "Cesta Criolla", "categoria_slug": "cestas"}],
            "total": 1,
            "fuente": "semantico",
        }

    async def fake_cat(client, args):
        return {"data": [], "total": 0}

    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)
    monkeypatch.setattr(ex.catalog, "catalogo_categoria", fake_cat)

    data = json.loads(
        await ex.execute_tool("buscar_semantico", {"q": "chocolates", "categoria_slug": "chocolates"})
    )

    assert data["total"] == 1
    assert data["aproximado"] is True
    assert data["categoria_pedida"] == "chocolates"
