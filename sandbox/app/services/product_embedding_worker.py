"""Worker MySQL-outbox → OpenAI embeddings → Qdrant."""
from __future__ import annotations

import asyncio
import base64
import logging
import struct
from typing import Optional

import httpx

from app.config import settings
from app.crm import http_client as crm_http
from app.observability import record_operation
from app.services.product_embedding_index import (
    build_embedding_text,
    build_payload,
    content_hash,
    needs_embedding,
)
from app.tools.search import embed, get_qdrant

log = logging.getLogger(__name__)
_task: Optional[asyncio.Task] = None


async def _catalog_product(product_id: int) -> dict | None:
    url = f"{settings.donregalo_api_base.rstrip('/')}/productos/{product_id}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    body = response.json()
    data = body.get("data")
    return data if isinstance(data, dict) else None


def _vector_bytes(vector: list[float]) -> str:
    raw = struct.pack(f"<{len(vector)}f", *vector)
    return base64.b64encode(raw).decode("ascii")


async def process_embedding_job(job: dict) -> None:
    job_id = int(job["id_job"])
    product_id = int(job["id_producto"])
    qdrant = get_qdrant()
    if qdrant is None:
        raise RuntimeError("Qdrant no configurado")

    product = (
        None
        if job.get("reason") == "product_unpublished"
        else await _catalog_product(product_id)
    )
    if product is None:
        from qdrant_client.models import PointIdsList

        await asyncio.to_thread(
            qdrant.delete,
            collection_name=settings.qdrant_collection,
            points_selector=PointIdsList(points=[product_id]),
        )
        await crm_http.finish_embedding_job(job_id, status="deleted")
        record_operation("embedding.worker", "deleted")
        return

    text = build_embedding_text(product)
    semantic_hash = content_hash(text)

    def _retrieve():
        records = qdrant.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[product_id],
            with_payload=True,
            with_vectors=True,
        )
        return records[0] if records else None

    existing = await asyncio.to_thread(_retrieve)
    existing_payload = dict(existing.payload or {}) if existing else None
    payload = build_payload(
        product,
        semantic_hash,
        model=settings.embed_model,
        dimensions=settings.embed_dim,
    )

    vector: list[float]
    if needs_embedding(
        existing_payload,
        semantic_hash,
        model=settings.embed_model,
        dimensions=settings.embed_dim,
    ):
        vector = (await embed([text]))[0]

        def _upsert():
            from qdrant_client.models import PointStruct

            qdrant.upsert(
                collection_name=settings.qdrant_collection,
                points=[
                    PointStruct(id=product_id, vector=vector, payload=payload)
                ],
            )

        await asyncio.to_thread(_upsert)
        record_operation("embedding.worker", "embedded")
    else:
        raw_vector = existing.vector if existing else None
        if not isinstance(raw_vector, list):
            raise RuntimeError("Punto Qdrant sin vector denso")
        vector = [float(value) for value in raw_vector]
        if existing_payload.get("payload_hash") != payload["payload_hash"]:
            await asyncio.to_thread(
                qdrant.overwrite_payload,
                collection_name=settings.qdrant_collection,
                payload=payload,
                points=[product_id],
            )
            record_operation("embedding.worker", "payload")
        else:
            record_operation("embedding.worker", "unchanged")

    await crm_http.finish_embedding_job(
        job_id,
        status="done",
        content_hash=semantic_hash,
        embedding_model=settings.embed_model,
        dimensions=len(vector),
        document_version=2,
        embedding_base64=_vector_bytes(vector),
    )


async def drain_embedding_jobs() -> int:
    jobs = await crm_http.claim_embedding_jobs(settings.embedding_worker_batch)
    processed = 0
    for job in jobs:
        job_id = int(job["id_job"])
        try:
            await process_embedding_job(job)
            processed += 1
        except Exception as error:
            log.warning(
                "[EMBEDDING-WORKER] job=%s producto=%s error=%s",
                job_id,
                job.get("id_producto"),
                type(error).__name__,
            )
            record_operation("embedding.worker", "error")
            try:
                await crm_http.finish_embedding_job(
                    job_id,
                    status="retry",
                    error=str(error)[:500],
                )
            except Exception:
                log.exception("[EMBEDDING-WORKER] no se pudo reencolar job=%s", job_id)
    return processed


async def _loop() -> None:
    log.info(
        "[EMBEDDING-WORKER] iniciado tick=%ss batch=%s",
        settings.embedding_worker_tick_seconds,
        settings.embedding_worker_batch,
    )
    while True:
        try:
            count = await drain_embedding_jobs()
            if count:
                log.info("[EMBEDDING-WORKER] procesados=%s", count)
        except Exception as error:
            log.warning("[EMBEDDING-WORKER] tick error=%s", type(error).__name__)
        await asyncio.sleep(settings.embedding_worker_tick_seconds)


def start_embedding_worker() -> None:
    global _task
    if not settings.embedding_worker_enabled:
        log.info("[EMBEDDING-WORKER] desactivado")
        return
    if settings.crm_mode != "external" or not crm_http.crm_enabled():
        log.warning("[EMBEDDING-WORKER] requiere CRM_MODE=external")
        return
    if not settings.openai_api_key or not settings.qdrant_url:
        log.error("[EMBEDDING-WORKER] faltan OpenAI/Qdrant")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())


def stop_embedding_worker() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
