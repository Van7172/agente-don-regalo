"""Cliente HTTP hacia el CRM Next.js (fuente de verdad Opción C)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if settings.crm_internal_token:
        h["X-CRM-Token"] = settings.crm_internal_token
    return h


def crm_enabled() -> bool:
    return settings.crm_mode == "external" and bool(settings.crm_base_url)


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
        },
    )


async def get_conversation(conversation_id: int) -> dict[str, Any]:
    return await _request("GET", f"/api/conversations/{conversation_id}")


async def set_mode(conversation_id: int, mode: str) -> dict[str, Any]:
    return await _request(
        "PATCH",
        f"/api/conversations/{conversation_id}/mode",
        json={"mode": mode},
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


async def mark_outbox(outbox_id: int, status: str, error: str | None = None) -> None:
    await _request(
        "PATCH",
        "/api/outbox",
        json={"outbox_id": outbox_id, "status": status, "error": error},
    )


async def put_setting(key: str, value: str) -> None:
    await _request("PUT", "/api/settings", json={key: value})
