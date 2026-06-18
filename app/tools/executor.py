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


async def execute_tool(name: str, args: dict) -> str:
    """Ejecuta una herramienta y devuelve el resultado como string JSON."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if name in _CATALOG_TOOLS:
                result = await _CATALOG_TOOLS[name](client, args or {})
            elif name == "buscar_semantico":
                if _is_free_campaign_search(args or {}):
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
                    result = await search.buscar_semantico(client, args or {})
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
