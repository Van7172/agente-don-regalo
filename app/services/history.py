"""Qué parte del historial es pasado y qué parte es el turno de ahora.

El historial que se le pasa al modelo sale del CRM, y el CRM ya tiene guardados
los mensajes de este turno: se persisten al entrar por el webhook, ANTES de que
el buffer los agrupe y arranque el turno. Así que el mensaje actual aparece dos
veces —una en el historial y otra como el turno— salvo que alguien lo quite.

Ese "alguien" existía por duplicado y no hacía lo mismo en cada copia:

- el camino local descartaba **todos** los mensajes de usuario del final;
- el camino externo descartaba **uno**.

Producción corre `CRM_MODE=external`, o sea que el que estaba mal era el que
está en el aire: en cuanto un turno fusionaba dos mensajes —por el debounce o
por una preempción— el primero le llegaba al modelo dos veces. Y el camino
local tenía el defecto contrario: un cliente que escribió tres veces sin que el
bot llegara a contestar (bot apagado, un mensaje en la DLQ) veía cómo su
backlog entero desaparecía del contexto, porque el bucle no distinguía "esto es
del turno" de "esto quedó sin responder".

Aquí solo hay una implementación, y no adivina: los mensajes del turno se
identifican por su `wa_message_id`, que es el único identificador que comparten
las dos puntas. La Cloud API lo trae en cada mensaje entrante y el CRM lo
devuelve en el detalle de la conversación.

El respaldo posicional sigue existiendo para cuando los ids no cuadran (una fila
antigua sin `wa_message_id`, un CRM sin actualizar), pero **acotado**: descarta
como mucho tantos mensajes como sabemos que trae este turno. Nunca más. Es la
diferencia entre "quito lo que sobra" y "vacío la cola", que es justo lo que
hacía el bucle de antes.
"""
from __future__ import annotations

from typing import Any, Collection, Iterable

# Clave con la que cada constructor de historial adjunta el id del mensaje. Se
# quita antes de devolver: al modelo no le llega ningún campo interno.
WA_ID_KEY = "wa_message_id"


def drop_current_turn(
    history: Iterable[dict[str, Any]],
    *,
    turn_wa_ids: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Historial sin los mensajes que ya viajan como turno actual.

    `history` son entradas `{"role", "content"}` que pueden traer además
    `wa_message_id`; el resultado nunca lo lleva.

    `turn_wa_ids` son los ids que el buffer consumió en este turno. Cuantos más
    se pasen, más exacto es el recorte: con ids se quitan ESOS mensajes estén
    donde estén, y sin ellos se cae al respaldo posicional acotado.
    """
    entradas = [dict(m) for m in history]
    buscados = {str(i) for i in turn_wa_ids if i}

    # 1. Coincidencia exacta. No supone orden ni posición: si el turno fusionó
    #    tres mensajes, salen los tres, y los que quedaron sin responder de
    #    antes se mantienen donde estaban.
    if buscados:
        quedan = [m for m in entradas if str(m.get(WA_ID_KEY) or "") not in buscados]
        acertados = len(entradas) - len(quedan)
        entradas = quedan
    else:
        acertados = 0

    # 2. Respaldo posicional para lo que no se pudo identificar. `esperados` es
    #    cuántos mensajes trae el turno; sin ids no lo sabemos y asumimos uno,
    #    que es el caso corriente y el comportamiento histórico.
    esperados = len(buscados) if buscados else 1
    por_quitar = max(0, esperados - acertados)
    while por_quitar and entradas and entradas[-1].get("role") == "user":
        entradas.pop()
        por_quitar -= 1

    for entrada in entradas:
        entrada.pop(WA_ID_KEY, None)
    return entradas
