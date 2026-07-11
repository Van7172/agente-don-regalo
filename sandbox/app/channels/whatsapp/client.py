"""Cliente Graph API para WhatsApp Cloud API."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self) -> None:
        self.token = settings.whatsapp_token
        self.phone_id = settings.whatsapp_phone_number_id
        self.base = settings.whatsapp_graph_url

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _messages_url(self) -> str:
        return f"{self.base}/{self.phone_id}/messages"

    async def send_text(self, to_wa_id: str, text: str) -> dict[str, Any]:
        if settings.whatsapp_dry_run:
            fake_id = f"wamid.dry.{int(__import__('time').time() * 1000)}"
            log.info("[WA-DRY] text -> %s id=%s body=%r", to_wa_id, fake_id, text[:120])
            return {"messages": [{"id": fake_id}]}
        if not self.token or not self.phone_id:
            raise RuntimeError("WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID vacíos")
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(self._messages_url(), headers=self._headers, json=body)
            if r.status_code >= 400:
                log.error(
                    "[WA] send_text FAIL status=%s to=%s body=%s",
                    r.status_code,
                    to_wa_id,
                    r.text[:800],
                )
            r.raise_for_status()
            data = r.json()
            log.info("[WA] text -> %s id=%s", to_wa_id, data.get("messages", [{}])[0].get("id"))
            return data

    async def send_image_url(self, to_wa_id: str, image_url: str, caption: str = "") -> dict[str, Any]:
        if settings.whatsapp_dry_run:
            fake_id = f"wamid.dry.img.{int(__import__('time').time() * 1000)}"
            log.info("[WA-DRY] image -> %s id=%s url=%s", to_wa_id, fake_id, image_url[:80])
            return {"messages": [{"id": fake_id}]}
        image: dict[str, Any] = {"link": image_url}
        if caption:
            image["caption"] = caption
        body = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "image",
            "image": image,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(self._messages_url(), headers=self._headers, json=body)
            r.raise_for_status()
            data = r.json()
            log.info("[WA] image -> %s url=%s", to_wa_id, image_url[:80])
            return data

    async def mark_read(self, wa_message_id: str) -> None:
        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wa_message_id,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(self._messages_url(), headers=self._headers, json=body)
        except Exception as e:
            log.warning("[WA] mark_read failed: %s", e)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Descarga media de Meta: GET /{media_id} → url; GET url → bytes."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            meta = await client.get(
                f"{self.base}/{media_id}",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            meta.raise_for_status()
            info = meta.json()
            url = info["url"]
            mime = info.get("mime_type", "application/octet-stream")
            bin_r = await client.get(url, headers={"Authorization": f"Bearer {self.token}"})
            bin_r.raise_for_status()
            return bin_r.content, mime


whatsapp_client = WhatsAppClient()
