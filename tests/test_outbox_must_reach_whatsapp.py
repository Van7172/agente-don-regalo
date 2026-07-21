"""Si Meta no entrega, el CRM NO debe mostrar el mensaje del asesor como enviado.

Bug real: send_message tragaba el error y devolvía None; deliver_outbox igual
marcaba el outbox como sent y hacía append_outbound. El asesor veía la burbuja
en el inbox y el cliente no recibía nada.
"""
import pytest

from app.services import outbox_drain as od


@pytest.mark.asyncio
async def test_si_whatsapp_no_entrega_texto_no_se_marca_enviado(monkeypatch):
    acciones = []

    async def send_falla(wa_id, content, *, reply_to=None):
        return None  # Meta falló / sin id

    async def mark_outbox(outbox_id, status, error=None):
        acciones.append(("mark", outbox_id, status, error))

    async def append_outbound(*a, **kw):
        acciones.append(("append", kw))

    async def set_mode(*a, **kw):
        acciones.append(("mode", a, kw))

    async def claim_outbox(outbox_id):
        return True  # nadie más tiene la fila

    monkeypatch.setattr(od, "send_message", send_falla)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "claim_outbox", claim_outbox)
    monkeypatch.setattr(od.crm_http, "mark_outbox", mark_outbox)
    monkeypatch.setattr(od.crm_http, "append_outbound", append_outbound)
    monkeypatch.setattr(od.crm_http, "set_mode", set_mode)

    with pytest.raises(RuntimeError, match="WhatsApp"):
        await od.deliver_outbox(
            wa_id="51999999999",
            content="Hola, te escribo desde el CRM",
            conversation_id=42,
            outbox_id=7,
        )

    assert acciones == [], (
        "sin wa_message_id no se puede marcar sent ni persistir en el hilo"
    )
