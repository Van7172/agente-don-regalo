"""Webhook WhatsApp Cloud API (Meta)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.channels.whatsapp.parser import parse_webhook_payload
from app.config import settings
from app.observability import (
    audit_event,
    new_trace_id,
    record_operation,
    trace_context,
)
from app.services.inbound_queue import submit_inbound

log = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        log.info("[WA] webhook verificado")
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


def _valid_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        return True  # opcional en sandbox
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.whatsapp_app_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def _summarize_payload(payload: dict) -> str:
    bits: list[str] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            field = change.get("field")
            value = change.get("value") or {}
            n_msg = len(value.get("messages") or [])
            n_st = len(value.get("statuses") or [])
            bits.append(f"field={field} messages={n_msg} statuses={n_st}")
    return "; ".join(bits) or "empty"


def _log_delivery_statuses(payload: dict) -> None:
    """Meta confirma delivered/read/failed aparte del 200 del POST de envío."""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for st in value.get("statuses") or []:
                errors = st.get("errors") or []
                err_txt = ""
                if errors:
                    parts = []
                    for e in errors:
                        parts.append(str(e.get("code") or "unknown"))
                    err_txt = "; ".join(parts)
                level = log.warning if st.get("status") == "failed" else log.info
                level(
                    "[WA-STATUS] status=%s error_codes=%s",
                    st.get("status"),
                    err_txt or "-",
                )


@router.post("/webhook")
async def receive_webhook(request: Request):
    with trace_context(new_trace_id()):
        return await _receive_webhook(request)


async def _receive_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _valid_signature(raw, sig):
        record_operation("webhook.request", "invalid_signature")
        audit_event("webhook.signature", "rejected", status_code=403)
        log.warning("[WA] firma inválida (¿WHATSAPP_APP_SECRET?)")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        record_operation("webhook.request", "invalid_json")
        audit_event("webhook.payload", "rejected", status_code=400)
        log.error("[WA] body no es JSON bytes=%s", len(raw))
        raise HTTPException(status_code=400, detail="Invalid JSON") from None

    summary = _summarize_payload(payload)
    log.info("[WA-POST] %s bytes=%s", summary, len(raw))
    _log_delivery_statuses(payload)

    messages = parse_webhook_payload(payload)
    if not messages:
        record_operation("webhook.request", "no_messages")
        audit_event("webhook.inbound", "ok", processed_count=0)
        log.info("[WA] sin mensajes inbound (%s)", summary)
        return {"status": "ok", "processed": 0, "note": summary}

    # Meta exige 200 rápido; el worker procesa y el buffer agrupa después.
    accepted = 0
    duplicates = 0
    rejected = 0
    for msg in messages:
        submission = submit_inbound(
            msg,
            trace_id=new_trace_id(msg.wa_message_id or None),
        )
        if submission.status == "accepted":
            accepted += 1
        elif submission.status == "duplicate":
            duplicates += 1
        else:
            rejected += 1

    if rejected:
        # Un 503 hace que Meta reintente. Los trabajos ya aceptados no se
        # duplicarán: la cola recuerda los pendientes y el buffer los procesados.
        log.error(
            "[WA] cola inbound no disponible accepted=%s duplicates=%s rejected=%s",
            accepted,
            duplicates,
            rejected,
        )
        record_operation("webhook.request", "rejected")
        audit_event(
            "webhook.inbound",
            "rejected",
            processed_count=accepted,
            duplicate_count=duplicates,
            rejected_count=rejected,
            status_code=503,
        )
        raise HTTPException(status_code=503, detail="Inbound queue unavailable")

    record_operation("webhook.request", "ok")
    audit_event(
        "webhook.inbound",
        "ok",
        processed_count=accepted,
        duplicate_count=duplicates,
        rejected_count=0,
    )
    return {
        "status": "ok",
        "accepted": accepted,
        "duplicates": duplicates,
    }
