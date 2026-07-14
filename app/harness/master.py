"""Orquestador del harness.

Un turno = percibir → clasificar → delegar → reducir → persistir.

Dos reglas que lo definen:

1. **El orquestador no habla con el cliente.** Todo texto de cara al cliente sale
   de un especialista (`registry.AGENTS`), incluidos los saludos, que atiende el
   `concierge`. Por eso su propio prompt no lleva ni identidad ni estilo.
2. **El estado se reduce desde `AgentResult`,** nunca desde la prosa de la
   respuesta. Los ids de producto vienen de los resultados de las tools.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.harness.checkout import (
    advance_checkout,
    resolve_chosen_product,
    wants_checkout,
)
from app.harness.contracts import AgentResult, EscalateReason, Turn
from app.harness.coverage import resolve_coverage
from app.harness.policies import dedupe_artifacts, grounding_violation, latest_user_text
from app.harness.registry import spec_for
from app.harness.router import classify_intent
from app.harness.state import ConversationState, load_state, save_state
from app.prompts.compose import build_system
from app.services.agent import HANDOFF_DONE, run_specialist

log = logging.getLogger(__name__)


def perceive(messages: list) -> Turn:
    """Qué nos llega del cliente en este turno."""
    text = latest_user_text(messages)
    return Turn(text=text or "", has_media=text is None, messages=messages)


def _reduce(state: ConversationState, result: AgentResult) -> ConversationState:
    """Aplica al estado lo que el especialista aprendió."""
    if result.state_patch:
        state.patch(result.state_patch)

    if result.artifacts:
        state.patch({
            "shown_product_ids": [p.id_producto for p in result.artifacts],
            "recent_products": [
                {"id_producto": p.id_producto, "nombre": p.nombre}
                for p in result.artifacts
            ],
        })

    if result.escalate is not None:
        state.handoff_reason = result.escalate.motivo or state.handoff_reason
        if result.escalate.is_payment:
            state.checkout_step = "payment"

    return state


async def run_master(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
) -> str | None:
    turn = perceive(messages)
    state = (
        await load_state(conversation_id, wa_id=wa_id)
        if conversation_id is not None
        else ConversationState()
    )

    intent = classify_intent(turn.text, state)
    state.intent_last = intent

    log.info(
        "[harness] conversation=%s intent=%s agent=%s step=%s",
        conversation_id,
        intent,
        spec_for(intent).name,
        state.checkout_step,
    )

    result = await _handle(
        intent,
        turn,
        state,
        wa_id=wa_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        session=session,
        use_external_crm=use_external_crm,
        persist=persist,
    )

    state = _reduce(state, result)

    if result.user_facing:
        violation = grounding_violation(result.user_facing, result.artifacts)
        if violation:
            # No cortamos la respuesta (dejaría al cliente sin nada), pero queda
            # en el log para que el eval lo cace.
            log.warning(
                "[harness] grounding conversation=%s: %s", conversation_id, violation
            )

    if conversation_id is not None:
        await save_state(conversation_id, state, wa_id=wa_id)

    if result.escalate is not None:
        return HANDOFF_DONE
    return result.user_facing


async def _handle(intent: str, turn: Turn, state: ConversationState, **ctx) -> AgentResult:
    """Enruta el turno al especialista o a la máquina de estados que le toca."""

    # ── Cobertura: determinista, sin LLM ──────────────────────────
    if intent == "coverage":
        raw = await resolve_coverage(turn.text, state)
        return AgentResult(
            user_facing=raw.get("user_facing") or raw.get("structured", {}).get("ask"),
            state_patch=raw.get("state_patch") or {},
        )

    # ── Cierre: máquina de estados, sin LLM ───────────────────────
    if intent == "checkout" or (
        wants_checkout(turn.text) and state.checkout_step in ("idle", "")
    ):
        return await _handle_checkout(turn, state, **ctx)

    # ── Resto: especialista LLM con toolset acotado ───────────────
    return await _run_specialty(intent, turn, state, **ctx)


async def _handle_checkout(turn: Turn, state: ConversationState, **ctx) -> AgentResult:
    if state.checkout_step in ("idle", ""):
        chosen = resolve_chosen_product(state, turn.text)
        if chosen is not None:
            # Solo fijamos el producto. El paso lo avanza `advance_checkout` desde
            # "idle", que además NO consume este texto: "quiero el panditas" es la
            # elección del producto, no el distrito.
            state.chosen_product_id, state.chosen_product_name = chosen
        elif state.recent_products:
            # Varias opciones a la vista y una referencia ambigua ("ese"):
            # preguntar es mejor que cerrar el pedido del producto equivocado.
            names = ", ".join(
                p["nombre"] for p in state.recent_products[:5] if p.get("nombre")
            )
            return AgentResult(
                user_facing=(
                    f"¡Genial! 😊 ¿Cuál de estos te llevas: {names}?"
                    if names
                    else "¡Genial! 😊 ¿Cuál de los que te mostré te llevas?"
                )
            )

    state, reply, meta = advance_checkout(state, turn.text)

    if not meta.get("escalate"):
        return AgentResult(user_facing=reply)

    # Resumen confirmado: el pago lo coordina un humano.
    motivo = state.handoff_reason or "cliente listo para pagar / coordinar comprobante"
    escalated = await _run_specialty(
        "escalate",
        turn,
        state,
        extra_system=(
            "El cliente confirmó el resumen del pedido y pasa a pago. "
            f"Llama YA `escalar_a_humano` con motivo: {motivo}."
        ),
        **ctx,
    )
    if escalated.escalate is not None:
        return escalated

    # El especialista no llamó la tool: el handoff se hace igual. Un cliente
    # listo para pagar no puede quedarse esperando.
    return AgentResult(
        user_facing=reply, escalate=EscalateReason(motivo=motivo, is_payment=True)
    )


async def _run_specialty(
    intent: str,
    turn: Turn,
    state: ConversationState,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
    extra_system: str = "",
) -> AgentResult:
    spec = spec_for(intent)
    system = build_system(spec, state, extra=extra_system)

    result = await run_specialist(
        [{"role": "system", "content": system}, *turn.messages],
        wa_id=wa_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        session=session,
        use_external_crm=use_external_crm,
        persist=persist,
        tools_override=spec.tools(
            with_memory=bool(contact_id or use_external_crm),
            with_handoff=conversation_id is not None,
        ),
        include_handoff=spec.can_handoff,
        include_memory=spec.customer_facing,
    )

    # Nunca mostrar dos veces el mismo producto: el cliente lo lee como que no le
    # hicimos caso ("otras, no esas").
    if spec.name == "catalog":
        result.artifacts = dedupe_artifacts(state.shown_product_ids, result.artifacts)

    return result
