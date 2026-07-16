"""Auto-retorno HUMAN → AI cuando el asesor olvida reactivar Modo AI."""
from __future__ import annotations

import logging
import time
from typing import Any

from app.config import settings
from app.crm import http_client as crm_http
from app.harness.state import ConversationState, load_state, save_state

log = logging.getLogger(__name__)

# Defaults del plan.
_T_IDLE_SEC = 20 * 60
_T_PAYMENT_SEC = 2 * 60 * 60

REENGAGE_MSG = "¡Hola de nuevo! 😊 Ya estoy aquí para seguir ayudándote."


def idle_threshold_sec(state: ConversationState) -> float:
    if state.keep_human:
        return float("inf")
    if state.is_payment_handoff():
        return float(getattr(settings, "harness_payment_releaser_sec", _T_PAYMENT_SEC))
    return float(getattr(settings, "harness_releaser_sec", _T_IDLE_SEC))


def last_human_outbound_epoch(messages: list[dict[str, Any]] | None) -> float | None:
    """Último outbound del asesor (sender_type=agent|human)."""
    if not messages:
        return None
    latest: float | None = None
    for m in messages:
        st = (m.get("sender_type") or m.get("role") or "").lower()
        direction = (m.get("direction") or "").lower()
        if st in ("agent", "human") or (direction == "outbound" and st == "human"):
            ts = m.get("created_at") or m.get("fecha_creacion") or m.get("timestamp")
            epoch = _parse_ts(ts)
            if epoch is not None and (latest is None or epoch > latest):
                latest = epoch
        # También contar role human en content history
        if (m.get("role") or "").lower() == "human":
            epoch = _parse_ts(m.get("created_at"))
            if epoch is not None and (latest is None or epoch > latest):
                latest = epoch
    return latest


def _parse_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        # ISO / MySQL datetime
        from datetime import datetime

        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(s.replace("Z", "+0000"), fmt)
                return dt.timestamp()
            except ValueError:
                continue
    except Exception:
        pass
    return None


def should_release_to_ai(
    *,
    mode: str,
    human_support: bool,
    state: ConversationState,
    last_human_at: float | None,
    now: float | None = None,
) -> bool:
    if (mode or "").upper() != "HUMAN" and not human_support:
        return False
    # Solo liberamos cuando estaba en modo humano explícito o human_support stuck.
    if state.keep_human:
        return False
    now = now if now is not None else time.time()
    # Preferir timestamp en estado si es más reciente. Si el asesor aún no escribió,
    # el ancla es el momento en que se le cedió el chat: así "lleva X sin contestar"
    # se mide desde algo real.
    anchor = state.last_human_outbound_at or last_human_at or state.handoff_at
    if anchor is None:
        # Sin ancla no hay forma de medir cuánto lleva el asesor con el chat, y sin
        # medirlo esto liberaba AL INSTANTE: el bot prometía un asesor y recuperaba
        # la conversación con el siguiente mensaje del cliente. Un humano tiene el
        # chat: no se le quita a ciegas (y siempre le queda "Devolver a Regalito").
        return False
    return (now - float(anchor)) >= idle_threshold_sec(state)


async def try_release_conversation(
    conversation_id: int,
    *,
    wa_id: str,
    conv: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
) -> tuple[bool, ConversationState]:
    """
    Si aplica, pasa a AI y limpia human_support.
    Devuelve (released, state).
    """
    state = await load_state(conversation_id, wa_id=wa_id)
    mode = str(conv.get("mode") or "")
    human_support = bool(conv.get("human_support"))

    # Preferir settings del CRM (actividad asesor + pin keep_human).
    if crm_http.crm_enabled():
        try:
            keep = await crm_http.get_setting(f"keep_human_{conversation_id}")
            if keep == "1":
                state.keep_human = True
            last_raw = await crm_http.get_setting(f"last_human_outbound_{conversation_id}")
            if last_raw and str(last_raw).isdigit():
                state.last_human_outbound_at = float(last_raw)
        except Exception as err:
            log.warning("[harness.releaser] settings: %s", err)

    last_h = state.last_human_outbound_at or last_human_outbound_epoch(messages)

    if not should_release_to_ai(
        mode=mode,
        human_support=human_support,
        state=state,
        last_human_at=last_h,
    ):
        return False, state

    log.info(
        "[harness.releaser] conversation=%s HUMAN→AI (idle)",
        conversation_id,
    )
    try:
        if crm_http.crm_enabled():
            await crm_http.set_mode(conversation_id, "AI", human_support=False)
            await crm_http.put_setting(f"keep_human_{conversation_id}", "0")
    except Exception as err:
        log.warning("[harness.releaser] set_mode falló: %s", err)
        return False, state

    state.keep_human = False
    await save_state(conversation_id, state, wa_id=wa_id)
    return True, state
