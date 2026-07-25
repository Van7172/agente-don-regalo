"""Contratos de la cola que desacopla Meta del agente."""
import asyncio

import pytest
from fastapi import HTTPException

from app.channels.whatsapp.parser import InboundMessage
from app.channels.whatsapp import webhook
from app.services.inbound_queue import InboundQueue, QueueSubmission


def _message(message_id: str) -> InboundMessage:
    return InboundMessage(
        wa_id="51999999999",
        wa_message_id=message_id,
        message_type="text",
        text="Hola",
        contact_name="Prueba",
    )


@pytest.mark.asyncio
async def test_worker_procesa_en_fifo_y_actualiza_metricas():
    processed: list[str] = []

    async def handler(msg):
        processed.append(msg.wa_message_id)
        return {"status": "ok"}

    queue = InboundQueue(handler=handler, maxsize=5, workers=1)
    await queue.start()
    assert queue.submit(_message("wamid.1")).status == "accepted"
    assert queue.submit(_message("wamid.2")).status == "accepted"

    await queue.join()
    stats = queue.stats()
    await queue.stop()

    assert processed == ["wamid.1", "wamid.2"]
    assert stats["accepted"] == 2
    assert stats["processed"] == 2
    assert stats["failed"] == 0
    assert stats["depth"] == 0


@pytest.mark.asyncio
async def test_wamid_pendiente_no_se_encola_dos_veces():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_msg):
        started.set()
        await release.wait()
        return {"status": "ok"}

    queue = InboundQueue(handler=handler, maxsize=5, workers=1)
    await queue.start()
    assert queue.submit(_message("wamid.same")).status == "accepted"
    await started.wait()

    assert queue.submit(_message("wamid.same")).status == "duplicate"
    assert queue.stats()["duplicates"] == 1

    release.set()
    await queue.join()
    await queue.stop()


@pytest.mark.asyncio
async def test_cola_llena_rechaza_para_que_meta_reintente():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_msg):
        started.set()
        await release.wait()
        return {"status": "ok"}

    queue = InboundQueue(handler=handler, maxsize=1, workers=1)
    await queue.start()
    assert queue.submit(_message("wamid.processing")).status == "accepted"
    await started.wait()
    assert queue.submit(_message("wamid.queued")).status == "accepted"

    assert queue.submit(_message("wamid.rejected")).status == "full"
    assert queue.stats()["rejected"] == 1

    release.set()
    await queue.join()
    await queue.stop()


@pytest.mark.asyncio
async def test_fallo_de_un_trabajo_no_detiene_el_worker():
    processed: list[str] = []

    async def handler(msg):
        if msg.wa_message_id == "wamid.bad":
            raise RuntimeError("fallo controlado")
        processed.append(msg.wa_message_id)
        return {"status": "ok"}

    queue = InboundQueue(handler=handler, maxsize=5, workers=1)
    await queue.start()
    queue.submit(_message("wamid.bad"))
    queue.submit(_message("wamid.good"))

    await queue.join()
    stats = queue.stats()
    await queue.stop()

    assert processed == ["wamid.good"]
    assert stats["failed"] == 1
    assert stats["processed"] == 1


@pytest.mark.asyncio
async def test_cola_detenida_no_acepta_trabajos():
    async def handler(_msg):
        return {"status": "ok"}

    queue = InboundQueue(handler=handler)

    assert queue.submit(_message("wamid.off")).status == "unavailable"
    assert queue.stats()["rejected"] == 1


class _Request:
    headers: dict[str, str] = {}

    async def body(self) -> bytes:
        return b"{}"


@pytest.mark.asyncio
async def test_webhook_confirma_solo_los_trabajos_aceptados(monkeypatch):
    messages = [_message("wamid.1"), _message("wamid.2")]
    submissions = iter([QueueSubmission("accepted"), QueueSubmission("duplicate")])
    monkeypatch.setattr(webhook, "_valid_signature", lambda *_args: True)
    monkeypatch.setattr(webhook, "parse_webhook_payload", lambda _payload: messages)
    monkeypatch.setattr(
        webhook,
        "submit_inbound",
        lambda _msg, **_kwargs: next(submissions),
    )

    response = await webhook.receive_webhook(_Request())

    assert response == {"status": "ok", "accepted": 1, "duplicates": 1}


@pytest.mark.asyncio
async def test_webhook_devuelve_503_si_la_cola_no_puede_aceptar(monkeypatch):
    monkeypatch.setattr(webhook, "_valid_signature", lambda *_args: True)
    monkeypatch.setattr(
        webhook,
        "parse_webhook_payload",
        lambda _payload: [_message("wamid.full")],
    )
    monkeypatch.setattr(
        webhook,
        "submit_inbound",
        lambda _msg, **_kwargs: QueueSubmission("full"),
    )

    with pytest.raises(HTTPException) as error:
        await webhook.receive_webhook(_Request())

    assert error.value.status_code == 503
