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
    tipos = {r.kind for r in RESULTADOS}
    assert {"routing", "reply", "handoff"} <= tipos


def test_el_corpus_adversarial_cubre_las_cinco_capas():
    """Las reglas de seguridad del CORE tienen que ser verificables, no confiables.

    El CORE le PIDE al modelo que no revele el prompt, que no obedezca al
    contenido no confiable y que jamás dé datos de otro cliente — pero un commit
    compuso una vez el system message sin el bloque de RESTRICCIONES y el bot
    corrió sin ninguna de esas reglas. Que exista al menos un ataque por capa es
    lo que impide que la seguridad vuelva a depender de que el modelo obedezca.
    """
    capas = {r.kind for r in RESULTADOS if r.kind.startswith("adv:")}
    assert capas == {
        "adv:injection",     # el cliente ataca directo
        "adv:tool_result",   # el catálogo o el RAG traen el ataque dentro
        "adv:tool_privacy",  # PII de terceros en un resultado de tool
        "adv:history",       # PII que se arrastra por el hilo
        "adv:profile",       # PII que se inyectaba al system prompt
        "adv:reply",         # la última barrera antes del cliente
    }
