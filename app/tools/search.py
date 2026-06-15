"""
Herramientas de búsqueda semántica (Qdrant) y conocimiento del equipo.
También expone las utilidades de embedding usadas por services/knowledge.py.
"""
import re
import asyncio
import logging
import unicodedata
from urllib.parse import urlparse

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# Peso de la consulta actual vs. preferencias del cliente al mezclar vectores.
_PREF_QUERY_WEIGHT   = 0.75
_PREF_HISTORY_WEIGHT = 0.25

# Búsqueda híbrida: pool más amplio + re-ranking léxico.
_HYBRID_POOL       = max(settings.semantic_limit * 4, 24)
_HYBRID_TERM_BONUS = 0.12

_STOPWORDS = {
    "para", "una", "unas", "unos", "con", "los", "las", "del", "que", "por",
    "mas", "muy", "algo", "quiero", "quisiera", "busco", "mejor", "entonces",
    "arreglo", "arreglos", "floral", "florales", "regalo", "regalos", "tienes",
    "tienen", "quisieras", "necesito", "ramo", "ramos", "bonito", "bonita",
}

_qdrant_client = None


# ─── Cliente Qdrant (singleton) ───────────────────────────────────────────────

def get_qdrant():
    """Retorna el cliente Qdrant (lazy singleton). None si no está configurado."""
    global _qdrant_client
    if not settings.qdrant_url:
        return None
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        parsed = urlparse(settings.qdrant_url)
        _qdrant_client = QdrantClient(
            host=parsed.hostname,
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            https=(parsed.scheme == "https"),
            api_key=settings.qdrant_api_key or None,
            prefer_grpc=False,
            timeout=20,
            check_compatibility=False,
        )
    return _qdrant_client


# ─── Embeddings (OpenAI) ──────────────────────────────────────────────────────

async def embed(texts: list[str]) -> list[list[float]]:
    """Embebe una lista de textos con OpenAI en una sola llamada."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": settings.embed_model, "input": texts},
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


async def embed_query(text: str) -> list[float]:
    return (await embed([text]))[0]


# ─── Helpers internos ─────────────────────────────────────────────────────────

def _blend(v1: list[float], w1: float, v2: list[float], w2: float) -> list[float]:
    return [a * w1 + b * w2 for a, b in zip(v1, v2)]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def _keyword_terms(q: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", _norm(q))
        if len(t) >= 4 and t not in _STOPWORDS
    }


def _keyword_bonus(terms: set[str], producto: dict) -> float:
    if not terms:
        return 0.0
    texto   = _norm(f"{producto.get('nombre','')} {producto.get('descripcion_corta','')}")
    aciertos = sum(1 for t in terms if t in texto)
    return _HYBRID_TERM_BONUS * aciertos


def _build_filter(args: dict):
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
    if args.get("categoria_slug"):
        must.append(FieldCondition(
            key="categoria_slug",
            match=MatchValue(value=str(args["categoria_slug"])),
        ))
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


# ─── Herramientas ─────────────────────────────────────────────────────────────

async def buscar_semantico(client: httpx.AsyncClient, args: dict):
    """Búsqueda semántica en Qdrant con re-ranking híbrido léxico."""
    from app.tools.catalog import get as http_get

    q = (args.get("q") or "").strip()
    if not q:
        return {"error": "Falta el término de búsqueda 'q'."}

    qc = get_qdrant()
    if qc is None:
        log.warning("Qdrant no configurado; usando búsqueda por texto.")
        params: dict = {"q": q, "per_page": settings.semantic_limit}
        if args.get("id_ocasion"):
            params["ocasion"] = int(args["id_ocasion"])
        return await http_get(client, f"{settings.donregalo_api_base}/productos/buscar", params)

    prefs = (args.get("preferencias") or "").strip()
    personalizado = False
    if prefs:
        q_vec, pref_vec = await embed([q, prefs])
        vector = _blend(q_vec, _PREF_QUERY_WEIGHT, pref_vec, _PREF_HISTORY_WEIGHT)
        personalizado = True
    else:
        vector = await embed_query(q)

    qfilter = _build_filter(args)

    def _search():
        return qc.query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            query_filter=qfilter,
            limit=_HYBRID_POOL,
            with_payload=True,
        ).points

    hits = await asyncio.to_thread(_search)

    terms = _keyword_terms(q)
    candidatos = []
    for h in hits:
        p = _hit_to_producto(h)
        p["_rank"] = h.score + _keyword_bonus(terms, p)
        candidatos.append(p)
    candidatos.sort(key=lambda p: p["_rank"], reverse=True)

    productos = []
    for p in candidatos[:settings.semantic_limit]:
        p.pop("_rank", None)
        productos.append(p)

    return {
        "data": productos,
        "total": len(productos),
        "fuente": "semantico",
        "personalizado": personalizado,
    }


async def productos_similares(args: dict):
    """Vecinos más cercanos al vector de un producto de referencia."""
    pid = int(args["id_producto"])
    qc  = get_qdrant()
    if qc is None:
        return {"error": "La búsqueda de similares no está disponible.", "tool": "productos_similares"}

    qfilter = _build_filter(args)

    def _query():
        recs = qc.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[pid],
            with_vectors=True,
        )
        if not recs:
            return None
        ref_vec = recs[0].vector
        return qc.query_points(
            collection_name=settings.qdrant_collection,
            query=ref_vec,
            query_filter=qfilter,
            limit=settings.semantic_limit + 1,
            with_payload=True,
        ).points

    hits = await asyncio.to_thread(_query)
    if hits is None:
        return {"error": f"No se encontró el producto {pid} en el índice.", "tool": "productos_similares"}

    productos = [_hit_to_producto(h) for h in hits if h.id != pid][:settings.semantic_limit]
    return {"data": productos, "total": len(productos), "fuente": "similares", "referencia": pid}


async def buscar_conocimiento(args: dict):
    """Busca en la KB aprendida de los vendedores con umbral mínimo de score."""
    q = (args.get("q") or "").strip()
    if not q:
        return {"error": "Falta la consulta 'q'."}

    qc = get_qdrant()
    if qc is None:
        return {"data": [], "total": 0, "fuente": "conocimiento_equipo"}

    vector = await embed_query(q)

    def _search():
        try:
            return qc.query_points(
                collection_name=settings.kb_collection,
                query=vector,
                limit=settings.kb_limit,
                with_payload=True,
            ).points
        except Exception as e:
            log.info("[KB] consulta sin resultados (%s)", e)
            return []

    hits = await asyncio.to_thread(_search)

    resultados = []
    for h in hits:
        if h.score < settings.kb_min_score:
            continue
        p = h.payload or {}
        resultados.append({
            "pregunta":  p.get("pregunta"),
            "respuesta": p.get("respuesta"),
            "categoria": p.get("categoria"),
            "score":     round(h.score, 4),
        })

    return {"data": resultados, "total": len(resultados), "fuente": "conocimiento_equipo"}
