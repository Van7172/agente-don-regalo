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


# ── Cita (responder a un mensaje) ────────────────────────────────

def test_parser_captura_el_mensaje_citado():
    payload = {
        "entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"wa_id": "519", "profile": {"name": "VK"}}],
            "messages": [{
                "from": "519", "id": "wamid.NEW", "type": "text",
                "text": {"body": "quiero este"},
                "context": {"id": "wamid.PRODUCTO"},
            }],
        }}]}]
    }
    msg = parse_webhook_payload(payload)[0]
    assert msg.quoted_wa_id == "wamid.PRODUCTO"


@pytest.mark.asyncio
async def test_la_cita_llega_al_llm_en_produccion(monkeypatch):
    """Regresión (VK, 16-07): el cliente respondió al mensaje del producto con
    "quiero este" y el bot volvió a preguntar cuál de los cinco. `_enqueue_external`
    mandaba `quoted_text=None` fijo: la cita se perdía en producción (el camino
    local sí la resolvía).
    """
    capturado = {}

    async def fake_upsert(wa_id, **kw):
        capturado["quoted_wa_id"] = kw.get("quoted_wa_id")
        return {
            "conversation_id": 1, "contact_id": 1,
            "conversation": {"mode": "AI", "bot_active": True},
            "quoted_text": "• 🎁 *Desayuno De Cumpleaños Para Ella* — S/163.20",
        }

    async def fake_setting(_k):
        return "0"

    async def fake_archive(_msg):
        return None, None

    async def fake_flush(_cid):
        return None

    monkeypatch.setattr(buf.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(buf.crm_http, "upsert_inbound", fake_upsert)
    monkeypatch.setattr(buf.crm_http, "get_setting", fake_setting)
    monkeypatch.setattr(buf, "_archive_media", fake_archive)
    monkeypatch.setattr(buf, "_flush_after_delay", fake_flush)

    msg = InboundMessage(
        wa_id="519", contact_name="VK", wa_message_id="wamid.NEW",
        message_type="text", text="quiero este", quoted_wa_id="wamid.PRODUCTO",
    )
    await buf.enqueue_inbound(msg)

    assert capturado["quoted_wa_id"] == "wamid.PRODUCTO"
    texto = " ".join(p["text"] for p in buf._buffers[1]["parts"])
    assert "respondiendo al mensaje" in texto
    assert "Desayuno De Cumpleaños Para Ella" in texto
    buf._buffers.pop(1, None)


def test_la_cita_desambigua_el_producto_entre_varios_desayunos():
    """`_name_hit` acierta con una sola palabra larga, así que entre varios
    desayunos casaban todos y quedaba ambiguo. El nombre COMPLETO desempata."""
    from app.harness.checkout import resolve_chosen_product
    from app.harness.state import ConversationState

    s = ConversationState()
    s.recent_products = [
        {"id_producto": 101, "nombre": "Desayuno Corazón Fit"},
        {"id_producto": 102, "nombre": "Desayuno De Cumpleaños Para Ella"},
        {"id_producto": 103, "nombre": "Gustito de Cumpleaños para él"},
    ]

    assert resolve_chosen_product(s, "quiero este") is None, "sin cita sigue siendo ambiguo"

    con_cita = (
        "[El cliente está respondiendo al mensaje: "
        "«• 🎁 *Desayuno De Cumpleaños Para Ella* — S/163.20»]\nquiero este"
    )
    assert resolve_chosen_product(s, con_cita) == (102, "Desayuno De Cumpleaños Para Ella")
