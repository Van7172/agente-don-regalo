"""
Punto de entrada único para ejecutar cualquier herramienta del agente.
"""
import json
import logging

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


async def execute_tool(name: str, args: dict) -> str:
    """Ejecuta una herramienta y devuelve el resultado como string JSON."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if name in _CATALOG_TOOLS:
                result = await _CATALOG_TOOLS[name](client, args or {})
            elif name == "buscar_semantico":
                result = await search.buscar_semantico(client, args or {})
            elif name == "productos_similares":
                result = await search.productos_similares(args or {})
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
