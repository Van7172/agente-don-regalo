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
os.environ.setdefault("CRM_MODE", "local")
os.environ.setdefault("WATCHDOG_ENABLED", "0")


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


def test_split_reply_url_pegada_a_vineta():
    """Regresión desayunos: el LLM pega URL + nombre en la misma línea."""
    from app.services.messenger import split_reply

    reply = (
        "Perfecto — aquí tienes opciones:\n"
        "https://donregalo.pe/imgs/a.jpg • 🎁 *Gustito* — S/75 ($22)\n"
        "1. https://donregalo.pe/imgs/b.webp\n"
        "• 🎁 *Otro Desayuno* — S/80 ($24)\n"
        "https://donregalo.pe/imgs/c.jpg?v=1 *Tercero* — S/90 ($26)\n"
        "\n¿Quieres más detalles de alguno?"
    )
    segs = split_reply(reply)
    images = [s for s in segs if s["type"] == "image"]
    assert len(images) == 3
    assert images[0]["url"].endswith("a.jpg")
    assert "Gustito" in (images[0].get("caption") or "")
    assert images[1]["url"].endswith("b.webp")
    assert "c.jpg" in images[2]["url"]
    # No debe quedar un bloque de texto con URLs crudas
    for s in segs:
        if s["type"] == "text":
            assert "https://" not in s["text"] or "donregalo" not in s["text"]


def test_split_reply_numbered_only_url():   
    from app.services.messenger import split_reply

    reply = "https://cdn.example.com/x.png\n• 🎁 *Solo* — S/10 ($3)"
    segs = split_reply(reply)
    assert segs[0]["type"] == "image"
    assert segs[0]["url"].endswith("x.png")


def test_dedupe_products_same_package_no_repeat():
    """Regresión: mismo producto en texto suelto y otra vez bajo otra imagen."""
    from app.services.messenger import _product_key, dedupe_products_in_reply, split_reply

    reply = (
        "• 🎁 *Peluche Kitty Sunshine* — S/ 95.20 ($28.00)\n"
        "  Peluche Kitty Sunshine, diseño tierno.\n"
        "\n"
        "https://cdn.example.com/oso.jpg\n"
        "• 🎁 *Peluche Kitty Sunshine* — S/ 95.20 ($28.00)\n"
        "  Peluche Kitty Sunshine, diseño tierno.\n"
        "• 🎁 *Osito Encantador* — S/ 107.37 ($31.58)\n"
        "  Osito cariñoso, pelaje antialérgico y frase bordada.\n"
        "\n"
        "¿Quieres más detalles de alguno? 😊"
    )
    cleaned = dedupe_products_in_reply(reply)
    keys = [_product_key(line) for line in cleaned.split("\n") if _product_key(line)]
    assert keys.count("peluche kitty sunshine") == 1
    assert "osito encantador" in keys

    segs = split_reply(reply)
    blob = "\n".join(
        (s.get("caption") or s.get("text") or "") for s in segs
    )
    seg_keys = [_product_key(line) for line in blob.split("\n") if _product_key(line)]
    assert seg_keys.count("peluche kitty sunshine") == 1
    assert "osito encantador" in seg_keys


def test_dedupe_keeps_distinct_products():
    from app.services.messenger import split_reply

    reply = (
        "https://cdn.example.com/a.jpg\n"
        "• 🎁 *Peluche Oso Loquito de Amor* — S/64.60 ($19.00)\n"
        "  En caja con tarjeta.\n"
        "\n"
        "https://cdn.example.com/b.jpg\n"
        "• 🎁 *Peluche Osita Lotso Dormida* — S/81.60 ($24.00)\n"
        "  Lotso dormido 35 cm.\n"
    )
    segs = split_reply(reply)
    assert len([s for s in segs if s["type"] == "image"]) == 2
    blob = "\n".join(s.get("caption", "") for s in segs if s["type"] == "image")
    assert "loquito" in blob.casefold()
    assert "lotso" in blob.casefold()


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
