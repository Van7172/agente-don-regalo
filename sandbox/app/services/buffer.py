"""Buffer debounce + orquestación del flush (CRM + WhatsApp Cloud API)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.crm import repository as repo
from app.crm.models import Message
from app.db import SessionLocal
from app.prompts.system import SYSTEM_PROMPT
from app.services.agent import HANDOFF_DONE, run_agent
from app.services.content import collapse_parts, inbound_to_parts
from app.services.messenger import human_delay, notify_team, send_image, send_message, set_typing, split_reply
from app.channels.whatsapp.parser import InboundMessage

log = logging.getLogger(__name__)

_buffers: dict[int, dict] = {}
_buffers_lock = asyncio.Lock()


async def enqueue_inbound(msg: InboundMessage) -> dict:
    """Persiste inbound, aplica gates, encola en buffer."""
    async with SessionLocal() as session:
        tenant = await repo.ensure_default_tenant(session)
        contact = await repo.get_or_create_contact(
            session, tenant.id, msg.wa_id, msg.contact_name
        )
        conv = await repo.get_or_create_conversation(session, tenant.id, contact.id)

        quoted_text = None
        if msg.quoted_wa_id:
            q = await session.execute(
                select(Message).where(Message.wa_message_id == msg.quoted_wa_id)
            )
            quoted_msg = q.scalar_one_or_none()
            if quoted_msg:
                quoted_text = (quoted_msg.content or "")[:400]

        await repo.add_message(
            session,
            conv.id,
            direction="inbound",
            sender_type="contact",
            content=msg.text or msg.caption or f"[{msg.message_type}]",
            wa_message_id=msg.wa_message_id,
            quoted_text=quoted_text,
            raw=msg.raw,
        )
        await session.commit()

        ok, reason = repo.bot_should_reply(conv)
        if not ok:
            log.info("[GATE] conversation=%s ignored: %s", conv.id, reason)
            return {"status": "ignored", "reason": reason}

        conversation_id = conv.id
        contact_id = contact.id
        wa_id = contact.wa_id

    parts = await inbound_to_parts(msg)
    if quoted_text:
        prefix = f"[El cliente está respondiendo al mensaje: «{quoted_text}»]\n"
        parts = [{"type": "text", "text": prefix}] + parts

    async with _buffers_lock:
        buf = _buffers.get(conversation_id)
        if buf and buf.get("task"):
            buf["task"].cancel()
        if not buf:
            buf = {"parts": [], "contact_id": contact_id, "wa_id": wa_id}
            _buffers[conversation_id] = buf
        buf["parts"].extend(parts)
        buf["contact_id"] = contact_id
        buf["wa_id"] = wa_id
        buf["task"] = asyncio.create_task(_flush_after_delay(conversation_id))

    return {"status": "buffered", "conversation_id": conversation_id}


async def _flush_after_delay(conversation_id: int) -> None:
    try:
        await asyncio.sleep(settings.buffer_seconds)
    except asyncio.CancelledError:
        return
    await _flush_buffer(conversation_id)


async def _flush_buffer(conversation_id: int) -> None:
    async with _buffers_lock:
        buf = _buffers.pop(conversation_id, None)
    if not buf or not buf.get("parts"):
        return

    contact_id = buf["contact_id"]
    wa_id = buf["wa_id"]
    user_content = collapse_parts(buf["parts"])
    if not user_content:
        return

    async with SessionLocal() as session:
        # Paralelizar perfil + historial
        profile_task = repo.get_contact_attributes(session, contact_id)
        history_task = repo.get_conversation_history(session, conversation_id)
        profile, history = await asyncio.gather(profile_task, history_task)

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if profile:
            datos = "\n".join(f"- {k}: {v}" for k, v in profile.items() if v)
            messages.append({
                "role": "system",
                "content": (
                    "DATOS CONOCIDOS DEL CLIENTE (de conversaciones previas):\n"
                    f"{datos}\n"
                    "Úsalos para personalizar la atención y NO vuelvas a preguntar lo que ya sabes."
                ),
            })
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_content})

        await set_typing(conversation_id, True)
        try:
            reply = await run_agent(
                messages,
                wa_id=wa_id,
                contact_id=contact_id,
                conversation_id=conversation_id,
                session=session,
            )
            if reply == HANDOFF_DONE:
                log.info("[OUT] conversation=%s handoff", conversation_id)
                return
            if reply:
                log.info("[OUT] conversation=%s reply=%r", conversation_id, reply[:200])
                for segment in split_reply(reply):
                    if segment["type"] == "image":
                        await asyncio.sleep(human_delay(segment.get("caption", "")))
                        wa_mid = await send_image(
                            wa_id, segment["url"], segment.get("caption", "")
                        )
                        await repo.add_message(
                            session,
                            conversation_id,
                            direction="outbound",
                            sender_type="bot",
                            content=segment.get("caption") or segment["url"],
                            wa_message_id=wa_mid,
                            media_url=segment["url"],
                        )
                    else:
                        await asyncio.sleep(human_delay(segment["text"]))
                        wa_mid = await send_message(wa_id, segment["text"])
                        await repo.add_message(
                            session,
                            conversation_id,
                            direction="outbound",
                            sender_type="bot",
                            content=segment["text"],
                            wa_message_id=wa_mid,
                        )
                await session.commit()
            else:
                log.warning("[OUT] conversation=%s sin respuesta; handoff", conversation_id)
                await send_message(
                    wa_id,
                    "Permíteme un momento por favor 🙏 En seguida un asesor de "
                    "nuestro equipo continúa contigo.",
                )
                await repo.set_human_support(session, conversation_id, True)
                await session.commit()
                await notify_team(
                    f"Agente sin respuesta (conversacion {conversation_id}); escalada a humano."
                )
        finally:
            await set_typing(conversation_id, False)
