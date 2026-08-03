"""Qué nos piden los clientes que no tenemos.

Es la señal más directa de qué producto lanzar y hasta ahora se tiraba entera.
Cuando la escalera de `executor.execute_tool` agota sus cuatro peldaños —API,
Qdrant, consulta acortada, categoría— el resultado sale marcado `aproximado` o
directamente vacío. Esa marca vivía dentro del resultado de la tool durante ese
turno y moría ahí: sabíamos decirle al cliente que no lo teníamos y no sabíamos
cuántos lo habían pedido.

Tres decisiones que importan:

**No puede costarle latencia al cliente.** Esto es telemetría, no parte de la
respuesta, así que `record_miss` no se espera: lanza una tarea y vuelve. Un CRM
lento o caído no puede añadir un segundo al turno de nadie — y si falla, lo que
se pierde es una fila de análisis, no una venta. Por eso tampoco propaga: se
traga todo y lo deja en el log.

**Se guarda el argumento de la tool, no el mensaje del cliente.** El mensaje
trae prosa, saludos y a veces la dirección o el teléfono del destinatario; el
argumento ya viene destilado a términos, que es justo lo que hay que agrupar.
Aun así pasa por `redact_personal_data`: el modelo compone ese argumento y nada
garantiza que no arrastre un dato personal desde la frase original.

**La conversación viaja por `ContextVar` y no por parámetro.** Cruzarla por la
firma obligaría a que `execute_tool` la recibiera desde `agent.py` y desde
`master.py`, y a que cada test que llama una tool la pasara. Es el mismo motivo
por el que el agente se etiqueta así en `observability/llm_usage.py`.
"""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from typing import Optional

from app.crm import http_client as crm_http
from app.guardrails.privacy import redact_personal_data
from app.observability import record_operation

log = logging.getLogger(__name__)

_CONVERSATION: ContextVar[Optional[int]] = ContextVar(
    "donregalo_demand_conversation", default=None
)

# asyncio solo guarda una referencia débil a las tareas sueltas: sin este set el
# recolector puede llevarse la tarea antes de que llegue a mandar nada, y el
# fallo no aparece en ninguna parte. Cada tarea se borra sola al terminar.
_pending: set[asyncio.Task] = set()

# El ancho de `consulta_demanda` en la migración 014. Cortar aquí y no en MySQL:
# con el modo estricto apagado, la base trunca en silencio.
MAX_QUERY = 255

VACIO = "vacio"
APROXIMADO = "aproximado"


def set_conversation(conversation_id: Optional[int]) -> None:
    """Ata las búsquedas siguientes a un chat. `None` las deja anónimas."""
    _CONVERSATION.set(conversation_id)


def current_conversation() -> Optional[int]:
    return _CONVERSATION.get()


def record_miss(
    query: str,
    *,
    resultado: str,
    n_resultados: int = 0,
    categoria: Optional[str] = None,
) -> None:
    """Anota que no teníamos lo que pidieron. No se espera: vuelve enseguida."""
    texto = (query or "").strip()
    if not texto:
        # Una búsqueda sin términos (navegación por categoría, destacados) no
        # dice nada sobre qué falta en el catálogo. Sería ruido puro.
        return
    if not crm_http.crm_enabled():
        return

    limpio = redact_personal_data(texto).value[:MAX_QUERY].strip()
    if not limpio:
        return

    if resultado not in (VACIO, APROXIMADO):
        resultado = VACIO

    try:
        tarea = asyncio.get_running_loop().create_task(
            _send(
                limpio,
                resultado=resultado,
                n_resultados=max(0, int(n_resultados)),
                categoria=(categoria or None),
                conversation_id=_CONVERSATION.get(),
            )
        )
    except RuntimeError:
        # Sin loop corriendo (un test síncrono, un script) no hay dónde lanzarla.
        return

    _pending.add(tarea)
    tarea.add_done_callback(_pending.discard)


async def _send(
    query: str,
    *,
    resultado: str,
    n_resultados: int,
    categoria: Optional[str],
    conversation_id: Optional[int],
) -> None:
    try:
        await crm_http.record_demand_miss(
            query,
            resultado=resultado,
            n_resultados=n_resultados,
            categoria=categoria,
            conversation_id=conversation_id,
        )
        record_operation("demand.miss", "recorded")
    except Exception as error:
        # Perder una fila de análisis no puede ensuciar un turno que salió bien.
        record_operation("demand.miss", "error")
        log.warning("[demanda] no se pudo anotar %r: %s", query[:40], error)
