"""El corpus de evals, como test.

Cada caso de `evals/corpus/` es un incidente real o una regla que el negocio no
puede permitirse romper. Antes, cada bug se arreglaba con una regex nueva y nadie
sabía si el parche de hoy rompía el de la semana pasada; lo comprobaba un cliente
en WhatsApp. Ahora lo comprueba CI.

Regla de trabajo: **cada bug arreglado deja un caso aquí.**
"""
import pytest

from evals.runner import run_all

RESULTADOS = run_all()


@pytest.mark.parametrize(
    "resultado",
    RESULTADOS,
    ids=[f"{r.kind}:{r.case_id}" for r in RESULTADOS],
)
def test_corpus(resultado):
    assert resultado.passed, resultado.detail


def test_el_corpus_no_se_queda_vacio():
    """Un corpus vacío pasaría en verde sin comprobar nada."""
    assert len(RESULTADOS) >= 30
    assert {r.kind for r in RESULTADOS} == {"routing", "reply", "handoff"}
