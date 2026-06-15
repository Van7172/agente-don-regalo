"""
Procesamiento de contenido multimedia entrante: audio, imagen, PDF y texto.
"""
import io
import base64
import logging
from urllib.parse import urlsplit, urlunsplit

import httpx
from pypdf import PdfReader

from app.config import settings

log = logging.getLogger(__name__)


def _normalize_chatwoot_url(url: str) -> str:
    """Reescribe el host del adjunto al host público de CHATWOOT_URL.
    Evita 404s causados por hostnames internos del contenedor."""
    if not settings.chatwoot_url or not url:
        return url
    base  = urlsplit(settings.chatwoot_url)
    parts = urlsplit(url)
    return urlunsplit((base.scheme, base.netloc, parts.path, parts.query, parts.fragment))


async def download(url: str) -> bytes | None:
    """Descarga un archivo desde Chatwoot con el token de acceso."""
    url = _normalize_chatwoot_url(url)
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            r = await client.get(
                url,
                headers={"api_access_token": settings.chatwoot_api_token},
                follow_redirects=True,
            )
            r.raise_for_status()
            return r.content
    except Exception as e:
        log.error("Error descargando archivo %s: %s", url, e)
        return None


async def transcribe_audio(audio_bytes: bytes, filename: str) -> str | None:
    """Transcribe una nota de voz usando OpenAI Whisper."""
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
    """Extrae texto plano de un PDF con límite de caracteres."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n\n".join(p.strip() for p in pages if p.strip())
        if len(text) > settings.pdf_max_chars:
            text = text[:settings.pdf_max_chars] + "\n\n[...documento truncado por longitud...]"
        return text
    except Exception as e:
        log.error("Error extrayendo texto del PDF: %s", e)
        return ""


async def message_to_parts(content: str, attachments: list) -> list[dict]:
    """Convierte un mensaje de Chatwoot en partes de contenido OpenAI."""
    if not attachments:
        return [{"type": "text", "text": content}] if content else []

    att      = attachments[0]
    att_type = att.get("file_type", "")
    att_url  = att.get("data_url") or att.get("download_url", "")
    att_name = (att.get("file_name") or att_url).lower()

    if att_type == "audio":
        audio_bytes = await download(att_url)
        if audio_bytes:
            transcription = await transcribe_audio(audio_bytes, att_name)
            if transcription:
                log.info("[AUDIO] transcription=%r", transcription)
                text = f"[Nota de voz transcrita]: {transcription}"
                return [{"type": "text", "text": f"{content}\n{text}" if content else text}]
            return [{"type": "text", "text": content or "[Nota de voz no transcribible]"}]
        return [{"type": "text", "text": content or "[Nota de voz no descargable]"}]

    if att_type == "image":
        image_bytes = await download(att_url)
        if image_bytes:
            if att_name.endswith(".png"):
                mime = "image/png"
            elif att_name.endswith(".webp"):
                mime = "image/webp"
            elif att_name.endswith(".gif"):
                mime = "image/gif"
            else:
                mime = "image/jpeg"
            b64 = base64.b64encode(image_bytes).decode()
            return [
                {"type": "text", "text": content or "El usuario envió una imagen. Descríbela y responde apropiadamente."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        return [{"type": "text", "text": content or "[Imagen no descargable]"}]

    if att_type == "file" and att_name.endswith(".pdf"):
        pdf_bytes = await download(att_url)
        if pdf_bytes:
            pdf_text = extract_pdf_text(pdf_bytes)
            if pdf_text:
                log.info("[PDF] extracted %d chars", len(pdf_text))
                prefix = f"{content}\n\n" if content else ""
                return [{"type": "text", "text": f"{prefix}[Contenido del PDF]:\n{pdf_text}"}]
            return [{"type": "text", "text": content or "[PDF sin texto extraíble]"}]
        return [{"type": "text", "text": content or "[PDF no descargable]"}]

    return [{"type": "text", "text": content or f"[Archivo adjunto de tipo: {att_type}]"}]


def collapse_parts(parts: list[dict]) -> str | list[dict]:
    """Une las partes acumuladas del buffer en un único contenido de mensaje."""
    texts  = [p["text"] for p in parts if p.get("type") == "text" and p.get("text")]
    images = [p for p in parts if p.get("type") == "image_url"]
    joined = "\n".join(texts)
    if not images:
        return joined
    content: list[dict] = []
    if joined:
        content.append({"type": "text", "text": joined})
    content.extend(images)
    return content
