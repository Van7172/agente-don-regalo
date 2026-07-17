"""Composición del system message: CORE + FACTS + PLAYBOOK + STATE.

Único punto donde se arma el prompt de un agente. Que sea único es lo que permite
garantizar —y testear— que el bloque de seguridad va SIEMPRE.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from app.prompts.core import core_system
from app.prompts.facts import render_facts

if TYPE_CHECKING:  # evita el ciclo registry → prompts → registry
    from app.harness.registry import AgentSpec
    from app.harness.state import ConversationState

_LIMA = ZoneInfo("America/Lima")
_WEEKDAYS = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)


def render_current_time(now: datetime | None = None) -> str:
    current = now.astimezone(_LIMA) if now and now.tzinfo else now
    current = current or datetime.now(_LIMA)
    return (
        "## FECHA Y HORA ACTUAL\n"
        "Zona: America/Lima\n"
        f"Ahora: {_WEEKDAYS[current.weekday()]} "
        f"{current.strftime('%d/%m/%Y, %H:%M')}.\n"
        "Interpreta hoy, mañana y los días de la semana desde esta fecha."
    )


def render_state(state: "ConversationState") -> str:
    """El estado del harness es la fuente de verdad, por encima del historial."""
    blob = json.dumps(
        {
            "checkout_step": state.checkout_step,
            "district": state.district,
            "shipping_fee_sol": state.shipping_fee_sol,
            "shown_product_ids": state.shown_product_ids[-30:],
            "chosen_product_id": state.chosen_product_id,
            "chosen_product_name": state.chosen_product_name,
        },
        ensure_ascii=False,
    )
    return (
        "## ESTADO (fuente de verdad — por encima del historial)\n"
        f"{blob}\n"
        "Si `shown_product_ids` no está vacío y el cliente pide más opciones, pasa "
        "esos ids en `excluir_ids`."
    )


def build_system(
    spec: "AgentSpec",
    state: "ConversationState | None" = None,
    *,
    extra: str = "",
    now: datetime | None = None,
) -> str:
    """System message completo de un agente.

    Los agentes de cara al cliente llevan SIEMPRE el CORE (identidad, estilo y
    restricciones). El orquestador no: no le habla al cliente.
    """
    blocks: list[str] = []

    if spec.customer_facing:
        blocks.append(core_system())
        blocks.append(render_current_time(now))

    facts = render_facts(spec.facts)
    if facts:
        blocks.append(facts)

    blocks.append(spec.playbook)

    if state is not None and spec.customer_facing:
        blocks.append(render_state(state))

    if extra:
        blocks.append(extra)

    return "\n\n".join(b for b in blocks if b)


def profile_block(profile: dict[str, Any]) -> str:
    datos = "\n".join(f"- {k}: {v}" for k, v in profile.items() if v)
    return (
        "DATOS CONOCIDOS DEL CLIENTE (de conversaciones previas):\n"
        f"{datos}\n"
        "Úsalos para personalizar y NO vuelvas a preguntar lo que ya sabes."
    )
