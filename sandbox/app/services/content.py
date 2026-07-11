"""Procesamiento de multimedia entrante vía WhatsApp Cloud API."""
from __future__ import annotations

import base64
import io
import logging

import httpx
from pypdf import PdfReader

from app.channels.whatsapp.client import whatsapp_client
from app.channels.whatsapp.parser import InboundMessage
from app.config import settings

log = logging.getLogger(__name__)


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.ogg") -> str | None:
    ext = "ogg"
    for supported in ("mp3", "mp4", "m4a", "wav", "webm", "ogg", "mpeg"):
        if filename.endswith(f".{supported}"):
            ext = supported
            break
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                files={"file": (f"audio.{ext}", audio_bytes, f"audio/{ext}")},
                data={"model": "whisper-1", "language": "es"},
            )
            r.raise_for_status()
            return r.json().get("text", "").strip()
    except Exception as e:
        log.error("Error transcribiendo audio: %s", e)
        return None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        if len(text) > settings.pdf_max_chars:
            text = text[: settings.pdf_max_chars] + "\n\n[...documento truncado por longitud...]"
        return text
    except Exception as e:
        log.error("Error extrayendo texto del PDF: %s", e)
        return ""


async def inbound_to_parts(msg: InboundMessage) -> list[dict]:
    """Convierte un InboundMessage Cloud API en partes OpenAI."""
    if msg.message_type == "text" or (msg.text and not msg.media_id):
        return [{"type": "text", "text": msg.text}] if msg.text else []

    if msg.message_type == "audio" and msg.media_id:
        data, mime = await whatsapp_client.download_media(msg.media_id)
        transcription = await transcribe_audio(data, f"audio.{(mime or '').split('/')[-1] or 'ogg'}")
        if transcription:
            return [{"type": "text", "text": f"[Nota de voz transcrita]: {transcription}"}]
        return [{"type": "text", "text": "[Nota de voz no transcribible]"}]

    if msg.message_type == "image" and msg.media_id:
        data, mime = await whatsapp_client.download_media(msg.media_id)
        b64 = base64.b64encode(data).decode()
        mime = mime or "image/jpeg"
        text = msg.caption or "El usuario envió una imagen. Descríbela y responde apropiadamente."
        return [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]

    if msg.message_type == "document" and msg.media_id:
        data, mime = await whatsapp_client.download_media(msg.media_id)
        if (mime or "").endswith("pdf") or (msg.caption or "").lower().endswith(".pdf"):
            pdf_text = extract_pdf_text(data)
            if pdf_text:
                return [{"type": "text", "text": f"[Contenido del PDF]:\n{pdf_text}"}]
        return [{"type": "text", "text": msg.text or f"[Documento: {mime}]"}]

    return [{"type": "text", "text": msg.text or f"[Mensaje tipo {msg.message_type}]"}]


def collapse_parts(parts: list[dict]) -> str | list[dict]:
    texts = [p["text"] for p in parts if p.get("type") == "text" and p.get("text")]
    images = [p for p in parts if p.get("type") == "image_url"]
    joined = "\n".join(texts)
    if not images:
        return joined
    content: list[dict] = []
    if joined:
        content.append({"type": "text", "text": joined})
    content.extend(images)
    return content
