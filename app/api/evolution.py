"""
Webhook nativo de Evolution API: captura el contexto de mensajes citados
(WhatsApp reply), que Chatwoot no reenvía.
"""
import logging

from fastapi import APIRouter, Request

from app.services.buffer import store_evo_quoted

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/evolution-webhook")
async def evolution_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"status": "error", "reason": "invalid json"}

    event = payload.get("event")
    log.info(
        "[EVO-RAW] event=%r data_type=%s keys=%s",
        event, type(payload.get("data")).__name__,
        list(payload.get("data", {}).keys()) if isinstance(payload.get("data"), dict) else "LIST",
    )

    if event != "messages.upsert":
        return {"status": "ignored", "reason": f"event={event!r}"}

    raw_data = payload.get("data", {})
    data = raw_data[0] if isinstance(raw_data, list) else raw_data
    key  = data.get("key", {})

    if key.get("fromMe"):
        return {"status": "ignored", "reason": "fromMe"}

    msg_id  = key.get("id", "")
    message = data.get("message", {})

    context_info = (
        message.get("extendedTextMessage", {}).get("contextInfo")
        or message.get("imageMessage", {}).get("contextInfo")
        or message.get("videoMessage", {}).get("contextInfo")
        or message.get("audioMessage", {}).get("contextInfo")
        or data.get("contextInfo")
        or {}
    )
    quoted_msg  = context_info.get("quotedMessage", {})
    quoted_text = (
        quoted_msg.get("conversation")
        or quoted_msg.get("extendedTextMessage", {}).get("text")
        or quoted_msg.get("imageMessage", {}).get("caption")
        or quoted_msg.get("videoMessage", {}).get("caption")
        or quoted_msg.get("documentMessage", {}).get("caption")
        or ""
    ).strip()

    log.info(
        "[EVO-DBG] msg_id=%r fromMe=%r msg_keys=%s context_keys=%s quoted_text=%r",
        msg_id, key.get("fromMe"), list(message.keys()),
        list(context_info.keys()), quoted_text[:60],
    )

    if msg_id and quoted_text:
        source_id = f"WAID:{msg_id}"
        store_evo_quoted(source_id, quoted_text)
        log.info("[EVO] cita guardada source_id=%s texto=%r", source_id, quoted_text[:80])

    return {"status": "ok"}
