"""Latencia con percentiles: el `max` esconde la cola.

`record_operation` guardaba `count`, `sum` y `max`. Con eso no se puede
responder la única pregunta que importa cuando alguien dice "va lento": **qué
está viendo el cliente lento**. La media la aplana el caso bueno y el máximo es
UN caso, casi siempre un pico raro. Un p95 malo con un máximo normal era
invisible.
"""
from __future__ import annotations

import pytest

from app.observability import (
    metrics_snapshot,
    record_operation,
    render_prometheus,
    reset_observability,
)
from app.observability.core import _LATENCY_BUCKETS_MS


@pytest.fixture(autouse=True)
def metricas_limpias():
    reset_observability()
    yield
    reset_observability()


def _serie(clave: str = "op:ok") -> dict:
    return metrics_snapshot()["operation_series"][clave]


def _muestras(*duraciones: float) -> None:
    for duracion in duraciones:
        record_operation("op", "ok", duration_ms=duracion)


# ── percentiles ─────────────────────────────────────────────────────────────


def test_el_p95_ve_la_cola_que_la_media_esconde():
    """El caso que motivó todo esto."""
    _muestras(*([200.0] * 95), *([8000.0] * 5))
    serie = _serie()
    media = serie["duration_ms_sum"] / serie["duration_count"]
    assert media < 700, "la media la aplana el caso bueno"
    assert serie["duration_ms_p99"] == 8000.0, "el p99 sí ve la cola"


def test_percentiles_sobre_una_distribucion_conocida():
    _muestras(*[float(n) for n in range(1, 101)])
    serie = _serie()
    assert serie["duration_ms_p95"] == pytest.approx(95.0, abs=1.0)
    assert serie["duration_ms_p99"] == pytest.approx(99.0, abs=1.0)


def test_el_percentil_nunca_supera_el_maximo_real():
    """La interpolación no conoce el máximo y se pasaba del borde del bucket.

    Con 95 muestras de 200 ms y 5 de 8 s salía un p99 de 9 s: mayor que nada que
    hubiera ocurrido nunca. En un panel eso se lee como un bug, y con razón.
    """
    _muestras(*([200.0] * 95), *([8000.0] * 5))
    serie = _serie()
    assert serie["duration_ms_p99"] <= serie["duration_ms_max"]


@pytest.mark.parametrize(
    "duraciones",
    [
        (300.0,) * 100,          # todas iguales
        (1234.0,),               # una sola muestra
        (15000.0,) * 10,         # todas por encima del último corte (+Inf)
        (0.0,) * 5,              # instantáneas
    ],
)
def test_los_percentiles_se_mantienen_dentro_del_rango(duraciones):
    _muestras(*duraciones)
    serie = _serie()
    for clave in ("duration_ms_p95", "duration_ms_p99"):
        assert 0 <= serie[clave] <= serie["duration_ms_max"], clave


def test_sin_muestras_cronometradas_el_percentil_es_cero():
    record_operation("op", "ok")  # sin duration_ms
    serie = _serie()
    assert serie["duration_ms_p95"] == 0.0
    assert serie["duration_count"] == 0


# ── la media dejó de mentir ─────────────────────────────────────────────────


def test_las_llamadas_sin_cronometrar_no_diluyen_la_media():
    """`count` y `duration_count` no son lo mismo.

    Hay operaciones que se registran sin medir tiempo (un lock ocupado, una
    entrada bloqueada por el guardrail). Dividir la suma entre `count` repartía
    el tiempo entre llamadas que nunca se cronometraron: la media salía más baja
    que la real, justo en las operaciones que mezclan ambos caminos.
    """
    _muestras(1000.0, 1000.0)
    for _ in range(8):
        record_operation("op", "ok")  # ocho sin duración

    serie = _serie()
    assert serie["count"] == 10
    assert serie["duration_count"] == 2
    assert serie["duration_ms_sum"] / serie["duration_count"] == 1000.0


# ── formato Prometheus ──────────────────────────────────────────────────────


def _lineas(salida: str, aguja: str) -> list[str]:
    return [line for line in salida.splitlines() if aguja in line]


def test_declara_histograma_y_no_summary():
    """Un summary no se agrega entre réplicas: sus cuantiles ya vienen
    calculados y promediarlos no significa nada. Los buckets sí se suman."""
    _muestras(100.0)
    assert "# TYPE donregalo_operation_duration_ms histogram" in render_prometheus()


def test_los_buckets_son_acumulados_y_en_orden():
    _muestras(30.0, 80.0, 400.0)
    valores = []
    for line in _lineas(render_prometheus(), "_duration_ms_bucket"):
        valores.append(int(line.rsplit(" ", 1)[1]))
    assert valores == sorted(valores), "un histograma acumulado no puede bajar"
    assert valores[0] == 1, "le=50 cuenta la de 30 ms"
    assert valores[-1] == 3, "+Inf cuenta todas"


def test_el_count_cuadra_con_el_bucket_infinito():
    """Si no cuadran, Prometheus considera el histograma inconsistente."""
    _muestras(10.0, 20.0, 30.0)
    for _ in range(4):
        record_operation("op", "ok")  # sin duración: no entran en el histograma

    salida = render_prometheus()
    inf = int(_lineas(salida, 'le="+Inf"')[0].rsplit(" ", 1)[1])
    count = int(_lineas(salida, "_duration_ms_count")[0].rsplit(" ", 1)[1])
    assert inf == count == 3


def test_se_emite_un_bucket_por_corte_mas_el_infinito():
    _muestras(100.0)
    assert len(_lineas(render_prometheus(), "_duration_ms_bucket")) == len(
        _LATENCY_BUCKETS_MS
    ) + 1


def test_el_maximo_sobrevive_al_histograma():
    """Un bucket no puede dar el peor caso real, y es el que se enseña cuando
    alguien se queja de un turno concreto."""
    _muestras(100.0, 7777.0)
    assert "donregalo_operation_duration_ms_max" in render_prometheus()
    assert _serie()["duration_ms_max"] == 7777.0


def test_las_operaciones_sin_duracion_siguen_contandose():
    """El contador de sucesos no depende de que se cronometre nada."""
    record_operation("guardrail.input", "blocked")
    salida = render_prometheus()
    assert (
        'donregalo_operations_total{operation="guardrail.input",outcome="blocked"} 1'
        in salida
    )
