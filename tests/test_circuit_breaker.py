import asyncio

import httpx
import pytest

from app.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    circuit_breakers_snapshot,
    render_circuit_breaker_prometheus,
)


@pytest.mark.asyncio
async def test_abre_y_rechaza_sin_invocar_la_dependencia():
    breaker = CircuitBreaker("prueba", failure_threshold=2, recovery_seconds=30)
    calls = 0

    async def falla():
        nonlocal calls
        calls += 1
        raise OSError("caído")

    with pytest.raises(OSError):
        await breaker.call(falla)
    with pytest.raises(OSError):
        await breaker.call(falla)
    with pytest.raises(CircuitOpenError):
        await breaker.call(falla)

    assert calls == 2
    assert breaker.snapshot().state == "open"


@pytest.mark.asyncio
async def test_una_prueba_sana_cierra_el_circuito_semiabierto():
    now = [10.0]
    breaker = CircuitBreaker(
        "prueba",
        failure_threshold=1,
        recovery_seconds=5,
        clock=lambda: now[0],
    )

    async def falla():
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await breaker.call(falla)
    now[0] = 15.0

    assert await breaker.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
    snapshot = breaker.snapshot()
    assert snapshot.state == "closed"
    assert snapshot.consecutive_failures == 0


@pytest.mark.asyncio
async def test_fallo_de_prueba_reabre_y_reinicia_la_pausa():
    now = [0.0]
    breaker = CircuitBreaker(
        "prueba",
        failure_threshold=1,
        recovery_seconds=10,
        clock=lambda: now[0],
    )

    async def falla():
        raise ConnectionError

    with pytest.raises(ConnectionError):
        await breaker.call(falla)
    now[0] = 10.0
    with pytest.raises(ConnectionError):
        await breaker.call(falla)

    assert breaker.snapshot().state == "open"
    assert breaker.snapshot().retry_after_seconds == 10.0


@pytest.mark.asyncio
async def test_un_4xx_de_negocio_no_deteriora_el_circuito():
    breaker = CircuitBreaker("prueba", failure_threshold=1)
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(404, request=request)

    async def no_encontrado():
        raise httpx.HTTPStatusError("404", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await breaker.call(no_encontrado)

    assert breaker.snapshot().state == "closed"
    assert breaker.snapshot().consecutive_failures == 0


@pytest.mark.asyncio
async def test_cancelacion_no_cuenta_como_fallo():
    breaker = CircuitBreaker("prueba", failure_threshold=1)

    async def cancelada():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await breaker.call(cancelada)

    assert breaker.snapshot().state == "closed"


def test_snapshot_y_prometheus_exponen_todos_los_circuitos():
    snapshot = circuit_breakers_snapshot()
    assert {
        "catalog.rest",
        "crm",
        "mcp",
        "openai.embeddings",
        "openai.router",
        "openai.specialist",
        "qdrant",
    }.issubset(snapshot)
    assert all(item["state"] == "closed" for item in snapshot.values())

    prometheus = render_circuit_breaker_prometheus()
    assert 'donregalo_circuit_breaker_state{circuit="mcp"} 0' in prometheus
