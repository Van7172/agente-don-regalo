"""El mensaje de este turno no puede llegarle al modelo dos veces.

El historial sale del CRM, y el CRM ya tiene guardados los mensajes del turno:
se persisten al entrar por el webhook, ANTES de que el buffer los agrupe. Así
que alguien tiene que quitarlos — y ese alguien existía por duplicado, haciendo
cosas distintas en cada copia:

    app/crm/repository.py   while history[-1] == "user": pop()   → los quita TODOS
    app/services/buffer.py  if history[-1] == "user": [:-1]      → quita UNO

Producción corre `CRM_MODE=external`, o sea que el que estaba mal era el que
está en el aire: en cuanto un turno fusionaba dos mensajes —por el debounce o
por una preempción— el primero le llegaba al modelo dos veces.

Y el bucle del camino local tenía el defecto simétrico: a un cliente que
escribió tres veces sin que el bot llegara a contestar le borraba el backlog
entero del contexto. Ninguna de las dos copias distinguía "esto ya va en el
turno" de "esto se quedó sin responder"; para eso está `wa_message_id`.
"""
import pytest

from app.services.history import WA_ID_KEY, drop_current_turn


def _u(texto: str, wa_id: str | None = None) -> dict:
    return {"role": "user", "content": texto, WA_ID_KEY: wa_id}


def _a(texto: str) -> dict:
    return {"role": "assistant", "content": texto}


# ── El caso que motivó todo ───────────────────────────────────────────

def test_un_turno_fusionado_no_deja_ninguno_duplicado():
    """Dos mensajes del cliente en el mismo turno: ninguno sigue en el historial."""
    historial = [
        _a("¿Para qué fecha lo necesitas? 📅"),
        _u("06 de agosto 2026", "wamid.1"),
        _u("Jueves", "wamid.2"),
    ]

    quedan = drop_current_turn(historial, turn_wa_ids=["wamid.1", "wamid.2"])

    assert quedan == [{"role": "assistant", "content": "¿Para qué fecha lo necesitas? 📅"}]


def test_el_turno_de_un_solo_mensaje_se_comporta_como_siempre():
    historial = [_a("¿En qué te ayudo?"), _u("quiero un regalo", "wamid.9")]

    assert drop_current_turn(historial, turn_wa_ids=["wamid.9"]) == [
        {"role": "assistant", "content": "¿En qué te ayudo?"}
    ]


# ── Lo que el bucle de antes se comía ─────────────────────────────────

def test_el_backlog_sin_responder_se_queda_en_el_historial():
    """El defecto simétrico: no todo mensaje de usuario final es del turno.

    El bot estuvo caído, el cliente escribió tres veces y solo la última entra
    en este turno. Las otras dos SON contexto y el `while` de antes las borraba.
    """
    historial = [
        _a("¡Hola! Soy Don Regalo 🎁"),
        _u("hola?", "wamid.1"),
        _u("sigues ahí?", "wamid.2"),
        _u("quiero un desayuno", "wamid.3"),
    ]

    quedan = drop_current_turn(historial, turn_wa_ids=["wamid.3"])

    assert [m["content"] for m in quedan] == [
        "¡Hola! Soy Don Regalo 🎁",
        "hola?",
        "sigues ahí?",
    ]


def test_quita_el_del_turno_este_donde_este():
    """La coincidencia es por id, no por posición."""
    historial = [
        _u("quiero flores", "wamid.1"),
        _a("Te muestro estas 🌹"),
        _u("gracias", "wamid.2"),
    ]

    quedan = drop_current_turn(historial, turn_wa_ids=["wamid.1"])

    assert [m["content"] for m in quedan] == ["Te muestro estas 🌹", "gracias"]


# ── Respaldo cuando los ids no cuadran ────────────────────────────────

def test_sin_ids_descarta_uno_como_hasta_ahora():
    historial = [_a("¿En qué te ayudo?"), _u("quiero un regalo")]

    assert drop_current_turn(historial) == [
        {"role": "assistant", "content": "¿En qué te ayudo?"}
    ]


def test_con_ids_que_no_estan_en_el_historial_descarta_tantos_como_trae_el_turno():
    """Un CRM que no devuelve `wa_message_id`: el respaldo va ACOTADO.

    Se descartan dos porque el turno trae dos, no "todos los que haya". Esa
    diferencia es justo lo que evita volver a comerse el backlog.
    """
    historial = [
        _u("hola?", None),
        _a("¡Hola!"),
        _u("06 de agosto 2026", None),
        _u("Jueves", None),
    ]

    quedan = drop_current_turn(historial, turn_wa_ids=["wamid.1", "wamid.2"])

    assert [m["content"] for m in quedan] == ["hola?", "¡Hola!"]


