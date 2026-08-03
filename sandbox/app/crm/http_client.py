"""Cliente HTTP hacia el CRM (crm/ PHP en hosting del cliente, o legacy Next)."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from app.config import settings
from app.observability import audit_event, record_operation
from app.resilience import circuit_breaker

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
    async def _send() -> str:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                url,
                headers=_auth_headers(),
                files={"file": (filename, data, mime)},
            )
            if res.status_code >= 400:
                log.error("[CRM-HTTP] upload_media -> %s", res.status_code)
            res.raise_for_status()
            return str(res.json()["key"])

    return await circuit_breaker("crm").call(_send)


async def fetch_media(key: str) -> tuple[bytes, str]:
    """Descarga un medio guardado en el CRM. Devuelve (bytes, mime)."""
    url = f"{settings.crm_base_url.rstrip('/')}/media.php"
    async def _send() -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.get(url, headers=_auth_headers(), params={"f": key})
            if res.status_code >= 400:
                log.error("[CRM-HTTP] fetch_media -> %s", res.status_code)
            res.raise_for_status()
            mime = res.headers.get("content-type", "application/octet-stream").split(";")[0]
            return res.content, mime

    return await circuit_breaker("crm").call(_send)


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict[str, Any]:
    url = f"{settings.crm_base_url.rstrip('/')}{path}"
    started = time.monotonic()
    status_code: int | None = None
    try:
        async def _send() -> dict[str, Any]:
            nonlocal status_code
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.request(
                    method,
                    url,
                    headers=_headers(),
                    json=json,
                    params=params,
                )
                status_code = res.status_code
                if res.status_code >= 400:
                    log.error(
                        "[CRM-HTTP] %s %s -> %s",
                        method,
                        path,
                        res.status_code,
                    )
                res.raise_for_status()
                return res.json()

        data = await circuit_breaker("crm").call(_send)
    except Exception as error:
        latency_ms = (time.monotonic() - started) * 1000
        record_operation("crm.http", "error", duration_ms=latency_ms)
        audit_event(
            "crm.http",
            "error",
            backend="crm",
            operation=f"{method}_{path}",
            latency_ms=latency_ms,
            status_code=status_code,
            error_type=type(error).__name__,
        )
        raise

    latency_ms = (time.monotonic() - started) * 1000
    record_operation("crm.http", "ok", duration_ms=latency_ms)
    audit_event(
        "crm.http",
        "ok",
        backend="crm",
        operation=f"{method}_{path}",
        latency_ms=latency_ms,
        status_code=status_code,
    )
    return data


async def upsert_inbound(
    wa_id: str,
    *,
    name: str = "",
    content: str = "",
    wa_message_id: Optional[str] = None,
    media_url: Optional[str] = None,
    quoted_text: Optional[str] = None,
    quoted_wa_id: Optional[str] = None,
    referral: Optional[dict[str, Any]] = None,
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
            # De qué anuncio viene el lead. Meta lo manda una sola vez, en el
            # primer mensaje; el CRM lo fija y no lo pisa después.
            "referral": referral,
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
    quoted_media_url: Optional[str] = None,
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
            # Si citó una foto, el texto es "[image]": hace falta la imagen.
            "quoted_media_url": quoted_media_url,
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


async def claim_embedding_jobs(limit: int = 10) -> list[dict]:
    data = await _request(
        "POST",
        "/api/embedding-jobs/claim",
        json={"limit": max(1, min(50, int(limit)))},
    )
    return list(data.get("data") or [])


async def finish_embedding_job(
    job_id: int,
    *,
    status: str,
    content_hash: str | None = None,
    embedding_model: str | None = None,
    dimensions: int | None = None,
    document_version: int | None = None,
    embedding_base64: str | None = None,
    error: str | None = None,
) -> None:
    await _request(
        "PATCH",
        f"/api/embedding-jobs/{int(job_id)}",
        json={
            "status": status,
            "content_hash": content_hash,
            "embedding_model": embedding_model,
            "dimensions": dimensions,
            "document_version": document_version,
            "embedding_base64": embedding_base64,
            "error": error,
        },
    )


async def put_setting(key: str, value: str) -> None:
    await _request("PUT", "/api/settings", json={key: value})


async def record_demand_miss(
    query: str,
    *,
    resultado: str,
    n_resultados: int = 0,
    categoria: Optional[str] = None,
    conversation_id: Optional[int] = None,
) -> None:
    """Anota una búsqueda que el catálogo no pudo satisfacer.

    Quien llama es `services.demand`, que ya lo lanza en segundo plano y se traga
    los errores: aquí no hay reintento ni degradación a propósito. Si el CRM
    todavía no conoce el endpoint devolverá 404 y se perderá la fila, que es el
    resultado correcto — al revés que el claim del outbox, donde callar dejaría
    al equipo sin poder escribir a nadie, aquí lo único en juego es un dato de
    análisis.
    """
    await _request(
        "POST",
        "/api/demand",
        json={
            "query": query,
            "resultado": resultado,
            "n_resultados": n_resultados,
            "categoria": categoria,
            "conversation_id": conversation_id,
        },
    )


# El CRM viejo no conoce /settings/cas. Se detecta una vez y se deja de intentar:
# reintentarlo en cada guardado sumaría un 404 por turno al circuit breaker.
_cas_supported: bool = True


def reset_cas_support() -> None:
    """Solo para tests: vuelve a suponer que el CRM sabe hacer CAS."""
    global _cas_supported
    _cas_supported = True


async def put_setting_cas(key: str, value: str, expected_version: int) -> bool | None:
    """Escritura condicional. True=escrito, False=otro ganó, None=CRM sin soporte.

    Los tres valores son distintos y el que llama tiene que distinguirlos: ante
    `False` hay que releer y reintentar, pero ante `None` no hay nada que
    reintentar — ese CRM no sabe hacer CAS y toca escribir a pelo, igual que
    `deliver_outbox` envía igual cuando el CRM todavía no sabe reclamar. Tratar
    `None` como `False` dejaría al agente reintentando en bucle y sin guardar.
    """
    global _cas_supported
    if not _cas_supported:
        return None
    try:
        data = await _request(
            "POST",
            "/api/settings/cas",
            json={"key": key, "value": value, "expected_version": expected_version},
        )
    except httpx.HTTPStatusError as error:
        if error.response is not None and error.response.status_code == 404:
            _cas_supported = False
            log.warning(
                "[CRM-HTTP] el CRM no expone /settings/cas; el estado se guarda "
                "sin control de versión (sube el CRM para cerrar la carrera)"
            )
            return None
        raise
    return bool(data.get("stored"))
