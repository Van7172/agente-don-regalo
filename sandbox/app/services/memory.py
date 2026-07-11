"""Memoria: wrappers que delegan al CRM (session-bound)."""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.crm import repository as repo


async def get_contact_attributes(session: AsyncSession, contact_id: int) -> dict:
    return await repo.get_contact_attributes(session, contact_id)


async def save_contact_attributes(session: AsyncSession, contact_id: int, new_attrs: dict) -> str:
    return await repo.save_contact_attributes(session, contact_id, new_attrs)


async def get_conversation_history(session: AsyncSession, conversation_id: int) -> list[dict]:
    return await repo.get_conversation_history(session, conversation_id)


# Compatibilidad con agent.py legacy que importa save_contact_attributes sin session.
# El agent sandbox usa una variante con session inyectada vía closure.
async def save_contact_attributes_json(contact_id: int, new_attrs: dict, session: AsyncSession) -> str:
    result = await save_contact_attributes(session, contact_id, new_attrs)
    return result if isinstance(result, str) else json.dumps(result)
