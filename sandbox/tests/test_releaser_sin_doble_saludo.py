"""Al recuperar un chat HUMAN, un solo saludo — no dos.

Chat real (sep 2026): el cliente escribió «Hola», el releaser mandó
«¡Hola de nuevo! Ya estoy aquí…» y el cierre, en el mismo turno, soltó
«¡Hola! Solo me falta esto para cerrarlo: ¿Para qué fecha…?». Dos saludos
seguidos; parece que el bot no piensa.
"""
from __future__ import annotations

import pytest

from app.channels.whatsapp.parser import InboundMessage
from app.harness.checkout import advance_checkout
from app.harness.releaser import REENGAGE_MSG
from app.harness.state import ConversationState
from app.services import buffer as buf
from datetime import date

HOY = date(2026, 9, 5)


@pytest.mark.asyncio
async def test_al_liberar_human_no_se_manda_hola_de_nuevo(monkeypatch):
    """El mensaje del cliente se procesa a continuación: ese turno YA responde.
    Mandar REENGAGE delante duplica el saludo.
    """
    enviados: list[str] = []

    async def fake_upsert(wa_id, **kw):
        return {
            "conversation_id": 42,
            "contact_id": 7,
            "conversation": {"mode": "HUMAN", "human_support": True, "bot_active": True},
        }

    async def fake_detail(_cid):
        return {"messages": []}

    async def fake_release(*a, **kw):
        return True, ConversationState(checkout_step="date")

    async def fake_send(wa_id, content, **kw):
        enviados.append(content)
        return "wamid.REENGAGE"

    async def fake_append(*a, **kw):
        return {}

    async def fake_setting(_k):
        return "0"

    async def fake_archive(_msg):
        return None, None

    async def fake_flush(_cid):
        return None

    monkeypatch.setattr(buf.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(buf.crm_http, "upsert_inbound", fake_upsert)
    monkeypatch.setattr(buf.crm_http, "get_conversation", fake_detail)
    monkeypatch.setattr(buf.crm_http, "get_setting", fake_setting)
    monkeypatch.setattr(buf.crm_http, "append_outbound", fake_append)
    monkeypatch.setattr(buf, "try_release_conversation", fake_release)
    monkeypatch.setattr(buf, "send_message", fake_send)
    monkeypatch.setattr(buf, "_archive_media", fake_archive)
    monkeypatch.setattr(buf, "_flush_after_delay", fake_flush)

    msg = InboundMessage(
        wa_id="519999",
        contact_name="X",
        wa_message_id="wamid.HOLA",
        message_type="text",
        text="Hola",
    )
    await buf.enqueue_inbound(msg)

    assert REENGAGE_MSG not in enviados, "no mandar 'Hola de nuevo' si el turno va a hablar"
    assert 42 in buf._buffers, "el Hola del cliente sí entra al buffer"
    buf._buffers.pop(42, None)


def test_hola_en_cierre_es_una_sola_respuesta_coherente():
    """Con el paso date activo, «hola» reengancha al pedido — una burbuja."""
    state = ConversationState(
        checkout_step="date",
        district="Ate",
        chosen_product_id=880,
        chosen_product_name="Ramo de 6 girasoles",
    )
    state, reply, meta = advance_checkout(state, "Hola", today=HOY)
    assert not meta.get("handoff")
    assert state.checkout_step == "date"
    assert "fecha" in reply.casefold()
    assert "hola de nuevo" not in reply.casefold()
    assert "seguimos" in reply.casefold()
    # No el formulario frío "solo me falta esto" tras un saludo: suena a robot.
    assert "solo me falta esto" not in reply.casefold()
