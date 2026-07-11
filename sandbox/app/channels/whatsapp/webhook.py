"""Webhook WhatsApp Cloud API (Meta)."""
from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.channels.whatsapp.parser import parse_webhook_payload
from app.config import settings
from app.services.buffer import enqueue_inbound

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


@router.post("/webhook")
async def receive_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _valid_signature(raw, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    messages = parse_webhook_payload(payload)
    results = []
    for msg in messages:
        log.info(
            "[WA-IN] from=%s type=%s id=%s text=%r",
            msg.wa_id, msg.message_type, msg.wa_message_id, (msg.text or "")[:80],
        )
        results.append(await enqueue_inbound(msg))
    return {"status": "ok", "processed": len(results), "results": results}
