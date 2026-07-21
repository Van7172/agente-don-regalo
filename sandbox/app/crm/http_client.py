"""Cliente HTTP hacia el CRM (crm/ PHP en hosting del cliente, o legacy Next)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    token = (settings.crm_internal_token or "").strip()
    if token:
        h["X-CRM-Token"] = token
        h["Authorization"] = f"Bearer {token}"
    else:
        log.warning("[CRM-HTTP] CRM_INTERNAL_TOKEN vacío — las llamadas al CRM fallarán con 401")
    return h


def crm_enabled() -> bool:
    return settings.crm_mode == "external" and bool(settings.crm_base_url)


def _auth_headers() -> dict[str, str]:
    """Como _headers pero sin Content-Type: para multipart y descargas."""
    return {k: v for k, v in _headers().items() if k != "Content-Type"}


async def upload_media(data: bytes, filename: str, mime: str) -> str:
    """Guarda bytes en el CRM y devuelve la clave de almacenamiento."""
    url = f"{settings.crm_base_url.rstrip('/')}/api/media"
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.post(
            url,
            headers=_auth_headers(),
            files={"file": (filename, data, mime)},
        )
        if res.status_code >= 400:
            log.error("[CRM-HTTP] upload_media -> %s body=%s", res.status_code, (res.text or "")[:300])
        res.raise_for_status()
        return str(res.json()["key"])


async def fetch_media(key: str) -> tuple[bytes, str]:
    """Descarga un medio guardado en el CRM. Devuelve (bytes, mime)."""
    url = f"{settings.crm_base_url.rstrip('/')}/media.php"
    async with httpx.AsyncClient(timeout=60.0) as client:
        res = await client.get(url, headers=_auth_headers(), params={"f": key})
        if res.status_code >= 400:
            log.error("[CRM-HTTP] fetch_media -> %s body=%s", res.status_code, (res.text or "")[:300])
        res.raise_for_status()
        mime = res.headers.get("content-type", "application/octet-stream").split(";")[0]
        return res.content, mime


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict[str, Any]:
    url = f"{settings.crm_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.request(method, url, headers=_headers(), json=json, params=params)
        if res.status_code >= 400:
            log.error(
                "[CRM-HTTP] %s %s -> %s body=%s",
                method,
                url,
                res.status_code,
                (res.text or "")[:500],
            )
        res.raise_for_status()
        return res.json()


async def upsert_inbound(
    wa_id: str,
    *,
    name: str = "",
    content: str = "",
    wa_message_id: Optional[str] = None,
    media_url: Optional[str] = None,
    quoted_text: Optional[str] = None,
    quoted_wa_id: Optional[str] = None,
) -> dict[str, Any]:
    return await _request(
        "POST",
        "/api/conversations",
        json={
            "wa_id": wa_id,
            "name": name,
            "content": content,
            "wa_message_id": wa_message_id,
            "media_url": media_url,
            "quoted_text": quoted_text,
            # El id del mensaje citado: el CRM resuelve su texto (es quien guarda
            # los mensajes) y lo devuelve en `quoted_text`.
            "quoted_wa_id": quoted_wa_id,
            "direction": "inbound",
            "sender_type": "contact",
            "role": "user",
        },
    )


async def append_outbound(
    conversation_id: int,
    content: str,
    *,
    sender_type: str = "bot",
    role: str = "assistant",
    wa_message_id: Optional[str] = None,
    media_url: Optional[str] = None,
    quoted_text: Optional[str] = None,
) -> dict[str, Any]:
    return await _request(
        "POST",
        f"/api/conversations/{conversation_id}",
        json={
            "content": content,
            "direction": "outbound",
            "sender_type": sender_type,
            "role": role,
            "wa_message_id": wa_message_id,
            "media_url": media_url,
            # El asesor respondió citando: el hilo del CRM debe mostrar la cita.
            "quoted_text": quoted_text,
        },
    )


async def get_conversation(conversation_id: int) -> dict[str, Any]:
    return await _request("GET", f"/api/conversations/{conversation_id}")


async def set_mode(conversation_id: int, mode: str, *, human_support: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"mode": mode}
    if human_support is not None:
        body["human_support"] = human_support
    return await _request(
        "PATCH",
        f"/api/conversations/{conversation_id}/mode",
        json=body,
    )


async def get_memory(phone: str) -> Optional[dict[str, Any]]:
    data = await _request("GET", f"/api/memory/{phone}")
    return data.get("memory")


async def put_memory(phone: str, patch: dict[str, Any]) -> dict[str, Any]:
    return await _request("PUT", f"/api/memory/{phone}", json=patch)


async def get_setting(key: str) -> Optional[str]:
    data = await _request("GET", "/api/settings", params={"key": key})
    value = data.get("value")
    return None if value is None else str(value)


async def get_unanswered(min_sec: int = 180, max_sec: int = 7200) -> list[dict]:
    data = await _request(
        "GET",
        "/api/watchdog/unanswered",
        params={"min_sec": min_sec, "max_sec": max_sec},
    )
    return list(data.get("data") or [])


async def list_pending_outbox() -> list[dict]:
    data = await _request("GET", "/api/outbox")
    return list(data.get("data") or [])


async def claim_outbox(outbox_id: int) -> bool:
    """Reclama la fila antes de mandarla. `False` = otro camino ya la tiene.

    El push del CRM y el drenaje periódico competían por la misma fila `pending`
    durante toda la llamada a la Cloud API, y los dos la enviaban. Quien no gana
    el claim no envía.
    """
    data = await _request("POST", "/api/outbox/claim", json={"outbox_id": outbox_id})
    return bool(data.get("claimed"))


async def mark_outbox(outbox_id: int, status: str, error: str | None = None) -> None:
    await _request(
        "PATCH",
        "/api/outbox",
        json={"outbox_id": outbox_id, "status": status, "error": error},
    )


async def put_setting(key: str, value: str) -> None:
    await _request("PUT", "/api/settings", json={key: value})
