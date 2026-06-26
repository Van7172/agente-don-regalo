"""
Buffer de mensajes con debounce: agrupa los mensajes rápidos del cliente
y los procesa juntos tras BUFFER_SECONDS de silencio.
"""
import asyncio
import logging

import httpx

from app.config import settings
from app.prompts.system import SYSTEM_PROMPT
from app.services.content import message_to_parts, collapse_parts
from app.services.memory import get_contact_attributes, get_conversation_history
from app.services.messenger import send_message, send_image, set_typing, human_delay, split_reply, add_label
from app.services.agent import run_agent

log = logging.getLogger(__name__)

# Estado en memoria: {conversation_id: {parts, contact_id, wa_number, task, ...}}
_buffers: dict[int, dict] = {}
_buffers_lock = asyncio.Lock()

# Cita de mensajes capturada desde Evolution API antes de que llegue el webhook de Chatwoot.
# Clave: source_id ("WAID:..."), valor: texto del mensaje citado.
_evo_quoted: dict[str, str] = {}


def store_evo_quoted(source_id: str, quoted_text: str) -> None:
    """Guarda el texto citado recibido desde /evolution-webhook."""
    _evo_quoted[source_id] = quoted_text
    if len(_evo_quoted) > 500:
        oldest = list(_evo_quoted.keys())[:100]
        for k in oldest:
            _evo_quoted.pop(k, None)


async def enqueue(
    conversation_id: int,
    contact_id: int | None,
    wa_number: str,
    source_id: str,
    in_reply_to_id: int | None,
    content: str,
    attachments: list,
) -> None:
    """Convierte el mensaje entrante en partes y lo agrega al buffer."""
    parts = await message_to_parts(content, attachments)

    async with _buffers_lock:
        buf = _buffers.get(conversation_id)
        if buf and buf.get("task"):
            buf["task"].cancel()
        if not buf:
            buf = {"parts": [], "contact_id": contact_id, "wa_number": wa_number}
            _buffers[conversation_id] = buf
        buf["parts"].extend(parts)
        buf["contact_id"]  = contact_id
        buf["wa_number"]   = wa_number
        if source_id:
            buf["source_id"] = source_id
        if in_reply_to_id:
            buf["in_reply_to_id"] = in_reply_to_id
        buf["task"] = asyncio.create_task(_flush_after_delay(conversation_id))


async def _flush_after_delay(conversation_id: int) -> None:
    try:
        await asyncio.sleep(settings.buffer_seconds)
    except asyncio.CancelledError:
        return
    await _flush_buffer(conversation_id)


async def _get_quoted_message(conversation_id: int, message_id: int) -> str:
    """Busca en Chatwoot el texto de un mensaje citado por ID."""
    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            r = await client.get(url, headers={"api_access_token": settings.chatwoot_api_token})
            r.raise_for_status()
            for m in r.json().get("payload", []):
                if m.get("id") == message_id:
                    return (m.get("content") or "").strip()
    except Exception as e:
        log.warning("No se pudo leer mensaje citado id=%s: %s", message_id, e)
    return ""


async def _get_evolution_quoted(source_id: str) -> str:
    """Consulta Evolution API para obtener el texto del mensaje citado."""
    if not settings.evolution_api_url or not settings.evolution_api_key or not settings.evolution_instance:
        return ""
    if not source_id.startswith("WAID:"):
        return ""

    msg_id = source_id[5:]
    url    = f"{settings.evolution_api_url}/chat/findMessages/{settings.evolution_instance}"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                url,
                headers={"apikey": settings.evolution_api_key, "Content-Type": "application/json"},
                json={"where": {"key": {"id": msg_id}}},
            )
            r.raise_for_status()
            msgs = r.json()
            if not isinstance(msgs, list) or not msgs:
                return ""
            msg_obj = msgs[0]
            message = msg_obj.get("message", {})
            ctx = (
                message.get("extendedTextMessage", {}).get("contextInfo")
                or message.get("imageMessage", {}).get("contextInfo")
                or message.get("videoMessage", {}).get("contextInfo")
                or message.get("audioMessage", {}).get("contextInfo")
                or msg_obj.get("contextInfo")
                or {}
            )
            quoted_msg  = ctx.get("quotedMessage", {})
            quoted_text = (
                quoted_msg.get("conversation")
                or quoted_msg.get("extendedTextMessage", {}).get("text")
                or quoted_msg.get("imageMessage", {}).get("caption")
                or quoted_msg.get("videoMessage", {}).get("caption")
                or quoted_msg.get("documentMessage", {}).get("caption")
                or ""
            ).strip()
            log.info("[EVO-PULL] source_id=%s quoted_text=%r", source_id, quoted_text[:120])
            return quoted_text
    except Exception as e:
        log.warning("Error consultando Evolution quoted para %s: %s", source_id, e)
    return ""


