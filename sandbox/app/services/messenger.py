"""Envío de mensajes vía WhatsApp Cloud API (+ helpers de UI)."""
from __future__ import annotations

import logging
import re

import httpx

from app.channels.whatsapp.client import whatsapp_client
from app.config import settings

log = logging.getLogger(__name__)

_IMG_LINE = re.compile(r"^\s*(https?://\S+\.(?:jpg|jpeg|png|webp))\s*$", re.IGNORECASE)


def human_delay(text: str) -> float:
    secs = len(text or "") * settings.typing_seconds_per_char
    return max(settings.typing_min_delay, min(secs, settings.typing_max_delay))


def split_reply(reply: str) -> list[dict]:
    lines = reply.split("\n")
    segments: list[dict] = []
    pending_image: str | None = None
    text_buffer: list[str] = []

    def flush_text():
        text = "\n".join(text_buffer).strip()
        text_buffer.clear()
        return text

    for line in lines:
        m = _IMG_LINE.match(line)
        if m:
            if pending_image is not None:
                segments.append({"type": "image", "url": pending_image, "caption": flush_text()})
            else:
                leftover = flush_text()
                if leftover:
                    segments.append({"type": "text", "text": leftover})
            pending_image = m.group(1)
        else:
            text_buffer.append(line)

    if pending_image is not None:
        segments.append({"type": "image", "url": pending_image, "caption": flush_text()})
    else:
        leftover = flush_text()
        if leftover:
            segments.append({"type": "text", "text": leftover})

    return segments or [{"type": "text", "text": reply}]


async def send_message(wa_id: str, content: str) -> str | None:
    """Envía texto por Cloud API. Devuelve wa_message_id si existe."""
    try:
        data = await whatsapp_client.send_text(wa_id, content)
        return (data.get("messages") or [{}])[0].get("id")
    except Exception as e:
        log.error("Error enviando texto a %s: %s", wa_id, e)
        return None


async def send_image(wa_id: str, image_url: str, caption: str = "") -> str | None:
    """Envía imagen por link público (Cloud API)."""
    try:
        # Cloud API prefiere JPEG/PNG por URL pública HTTPS
        if image_url.lower().endswith(".webp"):
            # Fallback: enviar como texto con link si es webp poco fiable
            text = f"{caption}\n{image_url}".strip() if caption else image_url
            return await send_message(wa_id, text)
        data = await whatsapp_client.send_image_url(wa_id, image_url, caption)
        return (data.get("messages") or [{}])[0].get("id")
    except Exception as e:
        log.error("Error enviando imagen a %s: %s — fallback texto", wa_id, e)
        fallback = f"{caption}\n{image_url}".strip() if caption else image_url
        return await send_message(wa_id, fallback)


async def notify_team(text: str) -> None:
    if not settings.alert_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(settings.alert_webhook_url, json={"text": text})
    except Exception as e:
        log.warning("No se pudo enviar alerta al equipo: %s", e)


async def set_typing(_conversation_id: int, _on: bool) -> None:
    """Cloud API no expone typing estable en todos los números; no-op por ahora."""
    return
