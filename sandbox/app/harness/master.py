"""Regalito Master: clasifica intención, delega a specialties y guarda estado."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.harness.checkout import advance_checkout, start_checkout, wants_checkout
from app.harness.coverage import resolve_coverage
from app.harness.router import classify_intent
from app.harness.state import ConversationState, load_state, save_state
from app.harness.toolsets import tools_for
from app.prompts.harness import MASTER_PROMPT, SPECIALTY_PROMPTS
from app.services.agent import HANDOFF_DONE, run_agent

log = logging.getLogger(__name__)

_PRODUCT_ID_RE = re.compile(r'"id_producto"\s*:\s*(\d+)')


def _latest_user_text(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                bits = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        bits.append(part.get("text") or "")
                return "\n".join(bits)
    return ""


def _extract_shown_ids(text: str) -> list[int]:
    return [int(x) for x in _PRODUCT_ID_RE.findall(text or "")]


def _build_specialty_messages(
    history_messages: list,
    *,
    intent: str,
    state: ConversationState,
) -> list:
    specialty = SPECIALTY_PROMPTS.get(intent, SPECIALTY_PROMPTS["catalog_search"])
    state_blob = json.dumps(
        {
            "checkout_step": state.checkout_step,
            "district": state.district,
            "shown_product_ids": state.shown_product_ids[-30:],
            "chosen_product_id": state.chosen_product_id,
            "chosen_product_name": state.chosen_product_name,
            "shipping_fee_sol": state.shipping_fee_sol,
        },
        ensure_ascii=False,
    )
    system = (
        MASTER_PROMPT
        + "\n\n"
        + specialty
        + "\n\n## ESTADO HARNESS (fuente de verdad)\n"
        + state_blob
        + "\nUsa excluir_ids con shown_product_ids cuando el cliente pida más opciones."
    )
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    # Conservar historial + profile systems excepto el SYSTEM_PROMPT monolítico viejo
    for m in history_messages:
        if m.get("role") == "system" and "Eres Regalito, el asistente virtual" in (
            m.get("content") or ""
        ):
            continue
        out.append(m)
    return out


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
    """Entrada del harness: un turno = classify + (specialty | FSM | LLM acotado)."""
    user_text = _latest_user_text(messages)
    state = (
        await load_state(conversation_id, wa_id=wa_id)
        if conversation_id is not None
        else ConversationState()
    )

    intent = classify_intent(user_text, state)
    state.intent_last = intent
    log.info(
        "[harness] conversation=%s intent=%s step=%s",
        conversation_id,
        intent,
        state.checkout_step,
    )

    # ── Cobertura determinística ─────────────────────────────────
    if intent == "coverage":
        result = await resolve_coverage(user_text, state)
        state.patch(result.get("state_patch") or {})
        if conversation_id is not None:
            await save_state(conversation_id, state, wa_id=wa_id)
        return result.get("user_facing") or result.get("structured", {}).get("ask")

    # ── Cierre FSM ───────────────────────────────────────────────
    if intent == "checkout" or (
        wants_checkout(user_text) and state.checkout_step in ("idle", "")
    ):
        if state.checkout_step in ("idle", ""):
            # Intentar capturar nombre de producto citado / “me gusta esta”
            start_checkout(state)
        state, reply, meta = advance_checkout(state, user_text)
        if conversation_id is not None:
            await save_state(conversation_id, state, wa_id=wa_id)
        if meta.get("escalate"):
            # Delegar handoff real al loop del agente con tool forzada vía prompt
            esc_messages = _build_specialty_messages(
                messages, intent="escalate", state=state
            )
            esc_messages.append({
                "role": "system",
                "content": (
                    "El cliente confirmó el resumen o está en pago. "
                    "Llama YA escalar_a_humano con motivo: "
                    f"{state.handoff_reason or 'pago/comprobante'}."
                ),
            })
            tools = tools_for("escalate", with_memory=True, with_handoff=True)
            result = await run_agent(
                esc_messages,
                wa_id=wa_id,
                contact_id=contact_id,
                conversation_id=conversation_id,
                session=session,
                use_external_crm=use_external_crm,
                persist=persist,
                tools_override=tools,
                include_handoff=True,
                include_memory=True,
            )
            return result if result else reply
        return reply

    # ── Saludos / cortesía ───────────────────────────────────────
    if intent in ("greet", "small_talk"):
        greet_messages = [
            {"role": "system", "content": MASTER_PROMPT},
            {
                "role": "system",
                "content": (
                    "El cliente solo saluda o hace cortesía. Responde corto y cálido. "
                    "No ofrezcas catálogo a la fuerza. No llames tools."
                ),
            },
            {"role": "user", "content": user_text},
        ]
        reply = await run_agent(
            greet_messages,
            wa_id=wa_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            session=session,
            use_external_crm=use_external_crm,
            persist=persist,
            tools_override=[],
            include_handoff=False,
            include_memory=False,
        )
        if conversation_id is not None:
            await save_state(conversation_id, state, wa_id=wa_id)
        return reply or "¡Hola! 😊 ¿En qué regalo te ayudo hoy?"

    # ── Escalate explícito ───────────────────────────────────────
    if intent == "escalate":
        state.handoff_reason = state.handoff_reason or "cliente pide asesor / frustración"
        if conversation_id is not None:
            await save_state(conversation_id, state, wa_id=wa_id)

    # ── Specialty LLM con toolset acotado ────────────────────────
    tools = tools_for(
        intent,
        with_memory=bool(contact_id or use_external_crm),
        with_handoff=conversation_id is not None,
    )
    # Inyectar excluir_ids hint en system
    specialty_messages = _build_specialty_messages(messages, intent=intent, state=state)
    if state.shown_product_ids and intent == "catalog_search":
        specialty_messages.insert(
            1,
            {
                "role": "system",
                "content": (
                    "Al llamar buscar_semantico pasa excluir_ids="
                    f"{state.shown_product_ids[-40:]}"
                ),
            },
        )

    reply = await run_agent(
        specialty_messages,
        wa_id=wa_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        session=session,
        use_external_crm=use_external_crm,
        persist=persist,
        tools_override=tools if tools else None,
        include_handoff=intent in ("escalate", "policy_faq", "checkout"),
        include_memory=True,
    )

    if reply and reply != HANDOFF_DONE:
        ids = _extract_shown_ids(reply)
        # También IDs pueden venir solo de tools; el historial del agent no está aquí.
        # Heurística: si el reply es listado, no siempre trae id — OK.
        if ids:
            state.patch({"shown_product_ids": ids})
        # Detectar nombre entre * * como producto elegido en cierre futuro
        m = re.search(r"\*([^*\n]{3,80})\*", reply)
        if m and intent == "catalog_search":
            # No forzar chosen; solo memoria blanda del último listado
            pass

    if conversation_id is not None:
        await save_state(conversation_id, state, wa_id=wa_id)

    return reply
