"""API CRM del sandbox: local (SQLite) o proxy al CRM PHP (external)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crm import http_client as crm_http
from app.crm import repository as repo
from app.db import SessionLocal, get_session
from app.services.messenger import send_message as wa_send

log = logging.getLogger(__name__)
router = APIRouter(prefix="/crm", tags=["crm"])


class SendBody(BaseModel):
    text: str


class FlagBody(BaseModel):
    value: bool = True


def _conv_dict(c) -> dict:
    contact = c.contact
    last = ""
    if c.messages:
        last = (c.messages[-1].content or "")[:120]
    return {
        "id": c.id,
        "status": c.status,
        "bot_active": c.bot_active,
        "human_support": c.human_support,
        "labels": c.labels or [],
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "contact": {
            "id": contact.id if contact else None,
            "wa_id": contact.wa_id if contact else None,
            "name": contact.name if contact else None,
        },
        "last_message": last,
    }


@router.get("/conversations")
async def list_conversations():
    if crm_http.crm_enabled():
        try:
            return await crm_http._request("GET", "/api/conversations")
        except Exception as exc:
            log.exception("[CRM] proxy list conversations failed: %s", exc)
            raise HTTPException(
                502,
                "CRM externo no disponible. Revisa CRM_BASE_URL y CRM_INTERNAL_TOKEN "
                "(debe coincidir con config.php del CRM PHP). Panel: "
                f"{settings.crm_base_url}/",
            ) from exc

    async with SessionLocal() as session:
        tenant = await repo.ensure_default_tenant(session)
        rows = await repo.list_conversations(session, tenant.id)
        return {"data": [_conv_dict(c) for c in rows]}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int):
    if crm_http.crm_enabled():
        try:
            return await crm_http._request("GET", f"/api/conversations/{conversation_id}")
        except Exception as exc:
            log.exception("[CRM] proxy get conversation failed: %s", exc)
            raise HTTPException(502, "CRM externo no disponible") from exc

    async with SessionLocal() as session:
        c = await repo.get_conversation_detail(session, conversation_id)
        if not c:
            raise HTTPException(404, "Conversation not found")
        messages = [
            {
                "id": m.id,
                "direction": m.direction,
                "sender_type": m.sender_type,
                "content": m.content,
                "media_url": m.media_url,
                "quoted_text": m.quoted_text,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in (c.messages or [])
        ]
        return {"conversation": _conv_dict(c), "messages": messages}


@router.post("/conversations/{conversation_id}/send")
async def agent_send(
    conversation_id: int,
    body: SendBody,
    session: AsyncSession = Depends(get_session),
):
    if crm_http.crm_enabled():
        try:
            detail = await crm_http.get_conversation(conversation_id)
            conv = detail.get("conversation") or {}
            wa_id = (conv.get("contact") or {}).get("wa_id")
            if not wa_id:
                raise HTTPException(404, "Conversation not found")
            wa_mid = await wa_send(wa_id, body.text)
            await crm_http.append_outbound(
                conversation_id,
                body.text,
                sender_type="agent",
                role="human",
                wa_message_id=wa_mid,
            )
            await crm_http.set_mode(conversation_id, "HUMAN")
            return {"status": "ok", "wa_message_id": wa_mid}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("[CRM] proxy send failed: %s", exc)
            raise HTTPException(502, "CRM externo no disponible") from exc

    c = await repo.get_conversation_detail(session, conversation_id)
    if not c or not c.contact:
        raise HTTPException(404, "Conversation not found")
    wa_mid = await wa_send(c.contact.wa_id, body.text)
    await repo.add_message(
        session,
        conversation_id,
        direction="outbound",
        sender_type="agent",
        content=body.text,
        wa_message_id=wa_mid,
    )
    await session.commit()
    return {"status": "ok", "wa_message_id": wa_mid}


@router.post("/conversations/{conversation_id}/human-support")
async def toggle_human(
    conversation_id: int,
    body: FlagBody,
    session: AsyncSession = Depends(get_session),
):
    if crm_http.crm_enabled():
        try:
            await crm_http.set_mode(conversation_id, "HUMAN" if body.value else "AI")
            return {
                "status": "ok",
                "human_support": body.value,
                "label": settings.human_support_label,
            }
        except Exception as exc:
            raise HTTPException(502, "CRM externo no disponible") from exc

    await repo.set_human_support(session, conversation_id, body.value)
    await session.commit()
    return {"status": "ok", "human_support": body.value, "label": settings.human_support_label}


@router.post("/conversations/{conversation_id}/bot-active")
async def toggle_bot(
    conversation_id: int,
    body: FlagBody,
    session: AsyncSession = Depends(get_session),
):
    if crm_http.crm_enabled():
        try:
            await crm_http._request(
                "PATCH",
                f"/api/conversations/{conversation_id}/mode",
                json={"bot_active": body.value},
            )
            return {"status": "ok", "bot_active": body.value}
        except Exception as exc:
            raise HTTPException(502, "CRM externo no disponible") from exc

    await repo.set_bot_active(session, conversation_id, body.value)
    await session.commit()
    return {"status": "ok", "bot_active": body.value}
