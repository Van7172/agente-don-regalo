"""
Herramientas HTTP del catálogo de donregalo.pe.
Incluye caché TTL en-memoria para endpoints que cambian raramente.
"""
import time
import logging

import httpx

from app.config import settings
from app.tools import adapters
from app.tools.image_validation import valid_products

log = logging.getLogger(__name__)

DEFAULT_PER_PAGE = 6
_IMAGE_CANDIDATE_POOL = 50

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
#
# Todo lo que devuelve productos o distritos pasa por `adapters`: la API usa tres
# formas distintas de producto y una cuarta de distrito, y entrega los precios en
# USD. Aquí salen ya canónicos y en ambas monedas.


async def _productos(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    *,
    default_slug: str = "",
):
    candidate_params = dict(params or {})
    if "per_page" in candidate_params:
        candidate_params["per_page"] = max(
            int(candidate_params["per_page"]), _IMAGE_CANDIDATE_POOL
        )
    if "limit" in candidate_params:
        candidate_params["limit"] = max(
            int(candidate_params["limit"]), _IMAGE_CANDIDATE_POOL
        )
    payload = await get(client, url, candidate_params or None)
    rate = await adapters.usd_pen_rate(client)
    normalized = adapters.products_payload(payload, rate, default_slug=default_slug)
    if not isinstance(normalized, dict):
        return normalized
    if isinstance(normalized.get("data"), dict):
        # Detalle de UN producto: aquí una imagen rota NO puede tirar el producto.
        # En un listado sí — un producto que no se puede enseñar no se ofrece —
        # pero el detalle responde a "¿qué contiene ESE?" sobre algo que el
        # cliente ya vio. Descartarlo dejaba la pregunta sin respuesta teniendo la
        # lista de items delante (id 734: la foto da 404 y el desayuno se perdía
        # entero). Se cae la foto, no el dato: `render_product_list` ya imprime la
        # ficha sin imagen.
        detalle = normalized["data"]
        products = await valid_products(client, [detalle], limit=1)
        if products:
            return {**normalized, "data": products[0]}
        return {**normalized, "data": {**detalle, "imagen_url": ""}}
    if not isinstance(normalized.get("data"), list):
        return normalized
    products = await valid_products(
        client, normalized["data"], limit=DEFAULT_PER_PAGE
    )
    return {**normalized, "data": products, "total": len(products)}


async def explorar_catalogo(client: httpx.AsyncClient, args: dict):
    """Taxonomía real del sitio (categorías, filtros, ocasiones, landings).

    Es el "paso 0" del catálogo: el bot ofrece SOLO lo que aparece aquí, así no
    inventa tipos que no existen. La taxonomía cambia poco, así que se cachea.
    """
    incluir = bool((args or {}).get("incluir_temporales"))
    url = f"{settings.donregalo_api_base}/catalogo/navegacion"
    if incluir:
        # Con temporales la respuesta difiere: no compartimos caché con la base.
        return await get(client, url, {"incluir_temporales": "true"})
    return await _cached_get(client, url)


async def listar_categorias(client: httpx.AsyncClient, _args: dict):
    return await get(client, f"{settings.donregalo_api_base}/categorias")


async def listar_ocasiones(client: httpx.AsyncClient, _args: dict):
    return await get(client, f"{settings.donregalo_api_base}/ocasiones")


async def buscar_productos(client: httpx.AsyncClient, args: dict):
    params: dict = {"per_page": DEFAULT_PER_PAGE}
    if args.get("q"):
        params["q"] = args["q"]
    # Slugs de la taxonomía real (`explorar_catalogo`): filtran en el servidor.
    for key in ("categoria", "filtro", "landing"):
        value = (args.get(key) or "").strip("/")
        if value:
            params[key] = value
    if args.get("orden") in ("asc", "desc"):
        params["orden"] = args["orden"]
    if args.get("id_ocasion"):
        params["ocasion"] = int(args["id_ocasion"])
    # Cuando el cliente eligió una categoría de la taxonomía, esos productos SON
    # de esa categoría aunque el item no lo repita: se lo estampamos al adapter.
    default_slug = params.get("categoria", "") if not params.get("landing") else ""
    return await _productos(
        client,
        f"{settings.donregalo_api_base}/productos/buscar",
        params,
        default_slug=default_slug,
    )


