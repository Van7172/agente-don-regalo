"""Envío de mensajes vía WhatsApp Cloud API (+ helpers de UI)."""
from __future__ import annotations

import io
import logging
import re

import httpx

from app.channels.whatsapp.client import whatsapp_client
from app.config import settings

log = logging.getLogger(__name__)

_IMG_LINE = re.compile(r"^\s*(https?://\S+\.(?:jpg|jpeg|png|webp))\s*$", re.IGNORECASE)
_DIRECT_IMAGE_MIMES = {"image/jpeg", "image/png"}


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


def _prepare_image_bytes(image_bytes: bytes, filename: str, mime: str) -> tuple[bytes, str, str]:
    """Normaliza a JPEG/PNG; WebP y otros se convierten a JPEG."""
    clean_mime = (mime or "image/jpeg").split(";")[0].strip().lower()
    lower_name = filename.lower()
    if clean_mime in _DIRECT_IMAGE_MIMES and not lower_name.endswith(".webp"):
        return image_bytes, filename, clean_mime

    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.mode in ("RGBA", "LA") or "transparency" in img.info:
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            rgb = background
        else:
            rgb = img.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=90, optimize=True)

    stem = re.sub(r"\.[^.]+$", "", filename) or "imagen"
    return out.getvalue(), f"{stem}.jpg", "image/jpeg"


async def _download_and_prepare(image_url: str) -> tuple[bytes, str, str]:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        img = await client.get(image_url)
        img.raise_for_status()
        image_bytes = img.content
        mime = img.headers.get("content-type", "image/jpeg")
    filename = image_url.split("/")[-1].split("?")[0] or "imagen.jpg"
    return _prepare_image_bytes(image_bytes, filename, mime)


async def send_image(wa_id: str, image_url: str, caption: str = "") -> str | None:
    """Envía imagen por Cloud API. WebP se convierte a JPEG y se sube a Meta."""
    try:
        lower = image_url.lower().split("?")[0]
        if lower.endswith(".webp") or lower.endswith(".gif"):
            data_bytes, filename, mime = await _download_and_prepare(image_url)
            media_id = await whatsapp_client.upload_media(data_bytes, mime, filename)
            data = await whatsapp_client.send_image_id(wa_id, media_id, caption)
            return (data.get("messages") or [{}])[0].get("id")

        data = await whatsapp_client.send_image_url(wa_id, image_url, caption)
        return (data.get("messages") or [{}])[0].get("id")
    except Exception as e:
        log.error("Error enviando imagen a %s (%s): %s — reintento upload", wa_id, image_url[:80], e)
        try:
            data_bytes, filename, mime = await _download_and_prepare(image_url)
            media_id = await whatsapp_client.upload_media(data_bytes, mime, filename)
            data = await whatsapp_client.send_image_id(wa_id, media_id, caption)
            return (data.get("messages") or [{}])[0].get("id")
        except Exception as e2:
            log.error("Fallback imagen falló: %s — envío caption sin link crudo", e2)
            if caption:
                return await send_message(wa_id, caption)
            return None


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
