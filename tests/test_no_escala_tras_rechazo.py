"""Una despedida cortés no es un pedido de asesor.

El incidente (L. Montes, 20-09-2026): el bot avisó que ya no tomaba pedidos
de Flores Amarillas para hoy y ofreció agendar otra fecha. El cliente
contestó *"Muchas gracias, por el momento no."* — una despedida clara, sin
pedir nada — y el bot respondió *"¡Claro! Te conecto con un asesor de
nuestro equipo 🙏 Dame un momento, en seguida continúan contigo."*.

La causa: `is_courtesy_text` tokeniza el mensaje y "el"/"momento" no están en
`_SMALL_TALK_WORDS`, así que la frase completa NO se reconocía como
cortesía. `is_small_talk` devolvía `False`, `handoff_policy` no encontraba
ninguna palabra de `_HANDOFF_FORCE_RE` ni `_SALES_CONTINUE_RE`, y caía al
`allow=True` por defecto — el mismo agujero que ya describió
`test_handoff_no_prometido.py` para otro mensaje distinto. El modelo llamó
`escalar_a_humano` por su cuenta y nadie lo vetó.
"""
from __future__ import annotations

import pytest

from app.guardrails import handoff_policy, is_small_talk


def _msg(texto: str) -> list[dict]:
    return [{"role": "user", "content": texto}]


@pytest.mark.parametrize(
    "texto",
    [
        "Muchas gracias, por el momento no.",  # el mensaje literal del incidente
        "Por el momento no, gracias",
        "Por ahora no",
        "No, por el momento no",
        "No gracias",
        "Ya no, gracias",
        "Así está bien, gracias",
        "Eso sería todo, gracias",
    ],
)
def test_despedida_cortes_no_escala(texto):
    assert handoff_policy(_msg(texto)).allow is False
    assert is_small_talk(_msg(texto)) is True


@pytest.mark.parametrize(
    "texto",
    [
        # Contiene "no" pero el resto del mensaje sigue pidiendo algo real:
        # no debe confundirse con una despedida.
        "No, mejor quiero cancelar mi pedido",
        "Por el momento no puedo pagar con tarjeta, ¿tienen Yape?",
    ],
)
def test_un_no_con_pedido_real_sigue_escalando_si_corresponde(texto):
    """El fallo caro en el otro sentido: que la regex de despedida trague un
    mensaje que sí pide algo."""
    assert is_small_talk(_msg(texto)) is False
