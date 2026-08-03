"""Un mensaje nuevo tumba el turno que aún no ha hecho nada — y solo ese.

El debounce de `buffer` fusiona los mensajes que llegan juntos, pero solo cubre
el hueco ANTES de arrancar el turno. Si el segundo entra mientras el agente ya
está pensando (un turno con LLM y tools tarda entre 3 y 10 segundos), se abre un
turno nuevo contra un estado que el primero está a punto de cambiar.

Lo que este módulo protege es el límite: cancelar a ciegas dejaría handoffs a
medias, fillers duplicados y pedidos temporales huérfanos, porque
`CancelledError` no deshace un `set_mode(HUMAN)` ni un mensaje ya enviado por
WhatsApp. De ahí la regla única: se aborta SOLO mientras no se haya cruzado el
punto de no retorno.
"""
import asyncio

import pytest

from app.services import preempt
from app.services.preempt import MAX_PREEMPTIONS, TurnAborted


@pytest.fixture(autouse=True)
def _limpio():
    preempt.reset()
    yield
    preempt.reset()


def _partes(texto: str) -> list:
    return [{"type": "text", "text": texto}]


# ── Lo que se puede abortar ───────────────────────────────────────────

def test_un_turno_que_no_ha_hecho_nada_se_suelta():
    guard = preempt.begin(7, _partes("06 de agosto 2026"), ["wamid.1"])

    rescatadas, ids = preempt.preempt(7)

    assert ids == ["wamid.1"], "los ids viajan con las partes, nunca por separado"
    assert rescatadas == _partes("06 de agosto 2026"), (
        "las partes tienen que volver: el turno nuevo debe responder a los dos "
        "mensajes y el primero ya no está en ninguna cola"
    )
    with pytest.raises(TurnAborted):
        guard.check()


def test_el_aborto_llega_hasta_el_contexto_del_turno():
    """La fachada de módulo es la que usan las primitivas con efecto."""
    preempt.begin(7, _partes("hola"))
    preempt.preempt(7)

    with pytest.raises(TurnAborted):
        preempt.check()
    with pytest.raises(TurnAborted):
        preempt.commit()


# ── El punto de no retorno ────────────────────────────────────────────

def test_un_turno_que_ya_hablo_no_se_aborta():
    guard = preempt.begin(7, _partes("hola"))
    guard.commit()  # p.ej. `_say` mandando la respuesta

    assert preempt.preempt(7) == ([], []), "ya habló: el turno tiene que terminar"
    guard.check()  # no levanta


def test_tras_el_commit_el_aborto_pendiente_deja_de_aplicar():
    """Carrera real: el aborto entra entre el `check` y el efecto."""
    guard = preempt.begin(7, _partes("hola"))
    guard.commit()
    guard.aborted = True  # alguien lo pidió justo después

    guard.check()  # no levanta: mandan los efectos ya hechos
    guard.commit()


def test_sin_guardia_las_primitivas_no_hacen_nada():
    """El releaser, el outbox y los tests no corren dentro de un turno."""
    preempt.check()
    preempt.commit()


# ── Que nadie se quede sin respuesta ──────────────────────────────────

def test_hay_un_tope_de_preempciones():
    """Quien escribe seis líneas seguidas también merece una respuesta."""
    for _ in range(MAX_PREEMPTIONS):
        preempt.begin(7, _partes("otra más"))
        assert preempt.preempt(7)[0], "debería haber abortado"

    guard = preempt.begin(7, _partes("y otra"))
    assert preempt.preempt(7) == ([], []), "pasado el tope, el turno responde"
    guard.check()  # no levanta


def test_un_turno_completo_reinicia_el_contador():
    preempt.begin(7, _partes("uno"))
    preempt.preempt(7)

    completo = preempt.begin(7, _partes("dos"))
    preempt.finish(completo)  # terminó sin abortarse

    otro = preempt.begin(7, _partes("tres"))
    assert preempt.preempt(7)[0], "el contador tenía que estar a cero"
    with pytest.raises(TurnAborted):
        otro.check()


def test_conversaciones_distintas_no_se_pisan():
    a = preempt.begin(7, _partes("de la 7"))
    b = preempt.begin(9, _partes("de la 9"))

    assert preempt.preempt(9)[0] == _partes("de la 9")
    with pytest.raises(TurnAborted):
        b.check()
    a.check()  # la otra conversación sigue tan tranquila


def test_finish_limpia_el_turno_en_vuelo():
    guard = preempt.begin(7, _partes("hola"))
    preempt.finish(guard)

    assert preempt.preempt(7) == ([], []), "ya no hay turno que tumbar"


