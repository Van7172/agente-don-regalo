"""
Webhook de Chatwoot: recibe eventos de mensajes y de cambios de estado.
Handler delgado — valida el payload y delega al servicio de buffer / conocimiento.
"""
import json
import asyncio
import logging

from fastapi import APIRouter, Request, HTTPException

from app.config import settings
from app.services import knowledge
from app.services.buffer import enqueue

log = logging.getLogger(__name__)

router = APIRouter()

# Conversaciones ya procesadas para captura de conocimiento (evita duplicados
# cuando Chatwoot dispara varios eventos por la misma resolución).
_kb_captured: set[int] = set()


async def _handle_conversation_event(payload: dict) -> dict:
    """Al resolverse una conversación, captura en segundo plano el conocimiento
    aportado por el vendedor humano (Nivel B)."""
    data = payload.get("data") or payload
    status = data.get("status") or payload.get("status")
    conversation_id = data.get("id") or payload.get("id")

    if status != "resolved":
        return {"status": "ignored", "reason": f"status={status!r}"}
    if not conversation_id:
        return {"status": "ignored", "reason": "sin conversation_id"}
    if conversation_id in _kb_captured:
        return {"status": "ignored", "reason": "ya capturada"}

    _kb_captured.add(conversation_id)
    asyncio.create_task(knowledge.capturar_de_conversacion(conversation_id))
    return {"status": "capturing", "conversation_id": conversation_id}


@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    event   = payload.get("event")

    if event in ("conversation_status_changed", "conversation_resolved", "conversation_updated"):
        return await _handle_conversation_event(payload)

    if event != "message_created":
        return {"status": "ignored", "reason": f"event {event!r} no manejado"}

    message = payload.get("data") or payload

    msg_type = message.get("message_type")
    if msg_type not in ("incoming", 0):
        return {"status": "ignored", "reason": f"not incoming (type={msg_type!r})"}

    sender_type = message.get("sender", {}).get("type", "")
    if sender_type in ("agent_bot", "agent"):
        return {"status": "ignored", "reason": "sent by agent"}

    conversation    = message.get("conversation", {})
    conversation_id = conversation.get("id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="No conversation_id in payload")

    labels = conversation.get("labels") or []

    # Si la conversación está escalada a un asesor humano, el bot NO interviene
    # (tiene prioridad sobre la etiqueta de activación). El equipo quita la
    # etiqueta cuando termina y el bot se reactiva solo.
    if settings.human_support_label in labels:
        log.info("[HUMAN] conversation=%s en soporte humano; bot no interviene (labels=%s)", conversation_id, labels)
        return {"status": "ignored", "reason": "soporte humano activo"}

    if settings.bot_active_label not in labels:
        log.info("[INACTIVE] conversation=%s labels=%s", conversation_id, labels)
        return {"status": "ignored", "reason": "bot not active"}

    content     = message.get("content") or ""
    attachments = message.get("attachments") or []
    source_id   = message.get("source_id", "")
    in_reply_to_id = (message.get("content_attributes") or {}).get("in_reply_to")

    sender_meta = conversation.get("meta", {}).get("sender", {})
    contact_id  = sender_meta.get("id") or message.get("sender", {}).get("id")

    wa_identifier = (
        sender_meta.get("identifier")
        or message.get("sender", {}).get("identifier")
        or ""
    )
    wa_number = wa_identifier.split("@")[0] if wa_identifier else ""

    log.info(
        "[IN] conversation=%s contact=%s content=%r attachments=%d in_reply_to=%s",
        conversation_id, contact_id, content, len(attachments), in_reply_to_id,
    )

    await enqueue(
        conversation_id=conversation_id,
        contact_id=contact_id,
        wa_number=wa_number,
        source_id=source_id,
        in_reply_to_id=in_reply_to_id,
        content=content,
        attachments=attachments,
    )
    return {"status": "buffered"}


@router.post("/debug-webhook")
async def debug_webhook(request: Request):
    """Endpoint de diagnóstico para inspeccionar el payload de Chatwoot."""
    payload = await request.json()
    message = payload.get("data") or payload
    log.info("[DEBUG] payload=%s", json.dumps(payload, default=str))
    return {
        "event":              payload.get("event"),
        "message_type":       message.get("message_type"),
        "sender_type":        message.get("sender", {}).get("type"),
        "labels":             message.get("conversation", {}).get("labels"),
        "content":            message.get("content"),
        "content_attributes": message.get("content_attributes"),
    }
