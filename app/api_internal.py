"""Endpoints internos agent ↔ CRM (outbox del asesor)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.crm import http_client as crm_http
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
