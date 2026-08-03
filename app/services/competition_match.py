"""Matching de productos ajenos contra el catálogo propio (Qdrant).

Si el vecino más cercano queda bajo el umbral, es un hueco candidato. Sin
Qdrant o sin embedding, no se inventa el match: se deja sin score.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.tools import search as search_tool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchResult:
    match_id_producto: Optional[int]
    match_score: Optional[float]
    match_nombre: Optional[str]
    es_hueco: bool


async def match_product(nombre: str, *, threshold: float | None = None) -> MatchResult:
    """Vecino más cercano al nombre. `es_hueco` si no hay equivalente cercano."""
    texto = (nombre or "").strip()
    floor = settings.competition_match_threshold if threshold is None else threshold
    if not texto:
        return MatchResult(None, None, None, True)

    qc = search_tool.get_qdrant()
    if qc is None or not settings.openai_api_key:
        return MatchResult(None, None, None, False)

    try:
        vector = await search_tool.embed_query(texto)

        def _search():
            return qc.query_points(
                collection_name=settings.qdrant_collection,
                query=vector,
                limit=1,
                with_payload=True,
            ).points

        import asyncio

        from app.resilience import circuit_breaker

        hits = await circuit_breaker("qdrant").call(lambda: asyncio.to_thread(_search))
    except Exception as err:
        log.warning("[competencia] match falló para %r: %s", texto[:40], err)
        return MatchResult(None, None, None, False)

    if not hits:
        return MatchResult(None, None, None, True)

    hit = hits[0]
    payload = hit.payload or {}
    score = round(float(hit.score), 4)
    pid = payload.get("id_producto")
    try:
        pid_int = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid_int = None
    nombre_match = str(payload.get("nombre") or "")[:255] or None
    es_hueco = score < floor
    return MatchResult(pid_int, score, nombre_match, es_hueco)
