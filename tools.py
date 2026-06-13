"""
Herramientas (function calling) del agente Regalito.

Cada herramienta mapea a un endpoint de la API REST de donregalo.pe.
TOOLS  → esquema en formato OpenAI tools (type: function)
execute_tool(name, args) → ejecuta la llamada HTTP y devuelve el JSON crudo
"""
import os
import re
import json
import logging
import unicodedata
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://donregalo.pe/clienteApiApp/api"

# Productos por defecto a traer en listados
DEFAULT_PER_PAGE = 6

# ─── Qdrant (búsqueda semántica) ──────────────────────────────────────────────
QDRANT_URL        = os.getenv("QDRANT_URL", "").rstrip("/")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY", "")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "productos")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL       = os.getenv("EMBED_MODEL", "text-embedding-3-small")
SEMANTIC_LIMIT    = int(os.getenv("SEMANTIC_LIMIT", "6"))

# Base de conocimiento aprendida de los vendedores (ver knowledge.py)
KB_COLLECTION = os.getenv("KB_COLLECTION", "respuestas_equipo")
KB_LIMIT      = int(os.getenv("KB_LIMIT", "3"))
KB_MIN_SCORE  = float(os.getenv("KB_MIN_SCORE", "0.5"))

_qdrant_client = None


def _get_qdrant():
    """Crea (una vez) el cliente Qdrant vía REST. Devuelve None si no está configurado."""
    global _qdrant_client
    if not QDRANT_URL:
        return None
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        parsed = urlparse(QDRANT_URL)
        _qdrant_client = QdrantClient(
            host=parsed.hostname,
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            https=(parsed.scheme == "https"),
            api_key=QDRANT_API_KEY or None,
            prefer_grpc=False,
            timeout=20,
            check_compatibility=False,
        )
    return _qdrant_client


