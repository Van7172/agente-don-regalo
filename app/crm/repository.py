"""Repositorio CRM: contactos, conversaciones, mensajes, gates del bot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.crm.models import Contact, Conversation, Message, Tenant


async def ensure_default_tenant(session: AsyncSession) -> Tenant:
    result = await session.execute(
        select(Tenant).where(Tenant.slug == settings.default_tenant_slug)
    )
    tenant = result.scalar_one_or_none()
    if tenant:
        return tenant
    tenant = Tenant(
        slug=settings.default_tenant_slug,
        name=settings.default_tenant_name,
        config={},
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Optional[Tenant]:
    result = await session.execute(select(Tenant).where(Tenant.slug == slug))
    return result.scalar_one_or_none()


async def get_or_create_contact(
    session: AsyncSession,
    tenant_id: int,
    wa_id: str,
    name: str = "",
) -> Contact:
    result = await session.execute(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.wa_id == wa_id)
    )
    contact = result.scalar_one_or_none()
    if contact:
        if name and name != contact.name:
            contact.name = name
        return contact
    contact = Contact(tenant_id=tenant_id, wa_id=wa_id, name=name or "", attributes={})
    session.add(contact)
    await session.flush()
    return contact


async def get_open_conversation(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> Optional[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.contact_id == contact_id,
            Conversation.status == "open",
        )
        .order_by(Conversation.id.desc())
    )
    return result.scalars().first()


async def get_or_create_conversation(
    session: AsyncSession, tenant_id: int, contact_id: int
) -> Conversation:
    conv = await get_open_conversation(session, tenant_id, contact_id)
    if conv:
        return conv
    labels = [settings.bot_active_label]
    conv = Conversation(
        tenant_id=tenant_id,
        contact_id=contact_id,
        status="open",
        bot_active=True,
        human_support=False,
        labels=labels,
    )
    session.add(conv)
    await session.flush()
    return conv


async def add_message(
    session: AsyncSession,
    conversation_id: int,
    *,
    direction: str,
    sender_type: str,
    content: str = "",
    wa_message_id: str | None = None,
    media_url: str | None = None,
    quoted_text: str | None = None,
    raw: dict[str, Any] | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        direction=direction,
        sender_type=sender_type,
        content=content,
        wa_message_id=wa_message_id,
        media_url=media_url,
        quoted_text=quoted_text,
        raw=raw,
    )
    session.add(msg)
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return msg


def bot_should_reply(conv: Conversation) -> tuple[bool, str]:
    """Gate equivalente a labels Chatwoot."""
    if conv.human_support or settings.human_support_label in (conv.labels or []):
        return False, "soporte humano activo"
    if not conv.bot_active and settings.bot_active_label not in (conv.labels or []):
        return False, "bot not active"
    if not conv.bot_active:
        return False, "bot not active"
    return True, "ok"


async def set_human_support(session: AsyncSession, conversation_id: int, on: bool = True) -> None:
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        return
    conv.human_support = on
    labels = list(conv.labels or [])
    if on and settings.human_support_label not in labels:
        labels.append(settings.human_support_label)
    if not on and settings.human_support_label in labels:
        labels = [x for x in labels if x != settings.human_support_label]
    conv.labels = labels
    await session.flush()


async def set_bot_active(session: AsyncSession, conversation_id: int, on: bool = True) -> None:
    conv = await session.get(Conversation, conversation_id)
    if not conv:
        return
    conv.bot_active = on
    labels = list(conv.labels or [])
    if on and settings.bot_active_label not in labels:
        labels.append(settings.bot_active_label)
    if not on and settings.bot_active_label in labels:
        labels = [x for x in labels if x != settings.bot_active_label]
    conv.labels = labels
    await session.flush()


async def get_conversation_history(
    session: AsyncSession, conversation_id: int
) -> list[dict[str, str]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.memory_window_hours)
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.created_at >= cutoff)
        .order_by(Message.id.asc())
    )
    rows = list(result.scalars().all())
    history: list[dict[str, str]] = []
    for m in rows:
        content = (m.content or "").strip()
        if not content:
            continue
        if m.direction == "inbound":
            history.append({"role": "user", "content": content})
        elif m.sender_type in ("bot", "agent"):
            history.append({"role": "assistant", "content": content})

    while history and history[-1]["role"] == "user":
        history.pop()
    if len(history) > settings.memory_max_messages:
        history = history[-settings.memory_max_messages :]
    return history


async def get_contact_attributes(session: AsyncSession, contact_id: int) -> dict:
    contact = await session.get(Contact, contact_id)
    return dict(contact.attributes or {}) if contact else {}


async def save_contact_attributes(
    session: AsyncSession, contact_id: int, new_attrs: dict
) -> str:
    import json

    contact = await session.get(Contact, contact_id)
    if not contact:
        return json.dumps({"ok": False, "motivo": "contact not found"})
    attrs = dict(contact.attributes or {})
    for k, v in new_attrs.items():
        if v is None or v == "":
            continue
        if k == "nota":
            notas = list(attrs.get("notas") or [])
            notas.append({"fecha": datetime.now(timezone.utc).isoformat(), "texto": str(v)})
            attrs["notas"] = notas[-20:]
        else:
            attrs[k] = v
    contact.attributes = attrs
    await session.flush()
    return json.dumps({"ok": True, "guardado": new_attrs})


async def list_conversations(session: AsyncSession, tenant_id: int, limit: int = 50) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .options(selectinload(Conversation.contact), selectinload(Conversation.messages))
        .where(Conversation.tenant_id == tenant_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_conversation_detail(session: AsyncSession, conversation_id: int) -> Optional[Conversation]:
    result = await session.execute(
        select(Conversation)
        .options(selectinload(Conversation.contact), selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    return result.scalar_one_or_none()
