"""
Memoria del cliente: largo plazo (atributos de contacto en Chatwoot)
y corto plazo (historial de conversación reciente).
"""
import json
import time
import logging
from datetime import date

import httpx

from app.config import settings

log = logging.getLogger(__name__)


async def get_contact_attributes(contact_id: int) -> dict:
    """Lee los custom_attributes (perfil de largo plazo) de un contacto."""
    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/contacts/{contact_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.get(url, headers={"api_access_token": settings.chatwoot_api_token})
            r.raise_for_status()
            payload = r.json().get("payload", {})
            return payload.get("custom_attributes") or {}
    except Exception as e:
        log.error("Error leyendo contacto %s: %s", contact_id, e)
        return {}


async def save_contact_attributes(contact_id: int, new_attrs: dict) -> str:
    """Fusiona y guarda datos del cliente en custom_attributes del contacto.
    Devuelve JSON string (consumido por el modelo como tool result)."""
    new_attrs = {k: v for k, v in (new_attrs or {}).items() if v not in (None, "")}
    if not new_attrs:
        return json.dumps({"ok": False, "motivo": "sin datos para guardar"})

    nota = new_attrs.pop("nota", None)

    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/contacts/{contact_id}"
    )
    try:
        current = await get_contact_attributes(contact_id)
        merged  = {**current, **new_attrs}

        if nota:
            entry    = f"[{date.today().isoformat()}] {nota}"
            historial = (current.get("notas") or "").strip()
            historial = f"{historial}\n{entry}" if historial else entry
            if len(historial) > 2000:
                historial = "…" + historial[-2000:]
            merged["notas"] = historial

        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.put(
                url,
                headers={
                    "api_access_token": settings.chatwoot_api_token,
                    "Content-Type": "application/json",
                },
                json={"custom_attributes": merged},
            )
            r.raise_for_status()

        guardado = {**new_attrs, **({"nota": nota} if nota else {})}
        log.info("[MEM] guardado contact=%s attrs=%s", contact_id, guardado)
        return json.dumps({"ok": True, "guardado": guardado})
    except Exception as e:
        log.error("Error guardando contacto %s: %s", contact_id, e)
        return json.dumps({"ok": False, "motivo": str(e)})


async def get_conversation_history(conversation_id: int) -> list[dict]:
    """Lee el transcript reciente como memoria de corto plazo (últimas 24 h).

    Excluye el turno actual del cliente (la racha final de mensajes 'user'),
    que se agrega por separado ya procesado."""
    url = (
        f"{settings.chatwoot_url}/api/v1/accounts/{settings.chatwoot_account_id}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.get(url, headers={"api_access_token": settings.chatwoot_api_token})
            r.raise_for_status()
            payload = r.json().get("payload", [])
    except Exception as e:
        log.error("Error leyendo historial %s: %s", conversation_id, e)
        return []

    payload.sort(key=lambda m: m.get("created_at") or 0)

    cutoff  = time.time() - settings.memory_window_hours * 3600
    history: list[dict] = []
    for m in payload:
        if m.get("private"):
            continue
        mtype = m.get("message_type")
        if mtype not in (0, 1):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        created = m.get("created_at")
        if isinstance(created, (int, float)) and created < cutoff:
            continue
        history.append({
            "role":    "user" if mtype == 0 else "assistant",
            "content": content,
        })

    while history and history[-1]["role"] == "user":
        history.pop()

    if len(history) > settings.memory_max_messages:
        history = history[-settings.memory_max_messages:]

    return history