async def _embed(texts: list[str]) -> list[list[float]]:
    """Embebe uno o varios textos con OpenAI (una sola llamada)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={"model": EMBED_MODEL, "input": texts},
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


async def _embed_query(text: str) -> list[float]:
    """Embebe un solo texto."""
    return (await _embed([text]))[0]


def _blend(v1: list[float], w1: float, v2: list[float], w2: float) -> list[float]:
    """Combina dos vectores con pesos (mezcla consulta + preferencias del cliente).
    Para distancia coseno la magnitud no afecta, así que no normalizamos."""
    return [a * w1 + b * w2 for a, b in zip(v1, v2)]


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
            "name": "buscar_semantico",
            "description": (
                "BÚSQUEDA PRINCIPAL de productos. Úsala SIEMPRE que el cliente describa "
                "lo que busca con palabras (intención, estilo, sentimiento, ocasión, "
                "tipo de producto), ej: 'algo romántico para mi novia', 'un detalle "
                "para felicitar a mi jefe', 'rosas blancas elegantes', 'desayuno "
                "sorpresa'. Entiende el significado, no solo palabras exactas, así que "
                "evita confundir 'rosas blancas' de cumpleaños con arreglos fúnebres. "
                "Por defecto NO devuelve productos fúnebres (pon incluir_funebre=true "
                "solo si el cliente pide explícitamente un arreglo de condolencia/fúnebre)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Lo que el cliente busca, descrito de la forma más rica posible (incluye estilo, ocasión y características que mencionó).",
                    },
                    "id_ocasion": {
                        "type": "integer",
                        "description": "Opcional. Filtra por ocasión si el cliente la indicó: Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7.",
                    },
                    "precio_max": {
                        "type": "number",
                        "description": "Opcional. Precio máximo en USD si el cliente dio un presupuesto.",
                    },
                    "incluir_funebre": {
                        "type": "boolean",
                        "description": "Por defecto false. Ponlo en true SOLO si el cliente pide explícitamente un arreglo fúnebre o de condolencias.",
                    },
                    "preferencias": {
                        "type": "string",
                        "description": "Opcional. Gustos DURABLES del cliente que conoces de su historial (DATOS CONOCIDOS), ej: 'le gustan los girasoles y los colores pastel, prefiere detalles con chocolate'. Se usan para personalizar el ranking sin sobreescribir lo que pide ahora. No inventes: solo lo que realmente sabes del cliente.",
                    },
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "productos_similares",
            "description": (
                "Devuelve productos PARECIDOS a uno que el cliente ya vio y le gustó. "
                "Úsala cuando el cliente diga cosas como 'muéstrame algo similar', "
                "'¿tienes otros parecidos?', 'algo así pero diferente', o cuando quieras "
                "ofrecer alternativas a un producto que acaba de ver. Pasa el id_producto "
                "de ese producto. Por defecto NO incluye fúnebres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id_producto": {
                        "type": "integer",
                        "description": "id_producto del producto de referencia (el que le gustó al cliente).",
                    },
                    "incluir_funebre": {
                        "type": "boolean",
                        "description": "Por defecto false. true solo en contexto fúnebre explícito.",
                    },
                },
                "required": ["id_producto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_productos",
            "description": (
                "Búsqueda por coincidencia de texto exacta (nombre o característica). "
                "Úsala como respaldo cuando buscar_semantico no encuentre lo que el "
                "cliente menciona, o cuando el cliente dé un nombre/término muy puntual. "
                "Si conoces la ocasión, pasa `id_ocasion`. "
                "Por defecto NO devuelve arreglos fúnebres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Término de búsqueda del producto que el cliente mencionó (ej: rosas, peluche, desayuno)",
                    },
                    "id_ocasion": {
                        "type": "integer",
                        "description": "Opcional. Filtra por ocasión: Cumpleaños=1, Aniversario=2, Felicitación=3, Nacimiento=4, Agradecimiento=5, Negocios=6, Otros=7. Úsalo si el cliente indicó la ocasión.",
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
    {
        "type": "function",
        "function": {
            "name": "buscar_conocimiento_equipo",
            "description": (
                "Consulta la base de conocimiento aprendida de los vendedores humanos. "
                "Úsala cuando el cliente haga una pregunta que NO se resuelve con las otras "
                "herramientas: dudas de políticas, casos especiales, objeciones (precio, "
                "tiempos, desconfianza), coordinaciones o situaciones poco comunes. "
                "Si encuentra una respuesta del equipo con buen puntaje, úsala como guía "
                "para responder con el tono y los datos que ya funcionaron. Si no devuelve "
                "nada útil, responde con tu criterio o deriva al equipo humano."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "La duda o situación del cliente, redactada de forma clara.",
                    },
                },
                "required": ["q"],
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
    if name == "buscar_semantico":
        return await _buscar_semantico(client, args)

    if name == "productos_similares":
        return await _productos_similares(args)

    if name == "buscar_conocimiento_equipo":
        return await _buscar_conocimiento(args)

    if name == "listar_categorias":
        return await _get(client, f"{API_BASE}/categorias")

    if name == "listar_ocasiones":
        return await _get(client, f"{API_BASE}/ocasiones")

    if name == "buscar_productos":
        params = {"q": args.get("q", ""), "per_page": DEFAULT_PER_PAGE}
        if args.get("orden") in ("asc", "desc"):
            params["orden"] = args["orden"]
        if args.get("id_ocasion"):
            params["ocasion"] = int(args["id_ocasion"])
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


# ─── Búsqueda semántica (Qdrant) ──────────────────────────────────────────────

# Peso de la consulta actual vs. las preferencias del cliente al mezclar vectores.
PREF_QUERY_WEIGHT = 0.75
PREF_HISTORY_WEIGHT = 0.25

# Búsqueda híbrida: se trae un pool mayor y se re-rankea sumando un bonus por
# coincidencia léxica. Esto rescata restricciones duras (color, material, tamaño)
# que la similitud semántica por sí sola ignora (ej: "rosas blancas" vs "rojas").
HYBRID_POOL        = max(SEMANTIC_LIMIT * 4, 24)
HYBRID_TERM_BONUS  = 0.12   # bonus por cada término diferenciador que aparece

# Palabras demasiado genéricas para diferenciar productos (no aportan al bonus).
_STOPWORDS = {
    "para", "una", "unas", "unos", "con", "los", "las", "del", "que", "por",
    "mas", "muy", "algo", "quiero", "quisiera", "busco", "mejor", "entonces",
    "arreglo", "arreglos", "floral", "florales", "regalo", "regalos", "tienes",
    "tienen", "quisieras", "necesito", "ramo", "ramos", "bonito", "bonita",
}


def _norm(s: str) -> str:
    """Minúsculas sin acentos, para comparación robusta."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def _keyword_terms(q: str) -> set[str]:
    """Términos diferenciadores de la consulta (sin stopwords ni palabras cortas)."""
    return {
        t for t in re.findall(r"[a-z0-9]+", _norm(q))
        if len(t) >= 4 and t not in _STOPWORDS
    }


