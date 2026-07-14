"""Entrega outbox del asesor a WhatsApp (push CRM o drenaje periódico)."""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.crm import http_client as crm_http
from app.services.messenger import send_media, send_message

log = logging.getLogger(__name__)


def _stored_content(*, content: str, msg_type: str, filename: str) -> str:
    text = (content or "").strip()
    if text:
        return text
    if msg_type == "document":
        return filename or "[document]"
    if msg_type == "image":
        return "[image]"
    if msg_type == "audio":
        return "[audio]"
    return "[media]"


async def deliver_outbox(
    *,
    wa_id: str,
    content: str = "",
    conversation_id: int | None = None,
    outbox_id: int | None = None,
    msg_type: str = "text",
    media_path: str | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """Envía a Meta y persiste el mensaje en el CRM. Lanza si falla."""
    if settings.whatsapp_dry_run:
        log.warning(
            "[OUTBOX] WHATSAPP_DRY_RUN=1 — el mensaje NO llegará al WhatsApp real "
            "(wa_id=%s outbox=%s)",
            wa_id,
            outbox_id,
        )

    media_key = media_path
    log.info(
        "[OUTBOX] send type=%s to=%s outbox=%s conv=%s dry_run=%s media=%s",
        msg_type,
        wa_id,
        outbox_id,
        conversation_id,
        settings.whatsapp_dry_run,
        bool(media_key),
    )

    if media_key and msg_type in ("image", "audio", "document"):
        data, mime = await crm_http.fetch_media(media_key)
        wa_mid = await send_media(
            wa_id,
            msg_type,
            data,
            mime,
            filename=filename,
            caption=content,
        )
        stored = _stored_content(content=content, msg_type=msg_type, filename=filename)
    else:
        if not (content or "").strip():
            raise ValueError("mensaje vacío")
        wa_mid = await send_message(wa_id, content)
        stored = content
        media_key = None

    if outbox_id and crm_http.crm_enabled():
        await crm_http.mark_outbox(outbox_id, "sent")
    if conversation_id and crm_http.crm_enabled():
        await crm_http.append_outbound(
            conversation_id,
            stored,
            sender_type="agent",
            role="human",
            wa_message_id=wa_mid,
            media_url=media_key,
        )
        await crm_http.set_mode(conversation_id, "HUMAN")

    return {"status": "ok", "wa_message_id": wa_mid}


def _row_field(row: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


async def drain_pending_outbox(limit: int = 10) -> int:
    """Procesa filas pending del CRM (si el push PHP→agente falló)."""
    if not crm_http.crm_enabled():
        return 0
    try:
        rows = await crm_http.list_pending_outbox()
    except Exception as err:
        log.warning("[OUTBOX] no pude listar pending: %s", err)
        return 0

    done = 0
    for row in (rows or [])[:limit]:
        outbox_id = int(_row_field(row, "id_outbox", "id", default=0) or 0)
        wa_id = str(_row_field(row, "wa_id", "waId", default="") or "")
        content = str(_row_field(row, "content_outbox", "content", default="") or "")
        msg_type = str(_row_field(row, "type_outbox", "type", default="text") or "text")
        media_path = _row_field(row, "media_path", "mediaPath")
        conversation_id = _row_field(row, "id_conversation", "conversation_id", "conversationId")
        try:
            conv_id = int(conversation_id) if conversation_id is not None else None
        except (TypeError, ValueError):
            conv_id = None

        if not outbox_id or not wa_id:
            continue

        try:
            await deliver_outbox(
                wa_id=wa_id,
                content=content,
                conversation_id=conv_id,
                outbox_id=outbox_id,
                msg_type=msg_type,
                media_path=str(media_path) if media_path else None,
                filename="",
            )
            done += 1
            log.info("[OUTBOX] drenado outbox_id=%s", outbox_id)
        except Exception as err:
            log.error("[OUTBOX] falló outbox_id=%s: %s", outbox_id, err)
            try:
                await crm_http.mark_outbox(outbox_id, "failed", str(err)[:500])
            except Exception:
                pass
    return done
