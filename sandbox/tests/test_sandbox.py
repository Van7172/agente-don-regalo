"""Tests del sandbox: parser WhatsApp, gates CRM, helpers de latencia."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Asegurar que sandbox/ esté en el path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_sandbox.db")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
os.environ.setdefault("DEFAULT_TENANT_SLUG", "test-tenant")


@pytest.fixture
def sample_wa_payload():
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "contacts": [{
                        "wa_id": "51999999999",
                        "profile": {"name": "Ana"},
                    }],
                    "messages": [{
                        "from": "51999999999",
                        "id": "wamid.TEST123",
                        "timestamp": "1710000000",
                        "type": "text",
                        "text": {"body": "Hola, busco desayunos"},
                        "context": {"id": "wamid.QUOTED1"},
                    }],
                },
            }],
        }],
    }


def test_parse_webhook_text_and_quote(sample_wa_payload):
    from app.channels.whatsapp.parser import parse_webhook_payload

    msgs = parse_webhook_payload(sample_wa_payload)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.wa_id == "51999999999"
    assert m.contact_name == "Ana"
    assert m.text == "Hola, busco desayunos"
    assert m.quoted_wa_id == "wamid.QUOTED1"
    assert m.message_type == "text"


def test_bot_should_reply_gates():
    from app.crm.models import Conversation
    from app.crm.repository import bot_should_reply
    from app.config import settings

    ok = Conversation(
        tenant_id=1, contact_id=1, bot_active=True, human_support=False,
        labels=[settings.bot_active_label],
    )
    assert bot_should_reply(ok)[0] is True

    human = Conversation(
        tenant_id=1, contact_id=1, bot_active=True, human_support=True, labels=[]
    )
    assert bot_should_reply(human)[0] is False

    off = Conversation(
        tenant_id=1, contact_id=1, bot_active=False, human_support=False, labels=[]
    )
    assert bot_should_reply(off)[0] is False


def test_human_delay_bounds():
    from app.services.messenger import human_delay
    from app.config import settings

    d = human_delay("hola")
    assert settings.typing_min_delay <= d <= settings.typing_max_delay
    d2 = human_delay("x" * 10000)
    assert d2 == settings.typing_max_delay


def test_split_reply_image_and_text():
    from app.services.messenger import split_reply

    reply = "Mira esto\nhttps://cdn.example.com/foto.jpg\n¿Te gusta?"
    segs = split_reply(reply)
    types = [s["type"] for s in segs]
    assert "image" in types
    assert "text" in types


@pytest.mark.asyncio
async def test_init_db_and_tenant():
    from app.db import init_db, SessionLocal
    from app.crm import repository as repo
    from app.config import settings

    await init_db()
    async with SessionLocal() as session:
        t = await repo.ensure_default_tenant(session)
        await session.commit()
        assert t.slug == settings.default_tenant_slug
