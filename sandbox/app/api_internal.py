"""Endpoints internos agent ↔ CRM (outbox del asesor)."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.crm import http_client as crm_http
from app.services.messenger import send_media, send_message

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
        media_key = body.media_path

        if media_key and body.type in ("image", "audio", "document"):
            # El CRM guarda el archivo; aquí se recupera, se convierte si hace
            # falta (nota de voz webm → ogg) y se sube a Meta.
            data, mime = await crm_http.fetch_media(media_key)
            wa_mid = await send_media(
                body.wa_id,
                body.type,
                data,
                mime,
                filename=body.filename,
                caption=body.content,
            )
            # CRM exige content no vacío al persistir; placeholders los oculta el inbox.
            stored_content = (body.content or "").strip()
            if not stored_content:
                if body.type == "document":
                    stored_content = body.filename or "[document]"
                elif body.type == "image":
                    stored_content = "[image]"
                elif body.type == "audio":
                    stored_content = "[audio]"
                else:
                    stored_content = "[media]"
        else:
            if not body.content.strip():
                raise ValueError("mensaje vacío")
            wa_mid = await send_message(body.wa_id, body.content)
            stored_content = body.content
            media_key = None

        if body.outbox_id and crm_http.crm_enabled():
            await crm_http.mark_outbox(body.outbox_id, "sent")
        if body.conversation_id and crm_http.crm_enabled():
            await crm_http.append_outbound(
                body.conversation_id,
                stored_content,
                sender_type="agent",
                role="human",
                wa_message_id=wa_mid,
                media_url=media_key,
            )
            await crm_http.set_mode(body.conversation_id, "HUMAN")
        return {"status": "ok", "wa_message_id": wa_mid}
    except Exception as err:
        if body.outbox_id and crm_http.crm_enabled():
            await crm_http.mark_outbox(body.outbox_id, "failed", str(err))
        raise HTTPException(502, f"send failed: {err}") from err
