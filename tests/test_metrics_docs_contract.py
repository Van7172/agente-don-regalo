"""Toda métrica que se publica tiene que estar documentada, y con su regla de
agregación.

`/metrics` es **por réplica**. Con varias, cada serie se agrega de una forma
distinta y equivocarse da cifras falsas *sin avisar*: `donregalo_dlq_depth` se
agrega con `max` —todas las réplicas leen el mismo `XLEN` de Redis y sumarlo lo
multiplicaría por el número de pods— mientras que los tokens se agregan con
`sum`, porque cada réplica solo ve su parte de la factura.

Una métrica nueva sin documentar es una alerta que alguien escribirá mal. Este
test es el candado que evita que la documentación se quede atrás del código.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.observability import (
    record_gauge,
    record_operation,
    record_tokens,
    render_prometheus,
    reset_observability,
)

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "OBSERVABILIDAD.md"

# Sufijos que Prometheus deriva de un histograma: se documenta la métrica base,
# no cada una de sus tres caras.
_DERIVADOS = ("_bucket", "_sum", "_count")


@pytest.fixture
def metricas_publicadas() -> set[str]:
    reset_observability()
    # Una muestra de cada familia para que todas aparezcan en la salida.
    record_operation("harness.turn", "ok", duration_ms=120)
    record_gauge("dlq_depth", 3, scope="redis")
    record_tokens(agent="catalog", prompt_tokens=10, completion_tokens=2, cost_usd=0.01)
    salida = render_prometheus()
    reset_observability()
    return {m.group(0) for m in re.finditer(r"^donregalo_[a-z_0-9]+", salida, re.M)}


def _documento() -> str:
    assert DOC.is_file(), "falta docs/OBSERVABILIDAD.md"
    return DOC.read_text(encoding="utf-8")


def test_toda_metrica_publicada_esta_en_la_documentacion(metricas_publicadas):
    doc = _documento()
    faltan = []
    for metrica in sorted(metricas_publicadas):
        base = metrica
        for sufijo in _DERIVADOS:
            if base.endswith(sufijo):
                base = base[: -len(sufijo)]
                break
        if metrica not in doc and base not in doc:
            faltan.append(metrica)
    assert not faltan, (
        f"métricas sin documentar en docs/OBSERVABILIDAD.md: {faltan}. "
        "Sin su regla de agregación, alguien escribirá la alerta mal."
    )


def test_la_documentacion_fija_la_regla_de_agregacion():
    """Sin esta tabla, `dlq_depth` sumado da el número de pods, no de mensajes."""
    doc = _documento()
    assert "Cómo agregar cada una entre réplicas" in doc
    assert "por réplica" in doc
    # Las dos reglas opuestas tienen que estar dichas explícitamente.
    assert "`max`" in doc and "`sum`" in doc
    assert "donregalo_dlq_depth" in doc


def test_el_histograma_explica_por_que_no_es_un_summary():
    """Es la razón por la que el p95 del servicio existe y no es el de un pod."""
    doc = _documento()
    assert "histogram_quantile" in doc
    assert "summary" in doc
