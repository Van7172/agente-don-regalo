"""Un mensaje nuevo puede tumbar el turno que todavía no ha hecho nada.

En WhatsApp se escribe a ráfagas. El debounce de `buffer` fusiona los mensajes
que llegan juntos, pero solo cubre el hueco ANTES de arrancar el turno: si el
segundo mensaje entra mientras el agente ya está pensando —y un turno con LLM y
tools tarda entre 3 y 10 segundos—, se abre un turno nuevo contra un estado que
el primero está a punto de cambiar. El cliente acaba recibiendo una respuesta a
media pregunta.

Lo que NO se puede hacer es `task.cancel()` a secas. Un turno no es una función
pura: para cuando llega el segundo mensaje puede haber mandado ya un filler por
WhatsApp, haber puesto la conversación en HUMAN, haber avisado al equipo, haber
creado el pedido temporal o haber pintado la venta en verde. `CancelledError` no
deshace nada de eso, así que cancelar a ciegas deja handoffs a medias y pedidos
huérfanos — peor que el problema que arregla.

De ahí la única regla de este módulo:

    Un turno se aborta SOLO mientras no haya cruzado el punto de no retorno:
    nada enviado al cliente, nada escrito en el CRM ni en la API.

El primer acto irreversible llama a `commit()` y a partir de ahí el turno se
termina pase lo que pase; el mensaje nuevo se procesará como turno siguiente,
que es el comportamiento de siempre y para ese caso el correcto. Los puntos de
commit viven en las primitivas que causan el efecto (`_say`, `perform_handoff`,
`save_state`, el envío de la respuesta), no en los sitios que las llaman: así
un camino nuevo no puede olvidarse de pedir permiso.

**Un filler no compromete el turno.** "Un momento, ya te ayudo 😊" no dice nada
que pueda volverse falso, así que sobrevivir a un aborto no le hace daño a
nadie. Si comprometiera, la preempción moriría a los 0.7s —cuando sale el
filler— y no serviría para ningún turno real.

El guardia viaja por `ContextVar`, como `demand` y `observability.llm_usage`:
cruzarlo por las firmas obligaría a que cada test que hace stub de una de esas
primitivas replicara el parámetro.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

from app.observability import record_operation

log = logging.getLogger(__name__)


class TurnAborted(Exception):
    """El cliente volvió a escribir y este turno aún no había hecho nada.

    No es un error: es la señal de que hay que soltar el trabajo y dejar que el
    turno siguiente responda a los dos mensajes juntos.
    """


# Cuántas veces seguidas puede un cliente tumbar el turno en curso. Sin tope,
# quien escribe seis líneas cortas seguidas no recibiría respuesta jamás: cada
# mensaje mataría al anterior. Al llegar aquí el turno se termina, contesta, y
# el contador vuelve a cero.
MAX_PREEMPTIONS = 2


@dataclass
class TurnGuard:
    """El permiso de un turno para seguir vivo."""

    conversation_id: int
    # Las partes del mensaje que este turno consumió. Si se aborta, vuelven al
    # buffer: el turno nuevo tiene que responder a los DOS mensajes, y el
    # primero ya no está en ninguna cola.
    parts: list = field(default_factory=list)
    # Y sus `wa_message_id`, que viajan JUNTO a las partes y nunca por separado.
    # Con ellos se descartan del historial los mensajes que ya van en el turno
    # (`services.history`); si un turno rescatado recuperase las partes sin los
    # ids, el mensaje volvería a llegarle duplicado al modelo — que es
    # exactamente el bug que esto arregla.
    wa_ids: list[str] = field(default_factory=list)
    aborted: bool = False
    committed: bool = False

    def request_abort(self) -> bool:
        """Pide el aborto. `False` si ya es tarde (cruzó el punto de no retorno)."""
        if self.committed:
            return False
        self.aborted = True
        return True

    def check(self) -> None:
        """Aborta si procede, sin comprometer nada.

        Para las salidas tempranas y baratas: cuanto antes se note, menos
        trabajo se tira.
        """
        if self.aborted and not self.committed:
            raise TurnAborted(
                f"conversación {self.conversation_id}: llegó otro mensaje"
            )

    def commit(self) -> None:
        """Punto de no retorno: se va a hacer algo que no se puede deshacer."""
        self.check()
        self.committed = True


_CURRENT: ContextVar[Optional[TurnGuard]] = ContextVar(
    "donregalo_turn_guard", default=None
)

# Turnos vivos y preempciones seguidas, por conversación. Diccionarios normales:
# esto es asyncio de un solo hilo y todo el acceso ocurre entre `await`s.
_inflight: dict[int, TurnGuard] = {}
_preemptions: dict[int, int] = {}


def begin(
    conversation_id: Optional[int],
    parts: list,
    wa_ids: Optional[list[str]] = None,
) -> Optional[TurnGuard]:
    """Marca el arranque de un turno abortable. `None` si no aplica."""
    if conversation_id is None:
        return None
    guard = TurnGuard(
        conversation_id=conversation_id,
        parts=list(parts or []),
        wa_ids=list(wa_ids or []),
    )
    _inflight[conversation_id] = guard
    _CURRENT.set(guard)
    return guard


def finish(guard: Optional[TurnGuard]) -> None:
    """Cierra el turno. Un turno que llegó al final limpia el contador."""
    _CURRENT.set(None)
    if guard is None:
        return
    if _inflight.get(guard.conversation_id) is guard:
        _inflight.pop(guard.conversation_id, None)
    if not guard.aborted:
        _preemptions.pop(guard.conversation_id, None)


def preempt(conversation_id: Optional[int]) -> tuple[list, list[str]]:
    """Intenta tumbar el turno en vuelo y recuperar lo que estaba respondiendo.

    Devuelve `(partes, wa_ids)` para que el turno nuevo los procese junto con
    los suyos, o dos listas vacías si no había turno que tumbar, si ya había
    cruzado el punto de no retorno, o si se agotó el tope de preempciones.

    Los dos valores salen juntos a propósito: separarlos deja al turno fusionado
    sin saber qué mensajes ya están en el historial.
    """
    if conversation_id is None:
        return [], []
    guard = _inflight.get(conversation_id)
    if guard is None:
        return [], []

    if _preemptions.get(conversation_id, 0) >= MAX_PREEMPTIONS:
        # Ya lo tumbamos dos veces: este turno termina y contesta. Volver a
        # abortar sería dejar al cliente sin respuesta por escribir deprisa.
        log.info(
            "[PREEMPT] conversation=%s tope alcanzado; el turno termina",
            conversation_id,
        )
        record_operation("turn.preempt", "capped")
        return [], []

    if not guard.request_abort():
        record_operation("turn.preempt", "too_late")
        return [], []

    _preemptions[conversation_id] = _preemptions.get(conversation_id, 0) + 1
    _inflight.pop(conversation_id, None)
    record_operation("turn.preempt", "aborted")
    log.info(
        "[PREEMPT] conversation=%s turno abortado; se responde a los dos mensajes",
        conversation_id,
    )
    return guard.parts, guard.wa_ids


# ── Fachada para las primitivas con efecto ────────────────────────────
#
# Sin guardia (un test, el releaser, el drenaje del outbox) no hacen nada: solo
# el turno de un cliente es abortable.


def check() -> None:
    guard = _CURRENT.get()
    if guard is not None:
        guard.check()


def commit() -> None:
    guard = _CURRENT.get()
    if guard is not None:
        guard.commit()


def reset() -> None:
    """Limpia el registro. Solo para los tests."""
    _CURRENT.set(None)
    _inflight.clear()
    _preemptions.clear()
