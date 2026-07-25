"""Guardrails deterministas del agente.

Esta es la frontera pública para decisiones de conversación y validación de
salidas. El resto de la aplicación no debe inventar reglas paralelas.
"""

from app.guardrails.conversation import (
    dedupe_artifacts,
    grounding_violation,
    handoff_policy,
    is_courtesy_text,
    is_payment_reason,
    is_simple_greeting,
    is_small_talk,
    latest_user_text,
    should_discard_handoff,
)
from app.guardrails.response import (
    GuardrailResult,
    SAFE_FALLBACK,
    Violation,
    check_reply,
    guard_reply,
    image_urls_on_own_line,
    no_cash_on_delivery,
    no_duplicates_within_reply,
    no_repeated_products,
    prices_are_sourced,
    sanitize_reply,
)

__all__ = [
    "GuardrailResult",
    "SAFE_FALLBACK",
    "Violation",
    "check_reply",
    "dedupe_artifacts",
    "grounding_violation",
    "guard_reply",
    "handoff_policy",
    "image_urls_on_own_line",
    "is_courtesy_text",
    "is_payment_reason",
    "is_simple_greeting",
    "is_small_talk",
    "latest_user_text",
    "no_cash_on_delivery",
    "no_duplicates_within_reply",
    "no_repeated_products",
    "prices_are_sourced",
    "sanitize_reply",
    "should_discard_handoff",
]
