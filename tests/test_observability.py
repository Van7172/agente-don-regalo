"""Contratos de trazabilidad, métricas y privacidad de auditoría."""
import json
import logging

import pytest
from fastapi import HTTPException

from app.channels.whatsapp.parser import InboundMessage
from app.harness.trace import Trace
from app.observability import (
    audit_event,
    current_trace_id,
    metrics_snapshot,
    record_operation,
    render_prometheus,
    reset_observability,
    trace_context,
)
from app.services.inbound_queue import InboundQueue


def _message(message_id: str) -> InboundMessage:
    return InboundMessage(
        wa_id="51999999999",
        wa_message_id=message_id,
        message_type="text",
        text="Hola",
        contact_name="Prueba",
    )


@pytest.fixture(autouse=True)
def clean_observability():
    reset_observability()
    yield
    reset_observability()


def test_contexto_de_traza_se_restaura():
    assert current_trace_id() == "-"
    with trace_context("wa-seguro") as trace_id:
        assert trace_id == "wa-seguro"
        assert current_trace_id() == "wa-seguro"
    assert current_trace_id() == "-"


def test_auditoria_descarta_contenido_y_secretos(caplog):
    caplog.set_level(logging.INFO, logger="app.audit")
    with trace_context("trace-1"):
        audit_event(
            "tool.execute",
            "ok",
            tool="buscar_productos",
            content="dirección privada",
            token="secreto",
            wa_id="51999999999",
        )

    line = next(record.message for record in caplog.records if "[audit]" in record.message)
    payload = json.loads(line.split("[audit] ", 1)[1])

    assert payload["trace_id"] == "trace-1"
    assert payload["tool"] == "buscar_productos"
    assert "content" not in payload
    assert "token" not in payload
    assert "wa_id" not in payload
    assert "dirección privada" not in line
    assert "secreto" not in line
    assert "51999999999" not in line


def test_metricas_prometheus_no_exponen_valores_libres():
    record_operation("harness.turn", "ok", duration_ms=12.5)
    record_operation("harness.turn", "ok", duration_ms=7.5)

    snapshot = metrics_snapshot()
    rendered = render_prometheus()

    assert snapshot["operation_series"]["harness.turn:ok"]["count"] == 2
    assert snapshot["operation_series"]["harness.turn:ok"]["duration_ms_sum"] == 20.0
    assert 'operation="harness.turn",outcome="ok"' in rendered
    assert "donregalo_operations_total" in rendered


def test_trace_emit_no_registra_texto_ni_datos_del_checkout(caplog):
    caplog.set_level(logging.INFO, logger="app.harness.trace")
    trace = Trace(
        conversation_id=7,
        intent="checkout",
        agent="checkout",
        user_text="Mi dirección es privada",
        handoff_reason="El cliente dijo información sensible",
        state_patch={"address": "dato privado", "phone": "999999999"},
    )

    with trace_context("trace-safe"):
        trace.done().emit()

    line = next(record.message for record in caplog.records if "[trace]" in record.message)
    payload = json.loads(line.split("[trace] ", 1)[1])

    assert payload["trace_id"] == "trace-safe"
    assert payload["input_chars"] == len("Mi dirección es privada")
    assert payload["state_patch_keys"] == ["address", "phone"]
    assert payload["handoff_reason_present"] is True
    assert "Mi dirección es privada" not in line
    assert "dato privado" not in line
    assert "999999999" not in line


@pytest.mark.asyncio
async def test_worker_propaga_trace_id_al_procesamiento():
    captured: list[str] = []

    async def handler(_msg):
        captured.append(current_trace_id())
        return {"status": "ok"}

    queue = InboundQueue(handler=handler, maxsize=2, workers=1)
    await queue.start()
    queue.submit(_message("wamid.trace"), trace_id="trace-worker")
    await queue.join()
    await queue.stop()

    assert captured == ["trace-worker"]


@pytest.mark.asyncio
async def test_endpoint_metrics_y_health_publican_el_contrato():
    from app import main

    record_operation("contract.test", "ok", duration_ms=1)

    response = await main.metrics(x_agent_token=main.settings.agent_internal_token)
    health = await main.health()

    assert b'operation="contract.test",outcome="ok"' in response.body
    assert response.media_type == "text/plain; version=0.0.4"
    assert health["observability"] == {
        "trace_context": True,
        "audit": "structured_logs",
        "metrics": "/metrics",
    }

    with pytest.raises(HTTPException) as error:
        await main.metrics(x_agent_token="incorrecto")
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_snapshot_operacional_es_json_seguro_y_requiere_token(monkeypatch):
    from app import api_internal

    async def queue_snapshot():
        return {
            "backend": "redis_streams",
            "global_pending": 3,
            "dead_letter": 1,
            "durable": True,
        }

    monkeypatch.setattr(
        api_internal,
        "inbound_queue_operational_stats",
        queue_snapshot,
    )
    record_operation("inbound.worker", "retry", duration_ms=12)

    snapshot = await api_internal.operations(
        x_agent_token=api_internal.settings.agent_internal_token
    )

    assert snapshot["queue"]["global_pending"] == 3
    assert snapshot["queue"]["dead_letter"] == 1
    assert snapshot["operations"]["operation_series"]["inbound.worker:retry"]["count"] == 1
    assert "circuits" in snapshot
    rendered = json.dumps(snapshot)
    assert "WHATSAPP_TOKEN" not in rendered
    assert "DONREGALO_MCP_TOKEN" not in rendered

    with pytest.raises(HTTPException) as error:
        await api_internal.operations(x_agent_token="incorrecto")
    assert error.value.status_code == 401
