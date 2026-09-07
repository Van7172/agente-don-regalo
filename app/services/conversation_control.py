"""Veredicto autoritativo sobre quién puede hablarle al cliente.

El gate de entrada evita arrancar turnos nuevos cuando el chat está en HUMAN,
pero no protege un turno que ya estaba pensando cuando el asesor lo tomó. Antes
de cada salida del bot se relee el CRM: la asignación humana manda incluso en la
ventana breve entre `/claim` y el cambio de modo a HUMAN.
"""
from __future__ import annotations

import logging

from app.crm import http_client as crm_http

log = logging.getLogger(__name__)


def bot_controls(detail: dict) -> bool:
    """True únicamente si el CRM confirma que el chat sigue libre y en IA."""
    conv = detail.get("conversation") if isinstance(detail, dict) else None
    if not isinstance(conv, dict):
        return False
    return (
        str(conv.get("mode") or "").upper() == "AI"
        and bool(conv.get("bot_active", False))
        and not bool(conv.get("human_support"))
        and not bool(conv.get("assigned"))
    )


async def bot_controls_external(conversation_id: int) -> bool:
    """Relee el CRM justo antes de hablar; ante duda, el bot se calla."""
    try:
        detail = await crm_http.get_conversation(conversation_id)
    except Exception as error:
        log.warning(
            "[CONTROL] conversation=%s no se pudo confirmar control (%s); "
            "respuesta del bot bloqueada",
            conversation_id,
            type(error).__name__,
        )
        return False
    allowed = bot_controls(detail)
    if not allowed:
        conv = detail.get("conversation") or {}
        log.info(
            "[CONTROL] conversation=%s salida bot bloqueada "
            "(mode=%s bot=%s help=%s assigned=%s)",
            conversation_id,
            conv.get("mode"),
            conv.get("bot_active"),
            conv.get("human_support"),
            bool(conv.get("assigned")),
        )
    return allowed
