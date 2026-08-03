"""El ACK durable debe esperar al flush real, no solo al alta en el buffer."""
import asyncio

import pytest

from app.channels.whatsapp.parser import InboundMessage
from app.services import buffer


@pytest.mark.asyncio
async def test_waiter_se_resuelve_al_terminar_flush(monkeypatch):
    monkeypatch.setattr(buffer.settings, "buffer_seconds", 0)
    flushed = asyncio.Event()

    async def fake_flush(_conversation_id, _contact_id, _wa_id, _content, _wa_ids=None):
        await asyncio.sleep(0)
        flushed.set()

    monkeypatch.setattr(buffer, "_use_external_crm", lambda: True)
    monkeypatch.setattr(buffer, "_flush_external", fake_flush)

    completion = await buffer._append_to_buffer(
        991,
        contact_id=1,
        wa_id="51999",
        parts=[{"type": "text", "text": "Hola"}],
    )
    assert not completion.done()

    await completion
    assert flushed.is_set()


@pytest.mark.asyncio
async def test_fallo_durable_libera_wamid_para_reintento(monkeypatch):
    calls = 0

    async def fake_local(_msg):
        nonlocal calls
        calls += 1
        completion = asyncio.get_running_loop().create_future()
        if calls == 1:
            completion.set_exception(RuntimeError("flush falló"))
        else:
            completion.set_result(None)
        return {"status": "buffered"}, completion

    monkeypatch.setattr(buffer, "_use_external_crm", lambda: False)
    monkeypatch.setattr(buffer, "_enqueue_local", fake_local)
    message = InboundMessage(
        wa_id="51999",
        contact_name="Prueba",
        wa_message_id="wamid.retry",
        message_type="text",
        text="Hola",
    )

    with pytest.raises(RuntimeError):
        await buffer.enqueue_inbound(message, wait_for_completion=True)
    result = await buffer.enqueue_inbound(message, wait_for_completion=True)

    assert result["status"] == "buffered"
    assert calls == 2