async def catalogo_categoria(client: httpx.AsyncClient, args: dict):
    """Productos de una categoría, listados COMO EN EL SITIO.

    Ojo con el endpoint: `/categorias/{slug}/productos` filtra por `id_categoria`
    EXACTA, así que para una categoría padre devuelve solo lo colgado del padre y
    no la familia — `desayunos` daba 2 productos cuando el sitio muestra 20 (el
    resto vive en Criollos, Light, de Amor, Temáticos). `/productos/buscar?categoria=`
    sí expande a las hijas, que es como lista la web (CATALOGO.md §4.3 y §6).
    """
    slug = args.get("slug", "").strip("/")
    url = f"{settings.donregalo_api_base}/productos/buscar"
    params: dict = {"categoria": slug, "per_page": DEFAULT_PER_PAGE}
    result = await _productos(client, url, params)

    # `buscar` excluye los fúnebres salvo `incluir_funebre`, así que pedir la
    # categoría fúnebre devolvía 0. Pedirla explícitamente ES el permiso: se
    # reintenta. Para una categoría no fúnebre el reintento no añade nada (el
    # filtro de categoría sigue mandando), así que es seguro y evita adivinar
    # qué slugs son fúnebres — `coronas-para-difuntos` ni lo parece.
    if slug and not (result.get("data") if isinstance(result, dict) else None):
        params["incluir_funebre"] = "1"
        result = await _productos(client, url, params)

    return result


async def productos_destacados(client: httpx.AsyncClient, _args: dict):
    return await _productos(
        client,
        f"{settings.donregalo_api_base}/productos/destacados",
        {"limit": DEFAULT_PER_PAGE},
    )


async def productos_oferta(client: httpx.AsyncClient, _args: dict):
    return await _productos(
        client,
        f"{settings.donregalo_api_base}/productos/ofertas",
        {"per_page": DEFAULT_PER_PAGE},
    )


async def detalle_producto(client: httpx.AsyncClient, args: dict):
    return await _productos(
        client, f"{settings.donregalo_api_base}/productos/{int(args['id_producto'])}"
    )


async def productos_por_ocasion(client: httpx.AsyncClient, args: dict):
    return await _productos(
        client,
        f"{settings.donregalo_api_base}/ocasiones/{int(args['id_ocasion'])}/productos",
        {"per_page": DEFAULT_PER_PAGE},
    )


async def distritos_cobertura(client: httpx.AsyncClient, _args: dict):
    payload = await _cached_get(client, f"{settings.donregalo_api_base}/distritos")
    rate = await adapters.usd_pen_rate(client)
    return adapters.districts_payload(payload, rate)


async def metodos_pago(client: httpx.AsyncClient, _args: dict):
    payload = await _cached_get(client, f"{settings.donregalo_api_base}/metodos-pago")
    return adapters.payment_methods_payload(payload)


async def tipo_cambio(client: httpx.AsyncClient, _args: dict):
    return await _cached_get(client, f"{settings.donregalo_api_base}/configuracion/tipo-cambio")


async def productos_activos(
    client: httpx.AsyncClient, ids: list[int]
) -> set[int] | None:
    """Qué ids siguen vivos en el catálogo. `None` si la API no pudo responder.

    Es la única forma de saber si un producto sigue existiendo. Importa porque las
    dos fuentes del catálogo pueden estar desfasadas: Qdrant se sincroniza cada
    cierto tiempo y el estado de la conversación guarda ids que el cliente vio
    hace horas o días.

    `None` (no se pudo verificar) NO es lo mismo que "no está activo": ante un
    fallo de la API preferimos enseñar de más a bloquear una venta sana.
    """
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return set()
    try:
        payload = await get(
            client,
            f"{settings.donregalo_api_base}/productos/activos",
            {"ids": ",".join(str(i) for i in ids)},
        )
    except Exception as err:
        log.warning("[activos] la API no respondió (%s); no bloqueamos nada", err)
        return None

    data = (payload or {}).get("data")
    if not isinstance(data, list):
        log.warning("[activos] respuesta inesperada: %r", str(payload)[:120])
        return None
    return {int(i) for i in data}


async def rastrear_pedido(client: httpx.AsyncClient, args: dict):
    r = await client.post(
        f"{settings.donregalo_api_base}/pedidos/rastrear",
        json={"email": args.get("email", ""), "codigo": args.get("codigo", "")},
    )
    r.raise_for_status()
    return r.json()
