"""
Envío de mensajes y control de estado de escritura en Chatwoot y Evolution API.
"""
import re
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_IMG_LINE = re.compile(r'^\s*(https?://\S+\.(?:jpg|jpeg|png|webp))\s*$', re.IGNORECASE)


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

    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        img = await client.get(image_url, follow_redirects=True)
        img.raise_for_status()
        image_bytes = img.content

    filename = image_url.split("/")[-1].split("?")[0] or "imagen.jpg"
    mime = img.headers.get("content-type", "image/jpeg")

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
    body: dict = {
        "number":    wa_number,
        "mediatype": "image",
        "media":     image_url,
        "fileName":  image_url.split("/")[-1].split("?")[0] or "imagen.jpg",
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

    Preferencia: adjunto en Chatwoot. Evolution directo queda como respaldo
    porque un 201 de Evolution no garantiza que el cliente lo vea en WhatsApp.
    """
    try:
        await _send_image_via_chatwoot(conversation_id, image_url, caption)
        return
    except Exception as e:
        log.error("Error enviando imagen por Chatwoot (%s): %s - intentando Evolution", image_url, e)

    try:
        log.info(
            "[IMG] evolution_check url=%s key=%s instance=%s wa_number=%r",
            bool(settings.evolution_api_url), bool(settings.evolution_api_key),
            settings.evolution_instance or "(vacio)", wa_number,
        )
        await _send_image_via_evolution(wa_number, image_url, caption)
        return
    except Exception as e:
        log.error("Error Evolution sendMedia (%s): %s - enviando fallback texto", image_url, e)

    fallback = f"{caption}\n{image_url}".strip() if caption else image_url
    await send_message(conversation_id, fallback)
