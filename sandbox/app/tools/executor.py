"""
Punto de entrada único para ejecutar cualquier herramienta del agente.
"""
import json
import logging
import unicodedata

import httpx

from app.tools import catalog, search

log = logging.getLogger(__name__)

# `listar_categorias` y `listar_ocasiones` NO están aquí a propósito: ningún toolset
# las expone (ver `registry.CATALOG_TOOLS`) y dejarlas ejecutables era una segunda
# puerta a la taxonomía. Si el modelo alucina la llamada, ahora recibe "Herramienta
# desconocida" y reintenta con `explorar_catalogo`, en vez de recibir en silencio una
# taxonomía parcial —sin filtros ni landings— de la que extrapolar categorías que no
# existen. Las funciones siguen en `catalog.py` para uso fuera del harness.
_CATALOG_TOOLS = {
    "explorar_catalogo":   catalog.explorar_catalogo,
    "buscar_productos":    catalog.buscar_productos,
    "catalogo_categoria":  catalog.catalogo_categoria,
    "productos_destacados":catalog.productos_destacados,
    "productos_oferta":    catalog.productos_oferta,
    "detalle_producto":    catalog.detalle_producto,
    "productos_por_ocasion":catalog.productos_por_ocasion,
    "distritos_cobertura": catalog.distritos_cobertura,
    "metodos_pago":        catalog.metodos_pago,
    "tipo_cambio":         catalog.tipo_cambio,
    "rastrear_pedido":     catalog.rastrear_pedido,
}

_CAMPAIGN_TERMS = (
    "dia del padre",
    "dia de padre",
    "para papa",
    "para el papa",
    "para papas",
    "feliz dia papa",
    "feliz dia del padre",
    "dia de la madre",
    "dia madre",
    "navidad",
    "san valentin",
    "fiestas patrias",
)

# Si el cliente nombró una categoría en la query y el LLM olvidó categoria_slug, la inyectamos.
# OJO: no mapear "oso/osito" → peluches (puede ser terrario/figuritas, p.ej. Familia Panditas).
_CATEGORY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("desayuno", "desayunos", "brunch", "media manana", "media mañana"), "desayunos"),
    (("terrario", "terrarios"), "terrarios"),
    (("suculenta", "suculentas", "macetero", "maceteros"), "plantas"),
    (("peluche", "peluches"), "peluches"),
    (("planta", "plantas"), "plantas"),
    (("cesta", "cestas", "canasta", "canastas"), "cestas"),
    (("bebe", "bebé", "nacimiento", "baby"), "regalo-para-bebe"),
    (
        ("flor", "flores", "ramo", "ramos", "rosas", "girasol", "tulipan", "arreglo floral"),
        "arreglos-florales",
    ),
)

# Si LIKE/categoría fallan, no insistir en filtro estrecho para estos temas.
_BROAD_THEME_RE = (
    "panda", "pandita", "osito", "ositos", "terrario", "terrarios",
    "suculenta", "figurita", "figuritas",
)

_MIN_RESULTS_OK = 2


def _norm_text(value: object) -> str:
    text = str(value or "").lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(text.replace("-", " ").split())


def _is_free_campaign_search(args: dict) -> bool:
    if args.get("categoria_slug"):
        return False
    q = _norm_text(args.get("q"))
    return any(term in q for term in _CAMPAIGN_TERMS)


def _infer_categoria_slug(args: dict) -> str | None:
    """Infiere slug desde q cuando el modelo no envió categoria_slug."""
    if args.get("categoria_slug"):
        return None
    q = _norm_text(args.get("q"))
    if not q:
        return None
    # Prioridad: desayuno / terrario antes que flores o plantas genéricas.
    for terms, slug in _CATEGORY_HINTS:
        if any(term in q for term in terms):
            return slug
    return None


def _wants_broad_theme(q: str) -> bool:
    n = _norm_text(q)
    return any(t in n for t in _BROAD_THEME_RE)


