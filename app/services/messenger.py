"""
Envío de mensajes y control de estado de escritura en Chatwoot y Evolution API.
"""
import re
import io
import base64
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_IMG_LINE = re.compile(r'^\s*(https?://\S+\.(?:jpg|jpeg|png|webp))\s*$', re.IGNORECASE)
_SEND_PRODUCT_MEDIA = True
_DIRECT_IMAGE_MIMES = {"image/jpeg", "image/png"}


def human_delay(text: str) -> float:
    """Pausa proporcional al texto para simular escritura humana."""
    secs = len(text or "") * settings.typing_seconds_per_char
    return max(settings.typing_min_delay, min(secs, settings.typing_max_delay))


def split_reply(reply: str) -> list[dict]:
    """Divide la respuesta en segmentos {type: image|text}.

    Patrón: URL de imagen en línea sola → imagen; resto → texto.
    """
    lines    = reply.split("\n")
    segments: list[dict] = []
    pending_image: str | None = None
    text_buffer: list[str]    = []

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


async def send_message(conversation_id: int, content: str) -> None:
    """Envía un mensaje saliente a la conversación en Chatwoot."""
    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                headers={
                    "api_access_token": settings.chatwoot_api_token,
                    "Content-Type": "application/json",
                },
                json={
                    "content": content,
                    "message_type": "outgoing",
                    "private": False,
                    "content_attributes": {"bot": True},
                },
            )
            r.raise_for_status()
    except Exception as e:
        log.error("Error enviando mensaje a conversación %s: %s", conversation_id, e)


async def add_label(conversation_id: int, label: str) -> None:
    """Agrega una etiqueta a la conversación SIN borrar las existentes.

    El endpoint de Chatwoot reemplaza la lista completa de etiquetas, así que
    primero leemos las actuales y enviamos la unión.
    """
    base = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}/labels"
    )
    headers = {"api_access_token": settings.chatwoot_api_token, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.get(base, headers={"api_access_token": settings.chatwoot_api_token})
            r.raise_for_status()
            current = r.json().get("payload") or []
            if label in current:
                return
            nuevas = list(dict.fromkeys([*current, label]))
            r2 = await client.post(base, headers=headers, json={"labels": nuevas})
            r2.raise_for_status()
        log.info("[LABEL] '%s' agregada a conversation=%s (labels=%s)", label, conversation_id, nuevas)
    except Exception as e:
        log.error("No se pudo agregar etiqueta '%s' a conversación %s: %s", label, conversation_id, e)


async def set_typing(conversation_id: int, on: bool) -> None:
    """Activa/desactiva el indicador 'escribiendo…' en Chatwoot."""
    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}/toggle_typing_status"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            r = await client.post(
                url,
                headers={
                    "api_access_token": settings.chatwoot_api_token,
                    "Content-Type": "application/json",
                },
                json={"typing_status": "on" if on else "off"},
            )
            r.raise_for_status()
    except Exception as e:
        log.warning("No se pudo cambiar typing status (%s): %s", conversation_id, e)


async def _send_image_via_chatwoot(conversation_id: int, image_url: str, caption: str = "") -> None:
    """Sube una imagen como adjunto saliente de Chatwoot."""
    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}/messages"
    )

    image_bytes, filename, mime = await _download_image_for_whatsapp(image_url)

    data: dict = {"message_type": "outgoing", "private": "false"}
    if caption:
        data["content"] = caption

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        r = await client.post(
            url,
            headers={"api_access_token": settings.chatwoot_api_token},
            data=data,
            files={"attachments[]": (filename, image_bytes, mime)},
        )
        r.raise_for_status()
    log.info("[IMG] enviada via Chatwoot conversation=%s: %s", conversation_id, image_url)


async def _download_image_for_whatsapp(image_url: str) -> tuple[bytes, str, str]:
    """Descarga y normaliza una imagen para enviarla por WhatsApp."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        img = await client.get(image_url, follow_redirects=True)
        img.raise_for_status()
        image_bytes = img.content

    filename = image_url.split("/")[-1].split("?")[0] or "imagen.jpg"
    mime = img.headers.get("content-type", "image/jpeg")
    return _prepare_image_for_whatsapp(image_bytes, filename, mime)


def _prepare_image_for_whatsapp(image_bytes: bytes, filename: str, mime: str) -> tuple[bytes, str, str]:
    """Convierte formatos poco confiables para WhatsApp a JPEG."""
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


async def _send_image_via_evolution(wa_number: str, image_url: str, caption: str = "") -> None:
    """Envia una imagen directo por Evolution API."""
    if not (
        settings.evolution_api_url
        and settings.evolution_api_key
        and settings.evolution_instance
        and wa_number
    ):
        raise RuntimeError("Evolution API no configurada o wa_number vacio")

    endpoint = f"{settings.evolution_api_url}/message/sendMedia/{settings.evolution_instance}"
    image_bytes, filename, mime = await _download_image_for_whatsapp(image_url)
    media_b64 = base64.b64encode(image_bytes).decode("ascii")
    body: dict = {
        "number":    wa_number,
        "mediatype": "image",
        "mediaType": "image",
        "mimetype":  mime,
        "media":     media_b64,
        "fileName":  filename,
    }
    if caption:
        body["caption"] = caption

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            endpoint,
            headers={"apikey": settings.evolution_api_key, "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        log.info(
            "[IMG] enviada via Evolution a %s: %s response=%s",
            wa_number,
            image_url,
            r.text[:300],
        )


async def send_image(conversation_id: int, wa_number: str, image_url: str, caption: str = "") -> None:
    """Envia una imagen de producto a WhatsApp.

    Envia media real. Las imagenes WebP se convierten a JPEG antes de subirlas
    porque WhatsApp/Evolution puede aceptar WebP con HTTP 200/201 y no mostrarlo
    como imagen normal en el celular.
    """
    if not _SEND_PRODUCT_MEDIA:
        text_fallback = f"{caption}\nFoto: {image_url}".strip() if caption else image_url
        await send_message(conversation_id, text_fallback)
        log.info("[IMG] enviada como texto conversation=%s: %s", conversation_id, image_url)
        return

    try:
        log.info(
            "[IMG] evolution_check url=%s key=%s instance=%s wa_number=%r",
            bool(settings.evolution_api_url), bool(settings.evolution_api_key),
            settings.evolution_instance or "(vacio)", wa_number,
        )
        await _send_image_via_evolution(wa_number, image_url, caption)
        return
    except Exception as e:
        log.error("Error Evolution sendMedia (%s): %s - intentando Chatwoot", image_url, e)

    try:
        await _send_image_via_chatwoot(conversation_id, image_url, caption)
        return
    except Exception as e:
        log.error("Error enviando imagen por Chatwoot (%s): %s - enviando fallback texto", image_url, e)

    fallback = f"{caption}\n{image_url}".strip() if caption else image_url
    await send_message(conversation_id, fallback)
