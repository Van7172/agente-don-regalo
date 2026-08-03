"""Una fecha contestada tarde no puede convertirse en un horario de entrega.

Chat real (03/08/2026). El cliente respondió a "¿Para qué fecha lo necesitas?"
en dos mensajes seguidos, como se escribe en WhatsApp:

    cliente → «06 de agosto 2026»
    bot     → "¿En qué horario prefieres que llegue?"   (el paso ya avanzó)
    cliente → «Jueves»
    bot     → "No logré cuadrar «Jueves» con nuestras franjas 😅"

Los dos mensajes eran UNA respuesta: el 06/08/2026 ES jueves. La aclaración la
recibió el parser de franjas, que no sabe qué es un jueves, y le gastó un
reintento de los tres que ceden el chat.

Y debajo había algo peor, silencioso: el parser de franjas sí "entendía" la otra
mitad. «06 de agosto 2026» leía el "06" como las 18:00 y «5 de agosto» caía en
el atajo del menú como la opción 5. Las dos devolvían *Tarde-noche* sin error y
sin reintento: una entrega agendada de 4 a 7 PM que nadie pidió.
"""
from datetime import date

from app.harness.checkout import advance_checkout, parse_schedule, recognized_window
from app.harness.orders import weekday_name
from app.harness.state import ConversationState

HOY = date(2026, 8, 3)  # lunes
JUEVES = "2026-08-06"


def _en_horario() -> ConversationState:
    return ConversationState(
        checkout_step="schedule", district="Santiago De Surco", date=JUEVES
    )


# ── Lo silencioso: una fecha nunca es una franja ──────────────────────

def test_una_fecha_no_se_agenda_como_tarde_noche():
    """El fallo que nadie habría visto hasta que llega el motorizado."""
    assert parse_schedule("06 de agosto 2026", JUEVES) is None
    assert recognized_window("06 de agosto 2026") is None


def test_el_atajo_del_menu_no_lee_el_dia_del_mes():
    """«5 de agosto» empieza por "5" y no es la quinta franja."""
    assert parse_schedule("5 de agosto", JUEVES) is None


def test_las_franjas_de_verdad_siguen_entrando():
    """El candado no puede comerse una respuesta legítima.

    "mañana" es el caso delicado: `normalize_fecha` lo lee como el día
    siguiente, pero en este paso la fecha ya está fija y significa la franja.
    """
    esperado = {
        "2": "09:00 AM a 11:00 AM",
        "5": "04:00 PM a 07:00 PM",
        "9 am a 11 am": "09:00 AM a 11:00 AM",
        "de 9 a 11": "09:00 AM a 11:00 AM",
        "mañana": "09:00 AM a 11:00 AM",
        "a las 5": "04:00 PM a 07:00 PM",
        "5 pm": "04:00 PM a 07:00 PM",
    }
    for texto, franja in esperado.items():
        assert parse_schedule(texto, JUEVES) == franja, texto


# ── La respuesta tardía ───────────────────────────────────────────────

def test_jueves_confirma_la_fecha_en_vez_de_reprocharla():
    state = _en_horario()
    state, reply, meta = advance_checkout(state, "Jueves", today=HOY)

    assert "No logré cuadrar" not in reply
    assert "jueves" in reply.lower()
    assert state.date == JUEVES
    assert not meta.get("handoff")


def test_una_aclaracion_no_gasta_un_reintento():
    """Es nuestro problema de timing: no se le cobra al cliente."""
    state = _en_horario()
    state, _, _ = advance_checkout(state, "Jueves", today=HOY)
    assert state.step_retries == 0

    # Y el cierre sigue vivo: la franja siguiente entra con normalidad.
    state, _, _ = advance_checkout(state, "2", today=HOY)
    assert state.time_slot == "09:00 AM a 11:00 AM"
    assert state.checkout_step == "card"


def test_otra_fecha_es_una_correccion_no_una_confirmacion():
    state = _en_horario()
    state, reply, _ = advance_checkout(state, "7 de agosto", today=HOY)

    assert state.date == "2026-08-07"
    assert "viernes" in reply.lower()
    assert state.step_retries == 0


def test_una_fecha_pasada_no_pisa_la_que_ya_teniamos():
    state = _en_horario()
    state, reply, _ = advance_checkout(state, "1 de agosto", today=HOY)

    assert state.date == JUEVES, "una fecha inválida no puede borrar la buena"
    assert "ya pasó" in reply
    assert state.step_retries == 1, "esto sí es un fallo del cliente"


def test_lo_que_no_es_ni_fecha_ni_franja_sigue_reintentando():
    """El arreglo no puede tragarse el camino de error que ya funcionaba."""
    state = _en_horario()
    state, reply, _ = advance_checkout(state, "sandía frita", today=HOY)

    assert "No logré cuadrar" in reply
    assert "sandía frita" in reply
    assert state.step_retries == 1


# ── El nombre del día ─────────────────────────────────────────────────

def test_weekday_name():
    assert weekday_name(JUEVES) == "jueves"
    assert weekday_name("2026-08-05") == "miércoles"
    assert weekday_name("") == ""
    assert weekday_name("no es una fecha") == ""