def result_product_count(result: object) -> int:
    """Cuenta productos en respuestas típicas de la API / Qdrant."""
    if not isinstance(result, dict):
        if isinstance(result, list):
            return len(result)
        return 0
    data = result.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        # Algunas APIs anidan listas
        for v in data.values():
            if isinstance(v, list):
                return len(v)
    total = result.get("total")
    if isinstance(total, int):
        return total
    return 0


def enforce_category(result: object, slug: str) -> object:
    """Descarta lo que NO es de la categoría pedida.

    El cliente pidió desayunos y recibió un arreglo floral. La causa: cuando la
    búsqueda semántica traía pocos resultados, el sistema soltaba el filtro de
    categoría **en silencio** y rellenaba con lo que fuera. Si el cliente nombra
    una categoría, esa categoría es un límite duro, no una sugerencia.
    """
    if not slug or not isinstance(result, dict):
        return result

    data = result.get("data")
    if not isinstance(data, list):
        return result

    objetivo = _norm_text(slug)
    filtrados = [
        p for p in data
        if isinstance(p, dict) and _matches_category(p, objetivo)
    ]

    descartados = len(data) - len(filtrados)
    if descartados:
        log.info(
            "[tool] categoria=%s: %d resultado(s) descartado(s) por no pertenecer",
            slug, descartados,
        )
    return {**result, "data": filtrados, "total": len(filtrados)}


def _matches_category(producto: dict, objetivo: str) -> bool:
    slug = _norm_text(producto.get("categoria_slug"))
    nombre = _norm_text(producto.get("categoria"))
    if not slug and not nombre:
        # Sin categoría no podemos afirmar que pertenezca: fuera. Preferimos
        # mostrar menos que colar un ramo en una lista de desayunos.
        return False
    # `desayunos` cubre `desayunos-criollos`; `plantas` cubre `terrarios` sólo si
    # el slug lo dice, no lo adivinamos.
    return (
        slug == objetivo
        or slug.startswith(objetivo)
        or objetivo.startswith(slug) and bool(slug)
        or objetivo.replace("-", " ") in nombre
    )


async def _semantic_fallback(
    client: httpx.AsyncClient,
    q: str,
    *,
    base_args: dict | None = None,
    drop_category: bool = False,
    reason: str = "",
) -> dict:
    args = dict(base_args or {})
    args["q"] = q
    if drop_category:
        args.pop("categoria_slug", None)
    log.info(
        "[tool] fallback semantico q=%r drop_category=%s (%s)",
        q[:80],
        drop_category,
        reason,
    )
    result = await search.buscar_semantico(client, args)
    if isinstance(result, dict):
        result = dict(result)
        result["fuente"] = result.get("fuente") or "semantico"
        result["fallback"] = reason or "semantico"
    return result


