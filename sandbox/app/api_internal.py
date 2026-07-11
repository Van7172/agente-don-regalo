"""Endpoints internos agent ↔ CRM (outbox del asesor)."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.crm import http_client as crm_http
from app.services.messenger import send_message

router = APIRouter(prefix="/internal", tags=["internal"])


class OutboxSendBody(BaseModel):
    outbox_id: int | None = None
    wa_id: str
    content: str
    conversation_id: int | None = None


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
        wa_mid = await send_message(body.wa_id, body.content)
        if body.outbox_id and crm_http.crm_enabled():
            await crm_http.mark_outbox(body.outbox_id, "sent")
        if body.conversation_id and crm_http.crm_enabled():
            await crm_http.append_outbound(
                body.conversation_id,
                body.content,
                sender_type="agent",
                role="human",
                wa_message_id=wa_mid,
            )
            await crm_http.set_mode(body.conversation_id, "HUMAN")
        return {"status": "ok", "wa_message_id": wa_mid}
    except Exception as err:
        if body.outbox_id and crm_http.crm_enabled():
            await crm_http.mark_outbox(body.outbox_id, "failed", str(err))
        raise HTTPException(502, f"send failed: {err}") from err
