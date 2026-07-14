"""Buffer debounce + orquestación del flush (CRM local o externo Opción C)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.channels.whatsapp.client import whatsapp_client
from app.channels.whatsapp.parser import InboundMessage
from app.config import settings
from app.crm import http_client as crm_http
from app.crm import repository as repo
from app.crm.models import Message
from app.db import SessionLocal
from app.prompts.compose import profile_block
from app.services.agent import HANDOFF_DONE
from app.harness.master import run_master
from app.harness.releaser import REENGAGE_MSG, try_release_conversation
from app.harness.state import load_state, save_state
from app.services.content import collapse_parts, inbound_to_parts
from app.services.messenger import (
    human_delay,
    notify_team,
    send_image,
    send_message,
    set_typing,
    split_reply,
)
from app.services.watchdog import record_fallback_event

log = logging.getLogger(__name__)

_FALLBACK_SOFT_MSG = (
    "Disculpa, se me cruzó un cable 😅 Cuéntame otra vez qué buscas "
    "y te ayudo al toque."
)

_buffers: dict[int, dict] = {}
_buffers_lock = asyncio.Lock()


def _use_external_crm() -> bool:
    return crm_http.crm_enabled()


async def enqueue_inbound(msg: InboundMessage) -> dict:
    """Persiste inbound, aplica gates, encola en buffer."""
    if _use_external_crm():
        return await _enqueue_external(msg)
    return await _enqueue_local(msg)


async def _archive_media(msg: InboundMessage) -> tuple[str | None, tuple[bytes, str] | None]:
    """Baja el adjunto de Meta y lo archiva en el CRM.

    Los medios de WhatsApp caducan y solo se descargan con el token de Meta, así
    que sin archivarlos el asesor nunca podría ver la foto ni oír la nota de voz.
    Devuelve (clave en el CRM, bytes) para que el LLM reutilice la descarga.
    """
    if not msg.media_id:
        return None, None

    try:
        data, mime = await whatsapp_client.download_media(msg.media_id)
    except Exception as err:
        log.warning("[MEDIA] no se pudo descargar %s: %s", msg.media_id, err)
        return None, None

    key = None
    try:
        ext = (mime or "").split("/")[-1].split(";")[0] or "bin"
        key = await crm_http.upload_media(data, f"{msg.message_type}.{ext}", mime)
        log.info("[MEDIA] %s archivado en el CRM: %s", msg.message_type, key)
    except Exception as err:
        # Que no se pueda archivar no debe impedir que el bot responda.
        log.warning("[MEDIA] no se pudo archivar en el CRM: %s", err)

    return key, (data, mime)


async def _enqueue_external(msg: InboundMessage) -> dict:
    media_key, prefetched = await _archive_media(msg)

    data = await crm_http.upsert_inbound(
        msg.wa_id,
        name=msg.contact_name or "",
        content=msg.text or msg.caption or f"[{msg.message_type}]",
        wa_message_id=msg.wa_message_id,
        media_url=media_key,
        quoted_text=None,
    )
    conv = data.get("conversation") or {}
    conversation_id = int(data["conversation_id"])
    contact_id = int(data.get("contact_id") or 0)
    wa_id = msg.wa_id

    if conv.get("mode") == "HUMAN" or conv.get("human_support") or not conv.get("bot_active", True):
        # Intentar auto-retorno HUMAN→AI (asesor olvidó Modo AI).
        try:
            detail = await crm_http.get_conversation(conversation_id)
            released, _st = await try_release_conversation(
                conversation_id,
                wa_id=wa_id,
                conv={
                    "mode": conv.get("mode"),
                    "human_support": conv.get("human_support"),
                },
                messages=detail.get("messages") or [],
            )
            if released:
                log.info("[GATE] conversation=%s released HUMAN→AI", conversation_id)
                # Enviar reenganche breve y seguir al buffer.
                try:
                    wa_mid = await send_message(wa_id, REENGAGE_MSG)
                    await crm_http.append_outbound(
                        conversation_id, REENGAGE_MSG, wa_message_id=wa_mid
                    )
                except Exception as err:
                    log.warning("[GATE] reengage msg falló: %s", err)
                # No return: continuar a buffer
            else:
                reason = (
                    "human_mode"
                    if conv.get("mode") == "HUMAN" or conv.get("human_support")
                    else "bot_off"
                )
                log.info("[GATE] conversation=%s ignored: %s", conversation_id, reason)
                return {"status": "ignored", "reason": reason}
        except Exception as err:
            log.warning("[GATE] releaser error: %s", err)
            reason = (
                "human_mode"
                if conv.get("mode") == "HUMAN" or conv.get("human_support")
                else "bot_off"
            )
            log.info("[GATE] conversation=%s ignored: %s", conversation_id, reason)
            return {"status": "ignored", "reason": reason}

    paused = await crm_http.get_setting("paused")
    if paused == "1":
        log.info("[GATE] conversation=%s ignored: paused", conversation_id)
        return {"status": "ignored", "reason": "paused"}

    parts = await inbound_to_parts(msg, prefetched)
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


async def _enqueue_local(msg: InboundMessage) -> dict:
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

    if _use_external_crm():
        await _flush_external(conversation_id, contact_id, wa_id, user_content)
    else:
        await _flush_local(conversation_id, contact_id, wa_id, user_content)


async def _build_messages(profile: dict, history: list, user_content) -> list:
    """Perfil + historial + turno. El system message lo compone el harness.

    Antes aquí se inyectaba el SYSTEM_PROMPT monolítico y el master lo descartaba
    después buscando un string dentro. Ahora cada especialista recibe el suyo,
    compuesto en `prompts/compose.py`.
    """
    messages: list = []
    if profile:
        messages.append({"role": "system", "content": profile_block(profile)})
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    return messages


async def _send_reply_segments(wa_id: str, conversation_id: int, reply: str, persist) -> None:
    """Envía segmentos. Cards de producto casi sin pausa; texto con delay corto."""
    segments = split_reply(reply)
    for i, segment in enumerate(segments):
        if segment["type"] == "image":
            if i > 0:
                await asyncio.sleep(0.08)
            wa_mid = await send_image(wa_id, segment["url"], segment.get("caption", ""))
            await persist(
                content=segment.get("caption") or segment["url"],
                wa_message_id=wa_mid,
                media_url=segment["url"],
            )
        else:
            await asyncio.sleep(min(human_delay(segment["text"]), 0.35))
            wa_mid = await send_message(wa_id, segment["text"])
            await persist(content=segment["text"], wa_message_id=wa_mid, media_url=None)


async def _flush_external(
    conversation_id: int, contact_id: int, wa_id: str, user_content
) -> None:
    detail = await crm_http.get_conversation(conversation_id)
    memory = await crm_http.get_memory(wa_id) or {}
    profile = {
        k: memory.get(k)
        for k in (
            "nombre_memory",
            "email_memory",
            "objetivo_memory",
            "situacion_memory",
            "temperatura_memory",
            "resumen_memory",
        )
        if memory.get(k)
    }
    # Alias legibles para el prompt
    if memory.get("nombre_memory"):
        profile["nombre"] = memory["nombre_memory"]
    if memory.get("email_memory"):
        profile["email"] = memory["email_memory"]

    history = []
    for m in detail.get("messages") or []:
        role = m.get("role") or ("user" if m.get("direction") == "inbound" else "assistant")
        if role in ("user", "assistant", "human"):
            mapped = "assistant" if role == "human" else role
            history.append({"role": mapped, "content": m.get("content") or ""})
    # El último inbound ya está en CRM; evitamos duplicarlo si coincide con user_content
    if history and history[-1].get("role") == "user":
        history = history[:-1]

    messages = await _build_messages(profile, history, user_content)

    async def persist(*, content: str, wa_message_id=None, media_url=None):
        await crm_http.append_outbound(
            conversation_id,
            content,
            wa_message_id=wa_message_id,
            media_url=media_url,
        )

    await set_typing(conversation_id, True)
    try:
        reply = await run_master(
            messages,
            wa_id=wa_id,
            contact_id=contact_id or None,
            conversation_id=conversation_id,
            session=None,
            use_external_crm=True,
            persist=persist,
        )
        if reply == HANDOFF_DONE:
            log.info("[OUT] conversation=%s handoff", conversation_id)
            return
        if reply:
            log.info("[OUT] conversation=%s reply=%r", conversation_id, reply[:200])
            await _send_reply_segments(wa_id, conversation_id, reply, persist)
        else:
            # Antes escalábamos a HUMAN ante cualquier None (timeout, max rounds,
            # glitch). Eso mataba leads sanos tipo "suculentas para el colegio".
            # Recovery suave: el bot sigue a cargo; solo el handoff explícito
            # o la cuota agotada deben pasar a humano.
            log.warning("[OUT] conversation=%s sin respuesta; recovery suave", conversation_id)
            wa_mid = await send_message(wa_id, _FALLBACK_SOFT_MSG)
            await persist(content=_FALLBACK_SOFT_MSG, wa_message_id=wa_mid, media_url=None)
            record_fallback_event()
            await notify_team(
                f"Agente sin respuesta (conversacion {conversation_id}); "
                f"recovery suave, bot sigue activo."
            )
    finally:
        await set_typing(conversation_id, False)


async def _flush_local(
    conversation_id: int, contact_id: int, wa_id: str, user_content
) -> None:
    async with SessionLocal() as session:
        profile_task = repo.get_contact_attributes(session, contact_id)
        history_task = repo.get_conversation_history(session, conversation_id)
        profile, history = await asyncio.gather(profile_task, history_task)
        messages = await _build_messages(profile, history, user_content)

        # Commit en cada mensaje: si el agente escala y sale antes, los avisos que
        # ya envió (filler, mensaje de espera) no deben perderse.
        async def persist(*, content: str, wa_message_id=None, media_url=None):
            await repo.add_message(
                session,
                conversation_id,
                direction="outbound",
                sender_type="bot",
                content=content,
                wa_message_id=wa_message_id,
                media_url=media_url,
            )
            await session.commit()

        await set_typing(conversation_id, True)
        try:
            reply = await run_master(
                messages,
                wa_id=wa_id,
                contact_id=contact_id,
                conversation_id=conversation_id,
                session=session,
                use_external_crm=False,
                persist=persist,
            )
            if reply == HANDOFF_DONE:
                log.info("[OUT] conversation=%s handoff", conversation_id)
                return
            if reply:
                log.info("[OUT] conversation=%s reply=%r", conversation_id, reply[:200])
                await _send_reply_segments(wa_id, conversation_id, reply, persist)
            else:
                log.warning("[OUT] conversation=%s sin respuesta; recovery suave", conversation_id)
                wa_mid = await send_message(wa_id, _FALLBACK_SOFT_MSG)
                await persist(content=_FALLBACK_SOFT_MSG, wa_message_id=wa_mid, media_url=None)
                await notify_team(
                    f"Agente sin respuesta (conversacion {conversation_id}); "
                    f"recovery suave, bot sigue activo."
                )
        finally:
            await set_typing(conversation_id, False)