def _keyword_bonus(terms: set[str], producto: dict) -> float:
    """Bonus léxico: cuántos términos de la consulta aparecen en el producto."""
    if not terms:
        return 0.0
    texto = _norm(f"{producto.get('nombre','')} {producto.get('descripcion_corta','')}")
    aciertos = sum(1 for t in terms if t in texto)
    return HYBRID_TERM_BONUS * aciertos


def _build_filter(args: dict):
    """Filtros de payload comunes: excluir fúnebre por defecto + ocasión + precio."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, Range

    must: list = []
    if not args.get("incluir_funebre"):
        must.append(FieldCondition(key="es_funebre", match=MatchValue(value=False)))
    if args.get("id_ocasion"):
        must.append(FieldCondition(
            key="ocasiones_ids",
            match=MatchAny(any=[int(args["id_ocasion"])]),
        ))
    if args.get("precio_max"):
        must.append(FieldCondition(key="precio", range=Range(lte=float(args["precio_max"]))))
    return Filter(must=must) if must else None


def _hit_to_producto(h) -> dict:
    p = h.payload or {}
    return {
        "id_producto":       p.get("id_producto"),
        "nombre":            p.get("nombre"),
        "precio":            p.get("precio"),
        "categoria":         p.get("categoria"),
        "descripcion_corta": p.get("descripcion_corta"),
        "imagen_url":        p.get("imagen_url"),
        "url":               p.get("url"),
        "score":             round(h.score, 4),
    }


async def _buscar_semantico(client: httpx.AsyncClient, args: dict):
    """Busca productos por significado en Qdrant, opcionalmente personalizado con
    las preferencias durables del cliente. Si Qdrant no está disponible, cae de
    vuelta a la búsqueda por texto (buscar_productos)."""
    import asyncio

    q = (args.get("q") or "").strip()
    if not q:
        return {"error": "Falta el término de búsqueda 'q'."}

    qc = _get_qdrant()
    if qc is None:
        # Fallback: búsqueda por texto
        log.warning("Qdrant no configurado; usando búsqueda por texto.")
        params = {"q": q, "per_page": DEFAULT_PER_PAGE}
        if args.get("id_ocasion"):
            params["ocasion"] = int(args["id_ocasion"])
        return await _get(client, f"{API_BASE}/productos/buscar", params)

    # 1) Embeber la consulta (y las preferencias del cliente, si las hay)
    prefs = (args.get("preferencias") or "").strip()
    personalizado = False
    if prefs:
        q_vec, pref_vec = await _embed([q, prefs])
        vector = _blend(q_vec, PREF_QUERY_WEIGHT, pref_vec, PREF_HISTORY_WEIGHT)
        personalizado = True
    else:
        vector = await _embed_query(q)

    # 2) Filtros de payload
    qfilter = _build_filter(args)

    # 3) Consultar Qdrant: traemos un pool mayor para re-rankear (cliente sync → hilo)
    def _search():
        return qc.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            query_filter=qfilter,
            limit=HYBRID_POOL,
            with_payload=True,
        ).points

    hits = await asyncio.to_thread(_search)

    # 4) Re-ranking híbrido: score semántico + bonus léxico por términos duros
    terms = _keyword_terms(q)
    candidatos = []
    for h in hits:
        p = _hit_to_producto(h)
        p["_rank"] = h.score + _keyword_bonus(terms, p)
        candidatos.append(p)
    candidatos.sort(key=lambda p: p["_rank"], reverse=True)

    productos = []
    for p in candidatos[:SEMANTIC_LIMIT]:
        p.pop("_rank", None)
        productos.append(p)

    return {
        "data": productos,
        "total": len(productos),
        "fuente": "semantico",
        "personalizado": personalizado,
    }


async def _productos_similares(args: dict):
    """Devuelve productos parecidos a uno de referencia, usando su propio vector
    en Qdrant como consulta (recomendación por vecindad)."""
    import asyncio

    pid = int(args["id_producto"])

    qc = _get_qdrant()
    if qc is None:
        return {"error": "La búsqueda de similares no está disponible.", "tool": "productos_similares"}

    qfilter = _build_filter(args)

    def _query():
        # Recupera el vector del producto de referencia…
        recs = qc.retrieve(
            collection_name=QDRANT_COLLECTION,
            ids=[pid],
            with_vectors=True,
        )
        if not recs:
            return None
        ref_vec = recs[0].vector
        # …y busca los más cercanos (pedimos uno extra para descartar el propio).
        return qc.query_points(
            collection_name=QDRANT_COLLECTION,
            query=ref_vec,
            query_filter=qfilter,
            limit=SEMANTIC_LIMIT + 1,
            with_payload=True,
        ).points

    hits = await asyncio.to_thread(_query)
    if hits is None:
        return {"error": f"No se encontró el producto {pid} en el índice.", "tool": "productos_similares"}

    # Excluir el producto de referencia del resultado
    productos = [_hit_to_producto(h) for h in hits if h.id != pid][:SEMANTIC_LIMIT]
    return {"data": productos, "total": len(productos), "fuente": "similares", "referencia": pid}


# ─── Conocimiento del equipo (Qdrant) ─────────────────────────────────────────

async def _buscar_conocimiento(args: dict):
    """Busca en la base de conocimiento aprendida de los vendedores. Devuelve solo
    respuestas con puntaje sobre el umbral, para no contaminar con ruido."""
    import asyncio

    q = (args.get("q") or "").strip()
    if not q:
        return {"error": "Falta la consulta 'q'."}

    qc = _get_qdrant()
    if qc is None:
        return {"data": [], "total": 0, "fuente": "conocimiento_equipo"}

    vector = await _embed_query(q)

    def _search():
        try:
            return qc.query_points(
                collection_name=KB_COLLECTION,
                query=vector,
                limit=KB_LIMIT,
                with_payload=True,
            ).points
        except Exception as e:
            # La colección puede no existir todavía (aún no se ha capturado nada)
            log.info("[KB] consulta sin resultados (%s)", e)
            return []

    hits = await asyncio.to_thread(_search)

    resultados = []
    for h in hits:
        if h.score < KB_MIN_SCORE:
            continue
        p = h.payload or {}
        resultados.append({
            "pregunta":  p.get("pregunta"),
            "respuesta": p.get("respuesta"),
            "categoria": p.get("categoria"),
            "score":     round(h.score, 4),
        })

    return {"data": resultados, "total": len(resultados), "fuente": "conocimiento_equipo"}


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None):
    r = await client.get(url, params=params)
    r.raise_for_status()
    return r.json()
