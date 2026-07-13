"""Máquina de estados del cierre de pedido (determinista)."""
from __future__ import annotations

import re
from typing import Any

from app.harness.state import ConversationState

SCHEDULE_OPTIONS = (
    "1. Mañana temprano — 07:00 AM a 09:00 AM\n"
    "2. Mañana — 09:00 AM a 11:00 AM\n"
    "3. Mediodía — 11:00 AM a 02:00 PM\n"
    "4. Tarde — 02:00 PM a 05:00 PM\n"
    "5. Tarde-noche — 04:00 PM a 07:00 PM"
)

_SCHEDULE_MAP = {
    "1": "07:00 AM a 09:00 AM",
    "2": "09:00 AM a 11:00 AM",
    "3": "11:00 AM a 02:00 PM",
    "4": "02:00 PM a 05:00 PM",
    "5": "04:00 PM a 07:00 PM",
}

_BUY_RE = re.compile(
    r"lo\s+quiero|me\s+lo\s+llevo|quiero\s+(ese|esa|este|esta|comprar)|"
    r"como\s+lo\s+(pido|reservo)|reserv[aoe]|me\s+gusta\s+est|"
    r"si\s+perfecto|dale\s+con\s+ese|ese\s+lo\s+pido",
    re.I,
)


def wants_checkout(text: str) -> bool:
    return bool(_BUY_RE.search(text or ""))


def parse_schedule(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw[0] in _SCHEDULE_MAP and (len(raw) == 1 or not raw[1].isdigit()):
        return _SCHEDULE_MAP[raw[0]]
    m = re.search(r"(\d{1,2})\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)", raw, re.I)
    if m:
        # Heurística: "09 AM a 11" → opción 2
        hour = int(m.group(1))
        if 7 <= hour < 9:
            return _SCHEDULE_MAP["1"]
        if 9 <= hour < 11:
            return _SCHEDULE_MAP["2"]
        if 11 <= hour < 14:
            return _SCHEDULE_MAP["3"]
        if 14 <= hour < 17:
            return _SCHEDULE_MAP["4"]
        if 16 <= hour <= 19:
            return _SCHEDULE_MAP["5"]
    low = raw.casefold()
    if "11:00" in low or "09" in low or "9 am" in low:
        return _SCHEDULE_MAP["2"]
    if "mediod" in low:
        return _SCHEDULE_MAP["3"]
    return None


def advance_checkout(state: ConversationState, user_text: str) -> tuple[ConversationState, str, dict[str, Any]]:
    """
    Avanza un paso del cierre. Devuelve (state, reply, meta).
    meta puede incluir escalate=True en payment tras confirmación.
    """
    step = state.checkout_step or "idle"
    text = (user_text or "").strip()
    meta: dict[str, Any] = {"specialty": "checkout"}

    if step == "idle" or (wants_checkout(text) and step == "idle"):
        if not state.district:
            state.checkout_step = "district"
            return state, "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?", meta
        state.checkout_step = "date"
        return state, "¿Para qué fecha lo necesitas? 📅", meta

    if step == "district":
        # No guardar intenciones de compra como nombre de distrito.
        if wants_checkout(text) or len(text) > 80:
            return state, "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?", meta
        if text:
            state.district = text
        if not state.district:
            return state, "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?", meta
        state.checkout_step = "date"
        return state, "¿Para qué fecha lo necesitas? 📅", meta

    if step == "date":
        if text:
            state.date = text
        state.checkout_step = "schedule"
        return (
            state,
            f"¿En qué horario prefieres que llegue? 🕐\n{SCHEDULE_OPTIONS}\n"
            "Responde con el número que prefieras.",
            meta,
        )

    if step == "schedule":
        slot = parse_schedule(text) or text
        state.time_slot = slot
        state.checkout_step = "card"
        return state, "¿Quieres incluir una tarjeta con mensaje? 💌", meta

    if step == "card":
        # Cualquier respuesta avanza; "sí" implica que pedirán el texto en el mismo flujo simple.
        low = text.casefold()
        if low.startswith("s") and "no" not in low[:4]:
            state.checkout_step = "summary"
            # Pedir texto y aún así preparar resumen en el siguiente turno sería ideal;
            # aquí pedimos el mensaje y guardamos flag liviano.
            return state, "¡Genial! ¿Qué texto quieres en la tarjeta?", {**meta, "await_card_text": True}

        state.checkout_step = "summary"
        return state, _summary_message(state) + "\n¿Todo correcto? 😊", meta

    if step == "summary":
        low = text.casefold()
        if any(x in low for x in ("si", "sí", "ok", "dale", "correcto", "perfecto", "yes")):
            state.checkout_step = "payment"
            state.handoff_reason = "cliente listo para pagar / coordinar comprobante"
            meta["escalate"] = True
            return (
                state,
                "Perfecto 🙌 Un asesor te comparte el link o las cuentas para pagar.",
                meta,
            )
        # Corrección → volver a fecha/distrito genérico
        state.checkout_step = "date"
        return state, "Sin problema. ¿Qué dato quieres corregir: distrito, fecha u horario?", meta

    if step == "payment":
        meta["escalate"] = True
        return state, "Un asesor de nuestro equipo continua contigo para el pago 🙏", meta

    return state, "¿En qué más te ayudo con tu pedido?", meta


def _summary_message(state: ConversationState) -> str:
    product = state.chosen_product_name or "Producto elegido"
    distrito = state.district or "—"
    fee = ""
    if state.shipping_fee_sol is not None:
        fee = f" — envío S/{state.shipping_fee_sol:.2f}"
    return (
        "📋 *Resumen del pedido:*\n"
        f"· Producto: {product}\n"
        f"· Distrito: {distrito}{fee}\n"
        f"· Fecha: {state.date or '—'}\n"
        f"· Horario: {state.time_slot or '—'}\n"
        "¿Todo correcto? 😊"
    )


def start_checkout(state: ConversationState, product_name: str = "", product_id: int | None = None) -> ConversationState:
    if product_name:
        state.chosen_product_name = product_name
    if product_id is not None:
        state.chosen_product_id = product_id
    state.checkout_step = "district" if not state.district else "date"
    return state
