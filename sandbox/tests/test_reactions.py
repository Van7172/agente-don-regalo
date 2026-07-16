"""Las reacciones (emoji) se pintan en el CRM pero NO despiertan al bot.

Regresión (16-07): una reacción llegaba como "[reaction]" (burbuja vacía) y, peor,
el bot la procesaba como "[Mensaje tipo reaction]" y respondía a un pulgar arriba.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_sandbox.db")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
os.environ.setdefault("DEFAULT_TENANT_SLUG", "test-tenant")
os.environ.setdefault("CRM_MODE", "local")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.channels.whatsapp.parser import InboundMessage, parse_webhook_payload  # noqa: E402
from app.services import buffer as buf  # noqa: E402


def _reaction_payload(emoji: str) -> dict:
    return {
        "entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"wa_id": "519", "profile": {"name": "Ana"}}],
            "messages": [{
                "from": "519", "id": "wamid.R", "type": "reaction",
                "reaction": {"message_id": "wamid.TARGET", "emoji": emoji},
            }],
        }}]}]
    }


def test_parser_extrae_el_emoji_de_la_reaccion():
    msg = parse_webhook_payload(_reaction_payload("❤️"))[0]
    assert msg.message_type == "reaction"
    assert msg.text == "❤️"


@pytest.mark.asyncio
async def test_reaccion_se_persiste_y_no_despierta_al_bot(monkeypatch):
    guardados = []

    async def fake_upsert(wa_id, *, name="", content="", wa_message_id=None, **kw):
        guardados.append(content)
        return {"conversation_id": 1, "contact_id": 1, "conversation": {}}

    monkeypatch.setattr(buf.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(buf.crm_http, "upsert_inbound", fake_upsert)

    msg = InboundMessage(
        wa_id="519", contact_name="Ana", wa_message_id="wamid.R",
        message_type="reaction", text="👍",
    )
    res = await buf.enqueue_inbound(msg)

    assert res["status"] == "reaction"        # no "buffered": el bot no corre
    assert guardados and "👍" in guardados[0]  # sí se dejó constancia en el CRM


@pytest.mark.asyncio
async def test_reaccion_retirada_no_pinta_nada(monkeypatch):
    monkeypatch.setattr(buf.crm_http, "crm_enabled", lambda: True)

    msg = InboundMessage(
        wa_id="519", contact_name="Ana", wa_message_id="wamid.R",
        message_type="reaction", text="",  # emoji vacío = reacción retirada
    )
    res = await buf.enqueue_inbound(msg)
    assert res["status"] == "ignored"
    assert res["reason"] == "reaction_removed"