def test_el_respaldo_nunca_pasa_de_un_mensaje_del_asistente():
    """Descartar de más sería borrar lo que el bot ya dijo."""
    historial = [_u("uno", None), _a("respondí"), _u("dos", None)]

    quedan = drop_current_turn(historial, turn_wa_ids=["a", "b", "c"])

    assert [m["content"] for m in quedan] == ["uno", "respondí"]


# ── Higiene ───────────────────────────────────────────────────────────

def test_el_id_interno_no_llega_al_modelo():
    quedan = drop_current_turn([_u("hola", "wamid.1"), _a("¡buenas!")])

    assert all(WA_ID_KEY not in m for m in quedan)
    assert quedan == [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "¡buenas!"}]


def test_no_muta_el_historial_que_recibe():
    historial = [_u("hola", "wamid.1")]
    copia = [dict(m) for m in historial]

    drop_current_turn(historial, turn_wa_ids=["wamid.1"])

    assert historial == copia


def test_un_historial_vacio_no_revienta():
    assert drop_current_turn([]) == []
    assert drop_current_turn([], turn_wa_ids=["wamid.1"]) == []


# ── Las dos puntas usan la MISMA función ──────────────────────────────

def test_los_dos_caminos_comparten_implementacion():
    """La divergencia era el bug. Que no vuelva a haber dos copias."""
    import inspect

    from app.crm import repository
    from app.services import buffer

    local = inspect.getsource(repository.get_conversation_history)
    externo = inspect.getsource(buffer._flush_external)

    for nombre, fuente in (("local", local), ("externo", externo)):
        assert "drop_current_turn(" in fuente, f"el camino {nombre} no usa la función común"
        assert "while history" not in fuente, f"el camino {nombre} recorta por su cuenta"


@pytest.mark.asyncio
async def test_el_buffer_pasa_los_ids_del_turno(monkeypatch):
    """De punta a punta: lo que el buffer acumula llega al recorte."""
    from app.services import buffer

    recibidos: list = []

    async def fake_flush_external(conv_id, contact_id, wa_id, user_content, wa_ids=None):
        recibidos.append(list(wa_ids or []))

    monkeypatch.setattr(buffer, "_use_external_crm", lambda: True)
    monkeypatch.setattr(buffer, "_flush_external", fake_flush_external)

    await buffer._append_to_buffer(
        5, contact_id=1, wa_id="519",
        parts=[{"type": "text", "text": "06 de agosto 2026"}],
        wa_message_id="wamid.1",
    )
    await buffer._append_to_buffer(
        5, contact_id=1, wa_id="519",
        parts=[{"type": "text", "text": "Jueves"}],
        wa_message_id="wamid.2",
    )
    await buffer._flush_buffer(5)

    assert recibidos == [["wamid.1", "wamid.2"]]


@pytest.mark.asyncio
async def test_un_turno_rescatado_recupera_sus_ids_con_sus_partes(monkeypatch):
    """El agujero fino de la preempción.

    Si el turno abortado devolviese el contenido pero no los ids, ese mensaje
    seguiría en el historial Y en el turno — el mismo duplicado que esto
    arregla, colado por la puerta de atrás.
    """
    import asyncio

    from app.services import buffer, preempt

    preempt.reset()
    recibidos: list = []

    async def fake_flush_external(conv_id, contact_id, wa_id, user_content, wa_ids=None):
        recibidos.append(list(wa_ids or []))
        await asyncio.sleep(0)
        preempt.check()

    monkeypatch.setattr(buffer, "_use_external_crm", lambda: True)
    monkeypatch.setattr(buffer, "_flush_external", fake_flush_external)
    monkeypatch.setattr(buffer.settings, "buffer_seconds", 0.01)

    await buffer._append_to_buffer(
        6, contact_id=1, wa_id="519",
        parts=[{"type": "text", "text": "06 de agosto 2026"}],
        wa_message_id="wamid.1",
    )
    tarea = asyncio.create_task(buffer._flush_buffer(6))
    await asyncio.sleep(0)  # el turno arranca y registra su guardia

    # El cliente escribe otra vez con el turno ya en marcha.
    await buffer._append_to_buffer(
        6, contact_id=1, wa_id="519",
        parts=[{"type": "text", "text": "Jueves"}],
        wa_message_id="wamid.2",
    )
    await tarea
    await buffer._flush_buffer(6)

    preempt.reset()
    assert recibidos[-1] == ["wamid.1", "wamid.2"], (
        "el id del turno abortado tiene que volver junto a sus partes"
    )
