"""Las URLs del catálogo se validan como imágenes reales antes de mostrarlas."""
from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def allow_mock_hosts(monkeypatch):
    from app.tools import image_validation

    async def allowed(_url: str) -> bool:
        return True

    monkeypatch.setattr(image_validation, "_is_public_url", allowed)


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_html_con_status_200_se_omite_y_se_completa_con_el_siguiente():
    from app.tools.image_validation import valid_products

    products = [
        {"id_producto": 1, "imagen_url": "https://img.test/uno.png"},
        {"id_producto": 2, "imagen_url": "https://img.test/rota.jpg"},
        {"id_producto": 3, "imagen_url": "https://img.test/tres.png"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rota.jpg":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html>Pagina no encontrada</html>",
            )
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=_png()
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await valid_products(client, products, limit=2)

    assert [p["id_producto"] for p in result] == [1, 3]


@pytest.mark.asyncio
async def test_mime_de_imagen_con_bytes_corruptos_se_rechaza():
    from app.tools.image_validation import valid_products

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "image/jpeg"}, content=b"no-es-jpeg"
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await valid_products(
            client,
            [{"id_producto": 9, "imagen_url": "https://img.test/falsa.jpg"}],
            limit=6,
        )

    assert result == []


@pytest.mark.asyncio
async def test_producto_sin_url_se_omite_sin_hacer_request():
    from app.tools.image_validation import valid_products

    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await valid_products(
            client, [{"id_producto": 7, "imagen_url": ""}], limit=6
        )

    assert result == []
    assert calls == 0


@pytest.mark.asyncio
async def test_se_detiene_al_completar_el_cupo():
    from app.tools.image_validation import valid_products

    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=_png()
        )

    products = [
        {"id_producto": i, "imagen_url": f"https://img.test/{i}.png"}
        for i in range(1, 5)
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await valid_products(client, products, limit=2)

    assert [p["id_producto"] for p in result] == [1, 2]
    assert calls == ["/1.png", "/2.png"]


@pytest.mark.asyncio
async def test_busqueda_pide_pool_extra_y_devuelve_solo_imagenes_validas(monkeypatch):
    from app.tools import catalog

    seen_params: dict = {}
    raw_products = [
        {
            "id_producto": 1,
            "nombre_producto": "Uno",
            "precio_producto": 10,
            "imagen_url": "https://img.test/uno.png",
        },
        {
            "id_producto": 2,
            "nombre_producto": "Roto",
            "precio_producto": 10,
            "imagen_url": "https://img.test/rota.jpg",
        },
        {
            "id_producto": 3,
            "nombre_producto": "Tres",
            "precio_producto": 10,
            "imagen_url": "https://img.test/tres.png",
        },
    ]

    async def fake_get(_client, _url, params=None):
        seen_params.update(params or {})
        return {"success": True, "data": raw_products}

    async def fake_rate(_client):
        return 3.5

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rota.jpg":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html/>"
            )
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=_png()
        )

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", fake_rate)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await catalog.buscar_productos(client, {"q": "regalo"})

    assert catalog.DEFAULT_PER_PAGE < seen_params["per_page"] <= 50
    assert [p["id_producto"] for p in result["data"]] == [1, 3]


@pytest.mark.asyncio
async def test_url_privada_se_rechaza_sin_hacer_request(monkeypatch):
    from app.tools import image_validation

    calls = 0

    async def denied(_url: str) -> bool:
        return False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=_png())

    monkeypatch.setattr(image_validation, "_is_public_url", denied)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await image_validation.valid_products(
            client,
            [{"id_producto": 8, "imagen_url": "http://127.0.0.1/private.png"}],
            limit=1,
        )

    assert result == []
    assert calls == 0


@pytest.mark.asyncio
async def test_detalle_con_imagen_rota_pierde_la_foto_pero_no_el_producto(monkeypatch):
    """Antes esto vaciaba `data`: sin foto no había producto.

    Vale para un LISTADO — algo que no se puede enseñar no se ofrece — pero no
    para el detalle, que responde "¿qué contiene ESE?" sobre algo que el cliente
    ya vio. Verificado contra la API real: `GET /productos/{id}` devuelve
    `imagen_url: null` y las cuatro variantes de `imagenes[]` dan 404, así que
    descartar el producto dejaba la pregunta sin respuesta teniendo la lista de
    items delante.

    Lo que SÍ se mantiene: esa URL no llega a WhatsApp. Se cae la foto, no el dato.
    """
    from app.tools import catalog

    async def fake_get(_client, _url, params=None):
        return {
            "success": True,
            "data": {
                "id_producto": 44,
                "nombre_producto": "Detalle roto",
                "precio_producto": 20,
                "imagen_url": "https://img.test/rota.jpg",
            },
        }

    async def fake_rate(_client):
        return 3.5

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"<html/>"
        )

    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", fake_rate)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await catalog.detalle_producto(client, {"id_producto": 44})

    assert result["data"]["id_producto"] == 44
    assert result["data"]["imagen_url"] == ""


def test_solo_hosts_de_imagen_del_dominio_configurado_son_confiables():
    from app.tools.image_validation import _is_trusted_host

    assert _is_trusted_host("donregalo.pe")
    assert _is_trusted_host("cdn.donregalo.pe")
    assert not _is_trusted_host("127.0.0.1")
    assert not _is_trusted_host("imagenes.example")


@pytest.mark.asyncio
async def test_validacion_tiene_limite_total_de_tiempo(monkeypatch):
    import asyncio
    from app.tools import image_validation

    async def slow_handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=_png()
        )

    monkeypatch.setattr(image_validation, "_TOTAL_VALIDATION_SECONDS", 0.01)
    products = [
        {"id_producto": i, "imagen_url": f"https://img.test/{i}.png"}
        for i in range(10)
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(slow_handler)
    ) as client:
        result = await image_validation.valid_products(client, products, limit=6)

    assert result == []