# ── Integración con el buffer ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_el_buffer_recupera_las_partes_del_turno_abortado(monkeypatch):
    """El caso que motivó todo: los dos mensajes acaban en el mismo turno."""
    from app.services import buffer

    vistos: list = []

    async def fake_flush_local(conversation_id, contact_id, wa_id, user_content, wa_ids=None):
        vistos.append(user_content)
        # Simula un turno lento que se entera del aborto en un checkpoint.
        await asyncio.sleep(0)
        preempt.check()

    monkeypatch.setattr(buffer, "_use_external_crm", lambda: False)
    monkeypatch.setattr(buffer, "_flush_local", fake_flush_local)
    monkeypatch.setattr(buffer.settings, "buffer_seconds", 0.01)

    await buffer._append_to_buffer(7, contact_id=1, wa_id="519", parts=_partes("06 de agosto 2026"))
    tarea = asyncio.create_task(buffer._flush_buffer(7))
    await asyncio.sleep(0)  # que el turno arranque y registre su guardia

    # El cliente escribe otra vez con el turno ya en marcha.
    await buffer._append_to_buffer(7, contact_id=1, wa_id="519", parts=_partes("Jueves"))
    await tarea

    await buffer._flush_buffer(7)

    assert len(vistos) == 2
    segundo = str(vistos[1])
    assert "06 de agosto 2026" in segundo and "Jueves" in segundo, (
        "el turno nuevo tiene que ver los DOS mensajes"
    )


# ── Integración con el turno real ─────────────────────────────────────

@pytest.mark.asyncio
async def test_un_turno_abortado_no_escribe_el_estado(monkeypatch):
    """Sin rastro: el turno nuevo tiene que releer un estado limpio.

    Un estado escrito a medias con una respuesta que nunca se envió es
    exactamente el descuadre que hace que el paso siguiente del cierre no le
    cuadre a nadie.
    """
    from app.harness import master as master_mod
    from app.harness import state as state_mod
    from app.harness.contracts import AgentResult

    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: False)

    guardados: list = []

    async def fake_save_state(*a, **kw):
        guardados.append(a)

    hablado: list = []

    async def specialty(*_a, **_kw):
        hablado.append("el especialista corrió")
        return AgentResult(user_facing="una respuesta cualquiera")

    monkeypatch.setattr(master_mod, "save_state", fake_save_state)
    monkeypatch.setattr(master_mod, "_run_specialty", specialty)

    preempt.begin(77, _partes("quiero un regalo"))
    preempt.preempt(77)  # el cliente escribió otra vez

    with pytest.raises(TurnAborted):
        await master_mod.run_master(
            [{"role": "user", "content": "quiero un regalo"}],
            wa_id="51999",
            conversation_id=77,
        )

    assert guardados == [], "un turno abortado no puede dejar estado escrito"
    assert hablado == [], "ni gastar un especialista: aborta justo tras clasificar"


@pytest.mark.asyncio
async def test_el_aborto_no_se_confunde_con_un_fallo_del_bucle(monkeypatch):
    """`run_specialist` atrapa `Exception` para no morirse con un turno.

    Si se tragara el aborto lo registraría como "el bucle murió", devolvería
    `None` y el rescate de `master` acabaría cediéndole el chat a un humano por
    un turno que simplemente sobraba.
    """
    from app.services import agent as agent_mod

    async def boom(*_a, **_kw):
        raise TurnAborted("conversación 7: llegó otro mensaje")

    monkeypatch.setattr(agent_mod, "_chat_completion", boom)

    with pytest.raises(TurnAborted):
        await agent_mod.run_specialist(
            [{"role": "system", "content": "x"}, {"role": "user", "content": "hola"}],
            wa_id="519",
            conversation_id=7,
            tools_override=[],
        )


@pytest.mark.asyncio
async def test_un_turno_abortado_no_deja_el_mensaje_sin_atender(monkeypatch):
    """El waiter se resuelve OK: lo contesta el turno siguiente, no se reintenta.

    Marcarlo como fallido haría que una cola durable reentregara el mismo
    mensaje y el cliente lo vería respondido dos veces.
    """
    from app.services import buffer

    async def fake_flush_local(conversation_id, contact_id, wa_id, user_content, wa_ids=None):
        preempt.check()

    monkeypatch.setattr(buffer, "_use_external_crm", lambda: False)
    monkeypatch.setattr(buffer, "_flush_local", fake_flush_local)

    completion = await buffer._append_to_buffer(
        7, contact_id=1, wa_id="519", parts=_partes("hola")
    )
    tarea = asyncio.create_task(buffer._flush_buffer(7))
    await asyncio.sleep(0)
    preempt.preempt(7)
    await tarea

    assert completion.done()
    assert completion.exception() is None
