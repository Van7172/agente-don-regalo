"""
Herramientas HTTP del catálogo de donregalo.pe.
Incluye caché TTL en-memoria para endpoints que cambian raramente.
"""
import time
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

DEFAULT_PER_PAGE = 6

# ─── Caché in-memory con TTL ─────────────────────────────────────────────────
# distritos_cobertura, metodos_pago y tipo_cambio raramente cambian;
# cachearlos elimina 1-3 HTTP calls extra por conversación (~200-400 ms).
_cache: dict[str, tuple[float, object]] = {}  # url → (timestamp, data)


async def _cached_get(client: httpx.AsyncClient, url: str) -> object:
    now = time.monotonic()
    if url in _cache and now - _cache[url][0] < settings.cache_ttl_seconds:
        log.debug("[CACHE-HIT] %s", url)
        return _cache[url][1]
    data = await get(client, url)
    _cache[url] = (now, data)
    return data


async def get(client: httpx.AsyncClient, url: str, params: dict | None = None):
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()


# ─── Implementaciones de herramientas ────────────────────────────────────────

async def listar_categorias(client: httpx.AsyncClient, _args: dict):
    return await get(client, f"{settings.donregalo_api_base}/categorias")


async def listar_ocasiones(client: httpx.AsyncClient, _args: dict):
    return await get(client, f"{settings.donregalo_api_base}/ocasiones")


async def buscar_productos(client: httpx.AsyncClient, args: dict):
    params: dict = {"q": args.get("q", ""), "per_page": DEFAULT_PER_PAGE}
    if args.get("orden") in ("asc", "desc"):
        params["orden"] = args["orden"]
    if args.get("id_ocasion"):
        params["ocasion"] = int(args["id_ocasion"])
    return await get(client, f"{settings.donregalo_api_base}/productos/buscar", params)


async def catalogo_categoria(client: httpx.AsyncClient, args: dict):
    slug = args.get("slug", "").strip("/")
    return await get(
        client,
        f"{settings.donregalo_api_base}/categorias/{slug}/productos",
        {"per_page": DEFAULT_PER_PAGE},
    )


async def productos_destacados(client: httpx.AsyncClient, _args: dict):
    return await get(
        client,
        f"{settings.donregalo_api_base}/productos/destacados",
        {"limit": DEFAULT_PER_PAGE},
    )


async def productos_oferta(client: httpx.AsyncClient, _args: dict):
    return await get(
        client,
        f"{settings.donregalo_api_base}/productos/ofertas",
        {"per_page": DEFAULT_PER_PAGE},
    )


async def detalle_producto(client: httpx.AsyncClient, args: dict):
    return await get(client, f"{settings.donregalo_api_base}/productos/{int(args['id_producto'])}")


async def productos_por_ocasion(client: httpx.AsyncClient, args: dict):
    return await get(
        client,
        f"{settings.donregalo_api_base}/ocasiones/{int(args['id_ocasion'])}/productos",
        {"per_page": DEFAULT_PER_PAGE},
    )


async def distritos_cobertura(client: httpx.AsyncClient, _args: dict):
    return await _cached_get(client, f"{settings.donregalo_api_base}/distritos")


async def metodos_pago(client: httpx.AsyncClient, _args: dict):
    return await _cached_get(client, f"{settings.donregalo_api_base}/metodos-pago")


async def tipo_cambio(client: httpx.AsyncClient, _args: dict):
    return await _cached_get(client, f"{settings.donregalo_api_base}/configuracion/tipo-cambio")


async def rastrear_pedido(client: httpx.AsyncClient, args: dict):
    r = await client.post(
        f"{settings.donregalo_api_base}/pedidos/rastrear",
        json={"email": args.get("email", ""), "codigo": args.get("codigo", "")},
    )
    r.raise_for_status()
    return r.json()
