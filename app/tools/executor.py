"""
Punto de entrada único para ejecutar cualquier herramienta del agente.
"""
import json
import logging
import unicodedata

import httpx

from app.tools import catalog, search

log = logging.getLogger(__name__)

_CATALOG_TOOLS = {
    "listar_categorias":   catalog.listar_categorias,
    "listar_ocasiones":    catalog.listar_ocasiones,
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
                result = await catalog.catalogo_categoria(client, args)
                if result_product_count(result) == 0:
                    slug = (args.get("slug") or "").strip("/")
                    q = slug.replace("-", " ") or slug
                    if q:
                        sem = await _semantic_fallback(
                            client,
                            q,
                            drop_category=True,
                            reason="catalogo_categoria_vacia",
                        )
                        if result_product_count(sem) > 0:
                            result = sem

            elif name in _CATALOG_TOOLS:
                result = await _CATALOG_TOOLS[name](client, args or {})

            elif name == "buscar_semantico":
                args = dict(args or {})
                if _is_free_campaign_search(args):
                    result = {
                        "error": (
                            "Búsqueda semántica libre bloqueada para campaña de temporada. "
                            "Usa listar_categorias y luego catalogo_categoria con el slug "
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
                    result = await search.buscar_semantico(client, args)
                    # Filtro de categoría demasiado estrecho → ampliar en Qdrant.
                    if (
                        result_product_count(result) < _MIN_RESULTS_OK
                        and args.get("categoria_slug")
                        and not _is_free_campaign_search({**args, "categoria_slug": None})
                    ):
                        broad = await _semantic_fallback(
                            client,
                            str(q),
                            base_args=args,
                            drop_category=True,
                            reason="categoria_sin_hits",
                        )
                        if result_product_count(broad) > result_product_count(result):
                            result = broad

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