async def execute_tool(name: str, args: dict) -> str:
    """Ejecuta una herramienta y devuelve el resultado como string JSON."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if name == "buscar_productos":
                args = dict(args or {})
                result = await catalog.buscar_productos(client, args)
                # LIKE vacío → Qdrant (cubre "ositos panda", typos, sinónimos).
                if result_product_count(result) == 0:
                    q = (args.get("q") or "").strip()
                    if q:
                        sem = await _semantic_fallback(
                            client,
                            q,
                            base_args={
                                k: args[k]
                                for k in ("excluir_ids", "id_ocasion", "precio_max", "preferencias")
                                if k in args
                            },
                            drop_category=True,
                            reason="api_like_vacia",
                        )
                        if result_product_count(sem) > 0:
                            result = sem

            elif name == "catalogo_categoria":
                args = dict(args or {})
                slug = (args.get("slug") or "").strip("/")
                try:
                    result = await catalog.catalogo_categoria(client, args)
                except httpx.HTTPStatusError as err:
                    # Un slug que no existe devuelve 404. Para el cliente eso es lo
                    # mismo que "no tenemos eso": seguimos y le ofrecemos parecidos.
                    if err.response.status_code != 404:
                        raise
                    log.info("[tool] categoria %r no existe (404)", slug)
                    result = {"data": [], "total": 0}

                # La API manda: si tiene productos de la categoría, son ESOS.
                if result_product_count(result) == 0 and slug:
                    q = slug.replace("-", " ")
                    sem = await _semantic_fallback(
                        client, q, drop_category=True, reason="catalogo_categoria_vacia",
                    )
                    if result_product_count(sem) > 0:
                        # No son de la categoría pedida: van marcados como alternativas
                        # para que el bot no los presente como si lo fueran.
                        sem["aproximado"] = True
                        sem["categoria_pedida"] = slug
                        result = sem

            elif name in _CATALOG_TOOLS:
                result = await _CATALOG_TOOLS[name](client, args or {})

            elif name == "buscar_semantico":
                args = dict(args or {})
                if _is_free_campaign_search(args):
                    result = {
                        "error": (
                            "Búsqueda semántica libre bloqueada para campaña de temporada. "
                            "Usa explorar_catalogo y luego catalogo_categoria con el slug "
                            "temporal correspondiente, por ejemplo dia-del-padre. Si luego "
                            "usas buscar_semantico, debe llevar categoria_slug."
                        ),
                        "tool": name,
                        "blocked": True,
                    }
                else:
                    q = args.get("q") or ""
                    # "ositos panda" no debe quedar preso en peluches (Familia Panditas
                    # vive en terrarios/plantas).
                    if args.get("categoria_slug") == "peluches" and _wants_broad_theme(q):
                        log.info(
                            "[tool] buscar_semantico: quitando categoria_slug=peluches "
                            "por tema amplio %r",
                            q[:60],
                        )
                        args.pop("categoria_slug", None)
                    elif not args.get("categoria_slug"):
                        inferred = _infer_categoria_slug(args)
                        if inferred == "peluches" and _wants_broad_theme(q):
                            inferred = None
                        if inferred:
                            args["categoria_slug"] = inferred
                            log.info(
                                "[tool] buscar_semantico: inyectado categoria_slug=%s",
                                inferred,
                            )

                    slug = args.get("categoria_slug")
                    result = await search.buscar_semantico(client, args)

                    if slug:
                        # La categoría es un límite duro. Antes, si venían pocos
                        # resultados, se soltaba el filtro y se rellenaba con
                        # cualquier cosa: así llegó un arreglo floral a una lista
                        # de desayunos.
                        result = enforce_category(result, slug)

                        if result_product_count(result) < _MIN_RESULTS_OK:
                            # La API es la fuente de verdad de la categoría.
                            api = await catalog.catalogo_categoria(client, {"slug": slug})
                            api = enforce_category(api, slug)
                            if result_product_count(api) > result_product_count(result):
                                log.info("[tool] categoria %s resuelta por la API", slug)
                                result = api

                        if result_product_count(result) == 0:
                            # Ni semántica ni API: recién ahora ofrecemos parecidos,
                            # y van MARCADOS como alternativas.
                            similares = await _semantic_fallback(
                                client,
                                str(q),
                                base_args={
                                    k: args[k] for k in ("excluir_ids", "precio_max", "preferencias")
                                    if k in args
                                },
                                drop_category=True,
                                reason="sin_stock_en_la_categoria",
                            )
                            if result_product_count(similares) > 0:
                                similares["aproximado"] = True
                                similares["categoria_pedida"] = slug
                                result = similares

            elif name == "productos_similares":
                result = await search.productos_similares(client, args or {})
            elif name == "buscar_conocimiento_equipo":
                result = await search.buscar_conocimiento(args or {})
            else:
                result = {"error": f"Herramienta desconocida: {name}"}
        return json.dumps(result, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.error("Tool %s HTTP %s: %s", name, e.response.status_code, e)
        return json.dumps({"error": f"HTTP {e.response.status_code}", "tool": name})
    except Exception as e:
        log.error("Tool %s error: %s", name, e)
        return json.dumps({"error": str(e), "tool": name})
