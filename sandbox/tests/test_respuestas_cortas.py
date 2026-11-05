"""Sí, no, o no está claro: una sola forma de leer las respuestas cortas.

Había una lista de palabras por cada paso del cierre y se iban a desincronizar. Y "sí,
no" se leía como un sí: ante un mensaje contradictorio hay que preguntar, no elegir.
"""
from datetime import date

import pytest

from app.harness.checkout import advance_checkout
from app.harness.replies import MIXED, NO, OTHER, YES, short_reply
from app.harness.router import classify_rules
from app.harness.state import ConversationState

HOY = date(2026, 11, 4)


@pytest.mark.parametrize(
    "texto",
    ["si", "Sí", "SÍ!!", "sii", "sip", "ok", "okey", "dale", "claro", "bueno", "ya", "va",
     "correcto", "perfecto", "listo", "de acuerdo", "por supuesto", "claro que sí", "de una",
     "sí, confirmo", "todo ok", "todo bien", "está bien", "así es", "sí por favor",
     "si todo correcto gracias", "no hay problema", "sin problema", "👍", "✅", "👌🏻"],
)
def test_afirmaciones(texto):
    assert short_reply(texto) == YES, texto


@pytest.mark.parametrize(
    "texto",
    ["no", "No!", "nop", "nel", "negativo", "no gracias", "no, gracias", "ya no", "claro que no",
     "por supuesto que no", "mejor no", "así no", "así no es", "todavía no", "aún no", "👎", "❌"],
)
def test_negaciones(texto):
    assert short_reply(texto) == NO, texto


@pytest.mark.parametrize("texto", ["sí, no", "si no", "ok no", "no sí", "no, ok", "sí... bueno no"])
def test_lo_contradictorio_no_se_decide(texto):
    assert short_reply(texto) == MIXED, texto


@pytest.mark.parametrize(
    "texto",
    ["", "gracias", "por favor", "todo", "sí, pero cambia la dirección", "ok pero falta la tarjeta",
     "no, es San Isidro", "quisiera cambiar la fecha", "Feliz cumple mamá", "espera", "¿cuánto es?"],
)
def test_lo_demas_no_es_una_respuesta_corta(texto):
    assert short_reply(texto) == OTHER, texto


def _resumen() -> ConversationState:
    return ConversationState(
        checkout_step="summary", chosen_product_id=1, chosen_product_name="Ramo",
        date="2026-11-11", time_slot="09:00 AM a 11:00 AM",
    )


def test_el_resumen_no_decide_ante_un_mensaje_contradictorio():
    state, reply, meta = advance_checkout(_resumen(), "sí, no", today=HOY)
    assert not meta.get("create_order")
    assert state.checkout_step == "summary"
    assert "sí" in reply.casefold() and "no" in reply.casefold()
    assert state.step_retries == 1


def test_la_tarjeta_no_decide_ante_un_mensaje_contradictorio():
    state, _, _ = advance_checkout(
        ConversationState(checkout_step="card", chosen_product_id=1), "si no", today=HOY
    )
    assert state.checkout_step == "card"
    assert state.dedicatoria == ""


def test_retomar_no_decide_ante_un_mensaje_contradictorio():
    state = ConversationState(
        checkout_step="resume_confirm", checkout_resume_step="date", chosen_product_id=1,
        chosen_product_name="Ramo",
    )
    state, _, _ = advance_checkout(state, "sí, no", today=HOY)
    assert state.checkout_step == "resume_confirm"


@pytest.mark.parametrize("texto", ["no hay problema", "de una", "ya"])
def test_el_router_usa_la_misma_lectura_tras_ver_la_ficha(texto):
    state = ConversationState(
        intent_last="product_detail", chosen_product_id=1, chosen_product_name="Ramo",
    )
    assert classify_rules(texto, state).intent == "checkout"


def test_un_no_tras_ver_la_ficha_no_abre_el_cierre():
    state = ConversationState(
        intent_last="product_detail", chosen_product_id=1, chosen_product_name="Ramo",
    )
    assert classify_rules("no gracias", state).intent != "checkout"