async def _flush_buffer(conversation_id: int) -> None:
    """Procesa todos los mensajes acumulados de una conversación como uno solo."""
    async with _buffers_lock:
        buf = _buffers.pop(conversation_id, None)
    if not buf or not buf.get("parts"):
        return

    contact_id     = buf["contact_id"]
    wa_number      = buf.get("wa_number", "")
    source_id      = buf.get("source_id", "")
    in_reply_to_id = buf.get("in_reply_to_id")
    user_content   = collapse_parts(buf["parts"])
    if not user_content:
        return

    # Resolución del mensaje citado (3 fuentes: push EVO, pull EVO, Chatwoot fallback)
    quoted = ""
    if source_id:
        quoted = _evo_quoted.pop(source_id, "")
    if not quoted and source_id:
        quoted = await _get_evolution_quoted(source_id)
    if not quoted and in_reply_to_id:
        quoted = await _get_quoted_message(conversation_id, in_reply_to_id)
    if quoted:
        quoted_short = quoted[:400] + "…" if len(quoted) > 400 else quoted
        prefix = f"[El cliente está respondiendo al mensaje: «{quoted_short}»]\n"
        log.info("[QUOTED] conversation=%s prefix=%r", conversation_id, prefix[:120])
        if isinstance(user_content, str):
            user_content = prefix + user_content
        else:
            user_content = [{"type": "text", "text": prefix}] + user_content

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Memoria de largo plazo
    profile = await get_contact_attributes(contact_id) if contact_id else {}
    if profile:
        log.info("[MEM] contact=%s profile=%s", contact_id, profile)
        datos = "\n".join(f"- {k}: {v}" for k, v in profile.items() if v)
        messages.append({
            "role": "system",
            "content": (
                "DATOS CONOCIDOS DEL CLIENTE (de conversaciones previas):\n"
                f"{datos}\n"
                "Úsalos para personalizar la atención y NO vuelvas a preguntar lo que ya sabes. "
                "Si aprendes datos nuevos o corregidos, guárdalos con `guardar_datos_cliente`."
            ),
        })

    # Memoria de corto plazo
    history = await get_conversation_history(conversation_id)
    if history:
        log.info("[CTX] conversation=%s history=%d mensajes", conversation_id, len(history))
        messages.extend(history)

    messages.append({"role": "user", "content": user_content})

    await set_typing(conversation_id, True)
    try:
        reply = await run_agent(messages, contact_id, conversation_id)
        if reply:
            log.info("[OUT] conversation=%s reply=%r", conversation_id, reply)
            for segment in split_reply(reply):
                if segment["type"] == "image":
                    await asyncio.sleep(human_delay(segment.get("caption", "")))
                    await send_image(conversation_id, wa_number, segment["url"], segment["caption"])
                else:
                    await asyncio.sleep(human_delay(segment["text"]))
                    await send_message(conversation_id, segment["text"])
        else:
            # El agente no produjo respuesta (error, límite de rondas o salida
            # vacía). Escalar a un asesor humano en lugar de quedar mudo.
            log.warning("[OUT] conversation=%s sin respuesta del agente; escalando a soporte humano", conversation_id)
            # 1) PRIMERO el mensaje de espera para el cliente.
            await send_message(
                conversation_id,
                "Permíteme un momento por favor 🙏 En seguida un asesor de "
                "nuestro equipo continúa contigo.",
            )
            # 2) Luego etiquetar la conversación para que el equipo intervenga.
            #    Mientras la etiqueta esté activa, el bot deja de responder
            #    (ver gate en app/api/webhook.py).
            await add_label(conversation_id, settings.human_support_label)
    finally:
        await set_typing(conversation_id, False)
