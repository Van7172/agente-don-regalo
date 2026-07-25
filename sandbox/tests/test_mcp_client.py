"""Cliente MCP: mapeo a forma canónica, aislamiento de red y degradado a HTTP.

Todo offline: `_call` se stubbea, así que ningún test toca la red. La forma
canónica que se comprueba es la MISMA que produce `catalog.*` vía `adapters`.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.tools import mcp_client


# ── Payload real del MCP (verificado contra producción), recortado ──────────
_BUSCAR = {
    "content": [{"type": "text", "text": "20 productos encontrados, mostrando 1."}],
    "structuredContent": {
        "total": 20,
        "mostrando": 1,
        "pagina": 1,
        "productos": [
            {
                "id": 734,
                "nombre": "Gustito Consintiéndote con Amor",
                "precio_pen": 74.8,
                "precio_usd": 22,
                "categoria_slug": "desayunos-de-amor",
                "en_oferta": False,
                "stock": 100,
                "imagen_url": "https://www.donregalo.pe/app/publicroot/imgs/files/medium/x.webp",
            }
        ],
        "hint": "Usa donregalo_detalle_producto...",
    },
    "isError": False,
}

_DETALLE = {
    "content": [{"type": "text", "text": "Gustito — S/ 74.80, stock 100."}],
    "structuredContent": {
        "id": 734,
        "nombre": "Gustito Consintiéndote con Amor",
        "descripcion_corta": "Desayuno para enamorar.",
        "descripcion": "Contiene: - Croissant de pollo. - Bebida Bio.",
        "precio_pen": 74.8,
        "precio_usd": 22,
        "en_oferta": False,
        "stock": 100,
        "categoria_slug": "desayunos-de-amor",
        "categoria": "Desayunos de Amor",
        "tags": [],
        "ocasiones": [],
        "url": "consintiendote",
        "imagen_url": "https://www.donregalo.pe/app/publicroot/imgs/files/medium/x.webp",
        "relacionados": [],
    },
    "isError": False,
}


@pytest.fixture(autouse=True)
def validacion_imagenes_controlada(monkeypatch):
    """Los tests offline aceptan imágenes salvo que el caso pruebe lo contrario."""
    async def passthrough(_client, products, *, limit):
        return products[:limit]

    monkeypatch.setattr(mcp_client, "valid_products", passthrough)


# ── Mappers puros (sin red, rate fijo) ──────────────────────────────────────

def test_map_product_renombra_claves():
    raw = mcp_client._map_product({"id": 7, "en_oferta": True, "nombre": "X"})
    assert raw["id_producto"] == 7 and "id" not in raw
    assert raw["tiene_oferta"] is True and "en_oferta" not in raw
    assert raw["nombre"] == "X"


def test_buscar_args_mapea_a_tool():
    out = mcp_client._buscar_args(
        {"q": "flores", "categoria": "/arreglos-florales/", "orden": "desc", "id_ocasion": "3"}
    )
    assert out["q"] == "flores"
    assert out["categoria"] == "arreglos-florales"  # sin barras
    assert out["orden"] == "desc"
    assert out["ocasion"] == 3
    assert out["limite"] == mcp_client._IMAGE_CANDIDATE_POOL


def test_list_payload_forma_canonica():
    out = mcp_client._list_payload(_BUSCAR["structuredContent"], rate=4.0)
    p = out["data"][0]
    assert p["id_producto"] == 734
    assert p["precio_usd"] == 22
    assert p["precio_sol"] == 88.0  # 22 * 4.0, lo calcula el agente, no el MCP
    assert p["categoria_slug"] == "desayunos-de-amor"
    assert p["imagen_url"].endswith("/medium/x.webp")
    assert p["stock"] == 100


def test_detail_payload_conserva_descripcion():
    out = mcp_client._detail_payload(_DETALLE["structuredContent"], rate=4.0)
    d = out["data"]
    assert d["id_producto"] == 734
    assert d["precio_sol"] == 88.0
    assert d["categoria"] == "Desayunos de Amor"
    assert "Croissant" in d["descripcion"]
    assert d["imagen_url"].endswith("/medium/x.webp")


def test_payment_payload_limpia_y_mapea():
    structured = {
        "metodos": [
            {"id": 1, "nombre": "Transferencia BCP", "descripcion": "Cuenta: 123\nCCI: 456"}
        ]
    }
    out = mcp_client._payment_payload(structured)
    m = out["data"][0]
    assert m["id_metodo_pago"] == 1
    assert m["nombre_metodo_pago"] == "Transferencia BCP"
    assert "123" in m["descripcion_metodo_pago"] and "456" in m["descripcion_metodo_pago"]


def test_oferta_mcp_conserva_precio_anterior_y_descuento():
    structured = {
        "productos": [
            {
                "id": 9,
                "nombre": "Cesta",
                "precio_usd": 30,
                "precio_antes_usd": 40,
                "precio_antes_pen": 160,
                "descuento_pct": 25,
                "categoria_slug": "cestas",
                "en_oferta": True,
                "imagen_url": "https://donregalo.pe/medium/cesta.webp",
            }
        ]
    }
    product = mcp_client._list_payload(structured, rate=4.0)["data"][0]

    assert product["precio_usd"] == 30
    assert product["precio_lista_usd"] == 40
    assert product["precio_lista_sol"] == 160
    assert product["descuento_pct"] == 25


# ── Funciones públicas con `_call` stubbeado ────────────────────────────────

@pytest.mark.asyncio
async def test_buscar_productos_via_mcp(monkeypatch):
    async def fake_call(client, tool, arguments):
        assert tool == "donregalo_buscar_productos"
        assert arguments["categoria"] == "desayunos"
        return _BUSCAR

    async def fake_rate(client):
        return 4.0

    monkeypatch.setattr(mcp_client, "_call", fake_call)
    monkeypatch.setattr(mcp_client.adapters, "usd_pen_rate", fake_rate)

    res = await mcp_client.buscar_productos(None, {"categoria": "desayunos"})
    assert res["data"][0]["id_producto"] == 734
    assert res["data"][0]["precio_sol"] == 88.0


@pytest.mark.asyncio
async def test_iserror_devuelve_vacio(monkeypatch):
    async def err_call(client, tool, arguments):
        return {"content": [{"type": "text", "text": "slug no existe"}], "isError": True}

    monkeypatch.setattr(mcp_client, "_call", err_call)

    res = await mcp_client.buscar_productos(None, {"categoria": "noexiste"})
    assert res == {"data": [], "total": 0}


@pytest.mark.asyncio
async def test_degrada_a_http_ante_fallo(monkeypatch):
    async def boom(client, tool, arguments):
        raise mcp_client.McpError("MCP caído")

    async def fake_http(client, args):
        return {"data": [{"id_producto": 1}], "total": 1, "_via": "http"}

    monkeypatch.setattr(mcp_client, "_call", boom)
    monkeypatch.setattr(mcp_client.catalog, "buscar_productos", fake_http)

    res = await mcp_client.buscar_productos(None, {"q": "x"})
    assert res.get("_via") == "http"  # cayó a la función HTTP de catalog


@pytest.mark.asyncio
async def test_rastrear_mantiene_sobre_rest(monkeypatch):
    async def fake_call(client, tool, arguments):
        assert tool == "donregalo_rastrear_pedido"
        return {
            "structuredContent": {"codigo": "AB12", "estado": "En preparación", "productos": []},
            "isError": False,
        }

    monkeypatch.setattr(mcp_client, "_call", fake_call)

    res = await mcp_client.rastrear_pedido(None, {"email": "a@b.com", "codigo": "AB12"})
    assert res["success"] is True
    assert res["data"]["codigo"] == "AB12"


@pytest.mark.asyncio
async def test_lifecycle_inicializa_una_vez_y_envia_version(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append((payload, dict(request.headers)))
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "test", "version": "1"},
                    },
                },
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"structuredContent": {"productos": []}, "isError": False},
            },
        )

    monkeypatch.setattr(mcp_client.settings, "donregalo_mcp_url", "https://mcp.test/")
    monkeypatch.setattr(mcp_client.settings, "donregalo_mcp_token", "secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await mcp_client._call(client, "donregalo_buscar_productos", {})
        await mcp_client._call(client, "donregalo_buscar_productos", {})

    assert [payload["method"] for payload, _ in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "tools/call",
    ]
    assert "mcp-protocol-version" not in calls[0][1]
    assert calls[1][1]["mcp-protocol-version"] == "2025-06-18"
    assert calls[2][1]["mcp-protocol-version"] == "2025-06-18"


def test_stream_sse_extrae_la_respuesta_solicitada():
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=(
            'event: message\n'
            'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n'
        ),
        request=httpx.Request("POST", "https://mcp.test/"),
    )

    assert mcp_client._decode_response(response, 7)["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_listado_descarta_imagenes_invalidas(monkeypatch):
    async def fake_call(_client, _tool, _arguments):
        return {
            "structuredContent": {
                "productos": [
                    {"id": 1, "nombre": "Rota", "precio_usd": 10, "imagen_url": "bad"},
                    {"id": 2, "nombre": "Viva", "precio_usd": 20, "imagen_url": "good"},
                ]
            },
            "isError": False,
        }

    async def only_valid(_client, products, *, limit):
        return [p for p in products if p.get("imagen_url") == "good"][:limit]

    async def fake_rate(_client):
        return 4.0

    monkeypatch.setattr(mcp_client, "_call", fake_call)
    monkeypatch.setattr(mcp_client, "valid_products", only_valid)
    monkeypatch.setattr(mcp_client.adapters, "usd_pen_rate", fake_rate)

    result = await mcp_client.buscar_productos(None, {})
    assert [product["id_producto"] for product in result["data"]] == [2]
