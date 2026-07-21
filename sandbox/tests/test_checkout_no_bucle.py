"""El cierre no puede convertirse en un bucle.

Todo este módulo nació de un chat real (21/07/2026). La clienta recibió cuatro
veces, carácter por carácter, "No pude confirmar esa fecha" y otras cuatro el
menú de horarios completo — incluso cuando escribió "Gracias" y cuando avisó
"Ya no deseo el pedido por q no entienden". La FSM era una función pura de
(paso, texto): sin memoria de sus propios fracasos, la misma entrada producía
los mismos bytes indefinidamente.
"""
from datetime import date

import pytest

from app.harness.checkout import advance_checkout, parse_schedule, recognized_window
from app.harness.state import ConversationState

HOY = date(2026, 7, 21)  # martes


def _en_paso(step: str, **kw) -> ConversationState:
    return ConversationState(
        checkout_step=step, district="Santiago De Surco", date="2026-07-21", **kw
    )


# ── Nunca la misma respuesta dos veces ────────────────────────────────

def test_no_repite_el_mismo_texto_al_no_entender():
    state = _en_paso("date")
    vistas = []
    for texto in ("cuando sea", "no se pe"):
        state, reply, meta = advance_checkout(state, texto, today=HOY)
        assert not meta.get("handoff")
        vistas.append(reply)
    assert vistas[0] != vistas[1], "el bot repitió la misma respuesta"


def test_cita_lo_que_escribio_el_cliente():
    """Que se note que hay alguien leyendo, no un formulario rebotando."""
    state = _en_paso("date")
    _, reply, _ = advance_checkout(state, "cuando sea", today=HOY)
    assert "cuando sea" in reply


def test_el_ejemplo_de_fecha_es_futuro():
    """La plantilla fija proponía '20/07'… el 21 de julio."""
    state = _en_paso("date")
    _, reply, _ = advance_checkout(state, "cuando sea", today=HOY)
    assert "20/07" not in reply
    assert "22/07" in reply


def test_al_tercer_fallo_cede_el_chat():
    state = _en_paso("date")
    for texto in ("cuando sea", "no se pe"):
        state, _, meta = advance_checkout(state, texto, today=HOY)
        assert not meta.get("handoff")
    state, reply, meta = advance_checkout(state, "asdfgh", today=HOY)
    assert meta["handoff"] is True
    assert "asesor" in reply
    # Es un rescate, no una venta: no se crea pedido temporal.
    assert not meta.get("create_order")
    assert not meta.get("escalate")


def test_avanzar_de_paso_reinicia_la_escalera():
    """Los reintentos de un paso no se arrastran al siguiente."""
    state = _en_paso("date")
    state, _, _ = advance_checkout(state, "cuando sea", today=HOY)
    assert state.step_retries == 1
    state, _, _ = advance_checkout(state, "mañana", today=HOY)
    assert state.checkout_step == "schedule"
    assert state.step_retries == 0


# ── Escuchar lo que no es una respuesta al formulario ─────────────────

@pytest.mark.parametrize(
    "texto",
    ["Ya no gracias", "ya no deseo el pedido", "mejor otro día", "olvídalo"],
)
def test_abandono_cede_el_chat_en_vez_de_repreguntar(texto):
    state = _en_paso("schedule")
    _, reply, meta = advance_checkout(state, texto, today=HOY)
    assert meta["handoff"] is True
    assert "asesor" in reply


@pytest.mark.parametrize(
    "texto",
    [
        "Ya comfirme la hora y fecha",  # sí, con la errata del chat real
        "ya te dije la fecha",
        "no entienden nada",
    ],
)
def test_queja_de_incomprension_cede_el_chat(texto):
    state = _en_paso("schedule")
    _, reply, meta = advance_checkout(state, texto, today=HOY)
    assert meta["handoff"] is True
    assert "asesor" in reply


def test_en_el_paso_de_tarjeta_un_no_sigue_siendo_un_no():
    """"mejor no" ahí responde "¿quieres tarjeta?", no abandona el pedido."""
    state = _en_paso("card")
    state, _, meta = advance_checkout(state, "mejor no", today=HOY)
    assert not meta.get("handoff")
    assert state.checkout_step == "recipient"
    assert state.dedicatoria == "Sin dedicatoria"


def test_la_cortesia_no_gasta_un_reintento():
    """Un "Gracias" no es la fecha que pedimos, pero tampoco un fallo."""
    state = _en_paso("date")
    state, reply, meta = advance_checkout(state, "Gracias", today=HOY)
    assert not meta.get("handoff")
    assert state.step_retries == 0
    assert "no pude" not in reply.casefold()


# ── Horarios: entender cómo escribe la gente ──────────────────────────

@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("De 7 a 9", "07:00 AM a 09:00 AM"),  # la respuesta real de la clienta
        ("7 a 9", "07:00 AM a 09:00 AM"),
        ("de 9 a 11", "09:00 AM a 11:00 AM"),
        ("entre 11 y 2", "11:00 AM a 02:00 PM"),
        ("a las 7", "07:00 AM a 09:00 AM"),
        ("temprano", "07:00 AM a 09:00 AM"),
        ("en la mañana", "09:00 AM a 11:00 AM"),
        ("al mediodía", "11:00 AM a 02:00 PM"),
        ("en la tarde", "02:00 PM a 05:00 PM"),
        ("tarde-noche", "04:00 PM a 07:00 PM"),
        ("1", "07:00 AM a 09:00 AM"),
        ("09 AM a 11:00 AM", "09:00 AM a 11:00 AM"),
    ],
)
def test_parse_schedule_entiende_lenguaje_natural(texto, esperado):
    assert parse_schedule(texto, "2026-07-21") == esperado


def test_franja_no_disponible_no_se_confunde_con_no_entender():
    """El viernes no hay 07:00–09:00. Decirlo es distinto a repetir el menú."""
    viernes = "2026-07-24"
    assert recognized_window("de 7 a 9") == "Mañana temprano"
    assert parse_schedule("de 7 a 9", viernes) is None

    state = _en_paso("schedule")
    state.date = viernes
    _, reply, meta = advance_checkout(state, "de 7 a 9", today=HOY)
    assert not meta.get("handoff")
    assert "Mañana temprano" in reply
    assert "no sale" in reply


# ── El chat real, de punta a punta ────────────────────────────────────

def test_el_chat_que_originó_esto_ahora_avanza():
    state = _en_paso("date")
    state, _, _ = advance_checkout(state, "Hoy dia 21 de julio", today=HOY)
    assert state.date == "2026-07-21"
    state, _, meta = advance_checkout(state, "De 7 a 9", today=HOY)
    assert state.time_slot == "07:00 AM a 09:00 AM"
    assert not meta.get("handoff")
    assert state.checkout_step == "card"
