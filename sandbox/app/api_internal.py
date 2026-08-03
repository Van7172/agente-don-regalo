"""Endpoints internos agent ↔ CRM (outbox del asesor)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.crm import http_client as crm_http
from app.observability import metrics_snapshot
from app.resilience import circuit_breakers_snapshot
from app.services.inbound_queue import inbound_queue_operational_stats
from app.services.outbox_drain import deliver_outbox

log = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


class OutboxSendBody(BaseModel):
    outbox_id: int | None = None
    wa_id: str
    content: str = ""
    conversation_id: int | None = None
    # Adjunto del asesor: 'image' | 'audio' | 'document' (o 'text' sin adjunto).
    type: str = "text"
    media_path: str | None = None
    filename: str = ""
    # El asesor respondió a un mensaje desde el inbox del CRM.
    reply_to_wa_id: str | None = None
    quoted_text: str | None = None
    # La FOTO del mensaje citado: su texto puede ser solo "[image]".
    quoted_media_url: str | None = None


def _check_token(token: str | None) -> None:
    expected = settings.agent_internal_token
    if expected and token != expected:
        raise HTTPException(401, "Unauthorized")


@router.get("/operations")
async def operations(
    x_agent_token: str | None = Header(default=None),
):
    """Snapshot JSON para el dashboard del CRM; nunca incluye mensajes ni PII."""
    _check_token(x_agent_token)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue": await inbound_queue_operational_stats(),
        "operations": metrics_snapshot(),
        "circuits": circuit_breakers_snapshot(),
        "competition": {
            "enabled": settings.competition_crawl_enabled,
            "crm_enabled": crm_http.crm_enabled(),
            "interval_seconds": settings.competition_crawl_interval_seconds,
        },
    }


@router.post("/competition/crawl")
async def competition_crawl(
    x_agent_token: str | None = Header(default=None),
):
    """Dispara un crawl de competencia ya (ignora el cooldown del watchdog)."""
    _check_token(x_agent_token)
    from app.services import competition_crawl as crawl

    summary = await crawl.run_crawl(force=True)
    log.info("[competencia] crawl manual: %s", summary)
    return {"ok": True, "summary": summary}


@router.post("/outbox/send")
async def outbox_send(
    body: OutboxSendBody,
    x_agent_token: str | None = Header(default=None),
):
    _check_token(x_agent_token)
    try:
        return await deliver_outbox(
            wa_id=body.wa_id,
            content=body.content,
            conversation_id=body.conversation_id,
            outbox_id=body.outbox_id,
            msg_type=body.type,
            media_path=body.media_path,
            filename=body.filename,
            reply_to_wa_id=body.reply_to_wa_id,
            quoted_text=body.quoted_text,
            quoted_media_url=body.quoted_media_url,
        )
    except Exception as err:
        log.error("[OUTBOX] push falló: %s", err)
        if body.outbox_id and crm_http.crm_enabled():
            # Dejamos pending para el drenaje; solo marcamos failed si ya se intentó metada.
            # Si Meta falló, sí marcamos failed para no reintentar en bucle eterno.
            await crm_http.mark_outbox(body.outbox_id, "failed", str(err)[:500])
        raise HTTPException(502, f"send failed: {err}") from err
