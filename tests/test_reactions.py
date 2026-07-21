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


# ── Responder desde el CRM (menú de clic derecho) ────────────────

@pytest.mark.asyncio
async def test_responder_del_asesor_manda_context_a_meta_y_cita_en_el_crm(monkeypatch):
    """El asesor responde a un mensaje desde el inbox: el cliente debe ver la cita
    en SU WhatsApp (context.message_id), y el hilo del CRM debe mostrarla también.
    """
    from app.services import outbox_drain as od

    enviado = {}
    guardado = {}

    async def fake_send(wa_id, content, *, reply_to=None):
        enviado["reply_to"] = reply_to
        return "wamid.OUT"

    async def fake_append(conversation_id, content, **kw):
        guardado["quoted_text"] = kw.get("quoted_text")
        return {}

    async def fake_noop(*a, **kw):
        return {}

    async def fake_claim(outbox_id):
        return True

    monkeypatch.setattr(od, "send_message", fake_send)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "claim_outbox", fake_claim)
    monkeypatch.setattr(od.crm_http, "append_outbound", fake_append)
    monkeypatch.setattr(od.crm_http, "mark_outbox", fake_noop)
    monkeypatch.setattr(od.crm_http, "set_mode", fake_noop)

    await od.deliver_outbox(
        wa_id="519",
        content="Sí, ese trae peluche",
        conversation_id=1,
        outbox_id=7,
        reply_to_wa_id="wamid.PRODUCTO",
        quoted_text="• 🎁 *Desayuno De Cumpleaños Para Ella* — S/163.20",
    )

    assert enviado["reply_to"] == "wamid.PRODUCTO", "la cita debe llegar a la Cloud API"
    assert "Desayuno De Cumpleaños Para Ella" in guardado["quoted_text"]


@pytest.mark.asyncio
async def test_un_envio_normal_no_lleva_cita(monkeypatch):
    from app.services import outbox_drain as od

    enviado = {}

    async def fake_send(wa_id, content, *, reply_to=None):
        enviado["reply_to"] = reply_to
        return "wamid.OUT"

    async def fake_noop(*a, **kw):
        return {}

    monkeypatch.setattr(od, "send_message", fake_send)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: False)
    monkeypatch.setattr(od.crm_http, "mark_outbox", fake_noop)

    await od.deliver_outbox(wa_id="519", content="hola", outbox_id=8)
    assert enviado["reply_to"] is None
