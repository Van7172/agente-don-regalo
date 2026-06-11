"""
Herramientas (function calling) del agente Regalito.

Cada herramienta mapea a un endpoint de la API REST de donregalo.pe.
TOOLS  → esquema en formato OpenAI tools (type: function)
execute_tool(name, args) → ejecuta la llamada HTTP y devuelve el JSON crudo
"""
import json
import logging
import httpx

log = logging.getLogger(__name__)

API_BASE = "https://donregalo.pe/clienteApiApp/api"

# Productos por defecto a traer en listados
DEFAULT_PER_PAGE = 6


# ─── Esquema de herramientas (OpenAI function calling) ────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "listar_categorias",
            "description": "Lista todas las categorías y subcategorías de la tienda. Úsala cuando el cliente quiera ver qué productos hay disponibles o pida ver el catálogo.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_ocasiones",
            "description": "Lista todas las ocasiones disponibles: Cumpleaños, Aniversario, Nacimiento, etc. Úsala antes de buscar productos por ocasión.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_productos",
            "description": "Busca productos por nombre o descripción. Úsala cuando el cliente mencione un producto específico, un precio o una característica.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Término de búsqueda del producto que el cliente mencionó (ej: rosas, peluche, desayuno)",
                    },
                    "orden": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Orden por precio: asc (menor a mayor) o desc (mayor a menor). Por defecto asc.",
                    },
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "catalogo_categoria",
            "description": "Obtiene los productos de una categoría específica. Usa el slug (url_categoria) obtenido de listar_categorias. Ejemplos de slugs: arreglos-florales, desayunos, peluches, plantas, cestas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "El slug (url_categoria) de la categoría, ej: arreglos-florales, desayunos, peluches",
                    },
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_destacados",
            "description": "Obtiene los productos más populares y destacados. Úsala cuando el cliente no sepa qué elegir, pida recomendaciones o pregunte qué es lo más vendido.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_oferta",
            "description": "Obtiene productos con descuento o en oferta. Úsala cuando el cliente busque algo económico, pregunte por promociones, descuentos o las mejores ofertas.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detalle_producto",
            "description": "Obtiene el detalle completo de un producto: descripción, precio, imágenes y relacionados. Úsala cuando el cliente quiera saber más de un producto que ya apareció en una búsqueda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_producto": {
                        "type": "integer",
                        "description": "El id_producto numérico obtenido de buscar_productos, catalogo_categoria o productos_destacados",
                    },
                },
                "required": ["id_producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_por_ocasion",
            "description": "Obtiene productos sugeridos para una ocasión. IDs: Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id_ocasion": {
                        "type": "integer",
                        "description": "El id de la ocasión. Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7",
                    },
                },
                "required": ["id_ocasion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "distritos_cobertura",
            "description": "Lista los distritos de Lima con cobertura de delivery y tarifa de envío. Úsala cuando el cliente pregunte si llegan a su zona o cuánto cuesta el envío.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "metodos_pago",
            "description": "Lista los métodos de pago disponibles. Úsala cuando el cliente pregunte cómo puede pagar su pedido.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tipo_cambio",
            "description": "Obtiene el tipo de cambio actual USD→Soles. Úsala para convertir precios de productos (que vienen en USD) a Soles antes de mostrarlos.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rastrear_pedido",
            "description": "Rastrea el estado de un pedido. SIEMPRE pide al cliente su email y código de pedido antes de usar esta herramienta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email":  {"type": "string", "description": "email del cliente"},
                    "codigo": {"type": "string", "description": "código del pedido"},
                },
                "required": ["email", "codigo"],
            },
        },
    },
]


# ─── Ejecutor ─────────────────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict) -> str:
    """Ejecuta una herramienta y devuelve el resultado como string JSON."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            result = await _dispatch(client, name, args or {})
        return json.dumps(result, ensure_ascii=False)
    except httpx.HTTPStatusError as e:
        log.error("Tool %s HTTP %s: %s", name, e.response.status_code, e)
        return json.dumps({"error": f"HTTP {e.response.status_code}", "tool": name})
    except Exception as e:
        log.error("Tool %s error: %s", name, e)
        return json.dumps({"error": str(e), "tool": name})


async def _dispatch(client: httpx.AsyncClient, name: str, args: dict):
    if name == "listar_categorias":
        return await _get(client, f"{API_BASE}/categorias")

    if name == "listar_ocasiones":
        return await _get(client, f"{API_BASE}/ocasiones")

    if name == "buscar_productos":
        params = {"q": args.get("q", ""), "per_page": DEFAULT_PER_PAGE}
        if args.get("orden") in ("asc", "desc"):
            params["orden"] = args["orden"]
        return await _get(client, f"{API_BASE}/productos/buscar", params)

    if name == "catalogo_categoria":
        slug = args.get("slug", "").strip("/")
        return await _get(client, f"{API_BASE}/categorias/{slug}/productos",
                          {"per_page": DEFAULT_PER_PAGE})

    if name == "productos_destacados":
        return await _get(client, f"{API_BASE}/productos/destacados",
                          {"limit": DEFAULT_PER_PAGE})

    if name == "productos_oferta":
        return await _get(client, f"{API_BASE}/productos/ofertas",
                          {"per_page": DEFAULT_PER_PAGE})

    if name == "detalle_producto":
        return await _get(client, f"{API_BASE}/productos/{int(args['id_producto'])}")

    if name == "productos_por_ocasion":
        return await _get(client, f"{API_BASE}/ocasiones/{int(args['id_ocasion'])}/productos",
                          {"per_page": DEFAULT_PER_PAGE})

    if name == "distritos_cobertura":
        return await _get(client, f"{API_BASE}/distritos")

    if name == "metodos_pago":
        return await _get(client, f"{API_BASE}/metodos-pago")

    if name == "tipo_cambio":
        return await _get(client, f"{API_BASE}/configuracion/tipo-cambio")

    if name == "rastrear_pedido":
        r = await client.post(
            f"{API_BASE}/pedidos/rastrear",
            json={"email": args.get("email", ""), "codigo": args.get("codigo", "")},
        )
        r.raise_for_status()
        return r.json()

    return {"error": f"Herramienta desconocida: {name}"}


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None):
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()
