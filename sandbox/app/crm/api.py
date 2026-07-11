"""API CRM para el panel de asesores."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crm import repository as repo
from app.db import get_session
from app.services.messenger import send_message as wa_send

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
async def list_conversations(session: AsyncSession = Depends(get_session)):
    tenant = await repo.ensure_default_tenant(session)
    rows = await repo.list_conversations(session, tenant.id)
    return {"data": [_conv_dict(c) for c in rows]}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int, session: AsyncSession = Depends(get_session)):
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
    await repo.set_human_support(session, conversation_id, body.value)
    await session.commit()
    return {"status": "ok", "human_support": body.value, "label": settings.human_support_label}


@router.post("/conversations/{conversation_id}/bot-active")
async def toggle_bot(
    conversation_id: int,
    body: FlagBody,
    session: AsyncSession = Depends(get_session),
):
    await repo.set_bot_active(session, conversation_id, body.value)
    await session.commit()
    return {"status": "ok", "bot_active": body.value}
