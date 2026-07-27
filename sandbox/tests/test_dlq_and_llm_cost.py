"""Los dos ojos que faltaban: la DLQ muda y el gasto ciego.

- **A3.** La cola de descartados existía desde el principio y nadie la miraba. Un
  mensaje que cae ahí es un cliente que escribió y al que no contestó nunca
  nadie: ni el bot (se rindió tras N reintentos) ni un humano (el fallo ocurre
  ANTES del CRM, así que ni siquiera aparece en el inbox).
- **B4.** El historial está acotado, pero nadie medía cuántos tokens gastaba cada
  agente. Un prompt que engorda solo se notaba en la factura, un mes tarde y sin
  saber quién lo causó.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.observability import (
    agent_context,
    collect_turn_usage,
    current_agent,
    estimate_cost_usd,
    metrics_snapshot,
    record_gauge,
    record_llm_usage,
    record_tokens,
    render_prometheus,
    reset_observability,
    usage_from_response,
)
from app.services import watchdog


@pytest.fixture(autouse=True)
def metricas_limpias():
    reset_observability()
    yield
    reset_observability()


def _respuesta(prompt=0, completion=0, cached=0):
    usage = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return {"choices": [{"message": {"content": "hola"}}], "usage": usage}


# ── B4: tokens y coste ──────────────────────────────────────────────────────


def test_extrae_el_uso_de_una_respuesta_de_openai():
    assert usage_from_response(_respuesta(1200, 90, 1024)) == {
        "prompt_tokens": 1200,
        "completion_tokens": 90,
        "cached_tokens": 1024,
    }


@pytest.mark.parametrize(
    "respuesta",
    [None, {}, {"usage": None}, {"usage": []}, "no soy un dict", {"usage": {"prompt_tokens": "x"}}],
)
def test_una_respuesta_rara_no_rompe_la_contabilidad(respuesta):
    """Medir el gasto no puede tumbar un turno que ya se respondió bien."""
    assert usage_from_response(respuesta)["prompt_tokens"] == 0
    assert record_llm_usage("catalog", "gpt-4o-mini", respuesta)["prompt_tokens"] == 0


def test_los_tokens_se_cuentan_por_agente():
    record_llm_usage("catalog", "gpt-4o-mini", _respuesta(1000, 50))
    record_llm_usage("concierge", "gpt-4o-mini", _respuesta(200, 30))
    tokens = metrics_snapshot()["llm_tokens"]
    assert tokens["catalog:prompt"] == 1000
    assert tokens["concierge:prompt"] == 200


def test_sin_tarifario_se_cuentan_tokens_pero_no_dinero(monkeypatch):
    """Un precio hardcodeado caduca en silencio y da una cifra falsa.

    Los tokens son exactos y no caducan; el dinero solo se calcula si alguien
    declaró `LLM_PRICES`.
    """
    monkeypatch.setattr(settings, "llm_prices", {}, raising=False)
    record_llm_usage("catalog", "gpt-4o-mini", _respuesta(1000, 100))
    snapshot = metrics_snapshot()
    assert snapshot["llm_tokens"]["catalog:prompt"] == 1000
    assert snapshot["llm_cost_usd"] == {}


def test_con_tarifario_se_calcula_el_coste(monkeypatch):
    monkeypatch.setattr(
        settings, "llm_prices", {"gpt-4o-mini": {"in": 0.15, "out": 0.60}}, raising=False
    )
    # 1.000 in + 1.000 out = 0.15 + 0.60
    assert estimate_cost_usd("gpt-4o-mini", {"prompt_tokens": 1000, "completion_tokens": 1000}) == pytest.approx(0.75)


def test_los_tokens_en_cache_se_cobran_a_su_tarifa(monkeypatch):
    """El CORE va idéntico en todos los agentes: la caché es la mitad del ahorro."""
    monkeypatch.setattr(
        settings,
        "llm_prices",
        {"m": {"in": 1.0, "out": 2.0, "cached_in": 0.5}},
        raising=False,
    )
    coste = estimate_cost_usd(
        "m", {"prompt_tokens": 2000, "cached_tokens": 1000, "completion_tokens": 0}
    )
    # 1.000 frescos a 1.0 + 1.000 cacheados a 0.5 = 1.0 + 0.5
    assert coste == pytest.approx(1.5)


def test_sin_tarifa_de_cache_se_estima_por_lo_alto(monkeypatch):
    """Nunca prometemos un ahorro que no sabemos si existe."""
    monkeypatch.setattr(settings, "llm_prices", {"m": {"in": 1.0, "out": 0.0}}, raising=False)
    coste = estimate_cost_usd(
        "m", {"prompt_tokens": 2000, "cached_tokens": 1000, "completion_tokens": 0}
    )
    assert coste == pytest.approx(2.0)


def test_un_modelo_desconocido_no_inventa_precio(monkeypatch):
    monkeypatch.setattr(settings, "llm_prices", {"otro": {"in": 9.0}}, raising=False)
    assert estimate_cost_usd("gpt-4o-mini", {"prompt_tokens": 1000}) == 0.0


def test_el_gasto_se_atribuye_al_agente_del_contexto():
    """Sin contexto no se sabría qué prompt engordó: todo sería 'specialist'."""
    assert current_agent() == "specialist"
    with agent_context("checkout"):
        assert current_agent() == "checkout"
        record_llm_usage(current_agent(), "m", _respuesta(10, 1))
    assert current_agent() == "specialist"
    assert "checkout:prompt" in metrics_snapshot()["llm_tokens"]


def test_el_acumulador_del_turno_suma_todas_las_llamadas():
    """Un turno hace varias llamadas (rondas de tools); interesa el total."""
    with collect_turn_usage() as usage:
        record_tokens(agent="catalog", prompt_tokens=100, completion_tokens=20, cached_tokens=64)
        record_tokens(agent="catalog", prompt_tokens=300, completion_tokens=40)
    assert usage == {"prompt": 400, "completion": 60, "cached": 64, "calls": 2}


def test_fuera_de_un_turno_no_se_acumula_nada():
    record_tokens(agent="catalog", prompt_tokens=10)
    with collect_turn_usage() as usage:
        pass
    assert usage["calls"] == 0


def test_los_tokens_salen_en_prometheus(monkeypatch):
    monkeypatch.setattr(settings, "llm_prices", {"m": {"in": 1.0, "out": 1.0}}, raising=False)
    record_llm_usage("catalog", "m", _respuesta(1000, 1000))
    salida = render_prometheus()
    assert 'donregalo_llm_tokens_total{agent="catalog",type="prompt"} 1000' in salida
    assert 'donregalo_llm_cost_usd_total{agent="catalog"} 2.000000' in salida


def test_la_traza_del_turno_lleva_lo_que_costo():
    from app.harness.trace import Trace

    trace = Trace(intent="catalog_search").with_usage(
        {"prompt": 1200, "completion": 90, "cached": 1024, "calls": 2}
    )
    payload = trace.to_dict()
    assert payload["prompt_tokens"] == 1200
    assert payload["llm_calls"] == 2


def test_la_traza_sin_uso_no_revienta():
    from app.harness.trace import Trace

    assert Trace().with_usage(None).prompt_tokens == 0


# ── A3: la DLQ deja de estar muda ───────────────────────────────────────────


@pytest.fixture
def cola(monkeypatch):
    estado = {"dead_letter": 0, "backend": "redis"}

    async def fake_stats():
        return dict(estado)

    import app.services.inbound_queue as queue_mod

    monkeypatch.setattr(queue_mod, "inbound_queue_operational_stats", fake_stats)
    return estado


@pytest.fixture
def avisos(monkeypatch):
    enviados: list[str] = []

    async def fake_alert(text: str) -> bool:
        enviados.append(text)
        return True

    async def sin_cooldown(_clave: str) -> bool:
        return False

    async def no_marcar(_clave: str) -> None:
        return None

    monkeypatch.setattr(watchdog, "_send_alert", fake_alert)
    monkeypatch.setattr(watchdog, "_en_cooldown", sin_cooldown)
    monkeypatch.setattr(watchdog, "_marcar", no_marcar)
    return enviados


@pytest.mark.asyncio
async def test_la_dlq_vacia_no_molesta_a_nadie(cola, avisos):
    await watchdog.check_dlq()
    assert avisos == []
    assert metrics_snapshot()["gauges"]["dlq_depth:redis"] == 0


@pytest.mark.asyncio
async def test_un_solo_mensaje_perdido_ya_avisa(cola, avisos):
    """No hay una cantidad 'sana' de clientes sin atender."""
    cola["dead_letter"] = 1
    await watchdog.check_dlq()
    assert len(avisos) == 1
    assert "1 mensaje" in avisos[0]


@pytest.mark.asyncio
async def test_el_umbral_es_configurable(cola, avisos, monkeypatch):
    monkeypatch.setattr(settings, "dlq_alert_threshold", 5, raising=False)
    cola["dead_letter"] = 4
    await watchdog.check_dlq()
    assert avisos == []
    cola["dead_letter"] = 5
    await watchdog.check_dlq()
    assert len(avisos) == 1


@pytest.mark.asyncio
async def test_la_profundidad_queda_como_metrica(cola, avisos):
    """Prometheus tiene que poder alertar aunque el WhatsApp del aviso esté caído."""
    cola["dead_letter"] = 7
    await watchdog.check_dlq()
    assert 'donregalo_dlq_depth{scope="redis"} 7' in render_prometheus()
    assert "# TYPE donregalo_dlq_depth gauge" in render_prometheus()


@pytest.mark.asyncio
async def test_en_cooldown_no_repite_el_aviso(cola, avisos, monkeypatch):
    """Un aviso cada 5 minutos se silencia y deja de servir para nada."""
    async def en_cooldown(_clave: str) -> bool:
        return True

    monkeypatch.setattr(watchdog, "_en_cooldown", en_cooldown)
    cola["dead_letter"] = 3
    await watchdog.check_dlq()
    assert avisos == []
    # La métrica sí se actualiza: el cooldown silencia el WhatsApp, no el gauge.
    assert metrics_snapshot()["gauges"]["dlq_depth:redis"] == 3


@pytest.mark.asyncio
async def test_si_la_cola_no_responde_el_watchdog_sigue_vivo(monkeypatch, avisos):
    """Best-effort: el watchdog nunca puede tumbar el agente."""
    async def revienta():
        raise RuntimeError("redis caído")

    import app.services.inbound_queue as queue_mod

    monkeypatch.setattr(queue_mod, "inbound_queue_operational_stats", revienta)
    await watchdog.check_dlq()
    assert avisos == []


@pytest.mark.asyncio
async def test_el_tick_del_watchdog_incluye_la_dlq(monkeypatch):
    """Que la función exista no sirve de nada si nadie la llama."""
    llamada = {"dlq": False}

    async def marcar():
        llamada["dlq"] = True

    for nombre in (
        "check_mute",
        "check_unattended_sales",
        "check_human_abandoned",
        "check_balance",
        "check_fallback_spike",
        "daily_audit",
    ):
        async def noop():
            return None

        monkeypatch.setattr(watchdog, nombre, noop)
    monkeypatch.setattr(watchdog, "check_dlq", marcar)

    await watchdog._tick()
    assert llamada["dlq"], "el tick del watchdog no vigila la DLQ"


def test_un_gauge_se_sobrescribe_no_se_acumula():
    """La pregunta es cuánto hay AHORA, no cuánto hubo alguna vez."""
    record_gauge("dlq_depth", 5, scope="redis")
    record_gauge("dlq_depth", 2, scope="redis")
    assert metrics_snapshot()["gauges"]["dlq_depth:redis"] == 2
