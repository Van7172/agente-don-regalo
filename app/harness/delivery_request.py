"""Delivery facts supplied by the customer, distinct from accepted commitments."""
from __future__ import annotations

import re
from datetime import date

from app.delivery_windows import windows_for
from app.harness.orders import lima_today, normalize_fecha
from app.harness.state import ConversationState

URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_RANGE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)?\s*(?:-|a\b|hasta\b|y\b)\s*\d{1,2}", re.I)
_CLOCK = re.compile(r"\b(?:a\s+las\s+|las\s+)(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.?\s*m\.?|p\.?\s*m\.?)?|\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.?\s*m\.?|p\.?\s*m\.?)\b|\b(\d{1,2}):(\d{2})\b|\b(\d{1,2})\s+en\s+punto\b", re.I)
_DATE = re.compile(r"\b(?:hoy|mañana|lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\b|\b\d{1,2}\s+(?:de\s+)?(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|setiembre|septiembre|octubre|noviembre|diciembre)\b|\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b|\b\d{1,2}/\d{1,2}\b", re.I)


def requested_clock_time(text: str) -> str:
    """A single requested hour is never consent to a two-hour window."""
    raw = URL_RE.sub("", text or "")
    if _RANGE.search(raw):
        return ""
    match = _CLOCK.search(raw)
    if not match:
        return ""
    hour = int(match[1] or match[4] or match[7] or match[9])
    minute = int(match[2] or match[5] or match[8] or 0)
    marker = re.sub(r"[.\s]", "", match[3] or match[6] or "").lower()
    if minute > 59 or hour > 23 or (marker and not 1 <= hour <= 12):
        return ""
    if marker == "pm" and hour < 12:
        hour += 12
    elif marker == "am" and hour == 12:
        hour = 0
    elif not marker and 1 <= hour <= 6:
        hour += 12
    return f"{hour:02d}:{minute:02d}"


def is_window_reply(text: str, *, allow_menu: bool = False) -> bool:
    raw = (text or "").strip().casefold().strip(" .!¿?")
    return bool(_RANGE.search(raw)) or raw in {
        "mañana", "manana", "en la mañana", "por la mañana", "temprano",
        "mañana temprano", "al mediodía", "mediodía", "en la tarde", "tarde", "tarde-noche",
    } or (allow_menu and raw in {"1", "2", "3", "4", "5"})


def flexible_time_reply(text: str) -> bool:
    return bool(re.search(
        r"\bno\s+(?:es\s+(?:indispensable|necesari[oa])|necesito|importa|tiene\s+que)|"
        r"\bcualquier\s+hora\b|\bpuede\s+ser\s+(?:en\s+)?el\s+rango\b", text or "", re.I,
    ))


def remember_delivery_date(state: ConversationState, text: str, *, today: date | None = None) -> None:
    raw = URL_RE.sub("", text or "")
    if state.checkout_step == "schedule" and raw.strip().casefold() in ("mañana", "manana"):
        return
    # 'por la mañana' is a time of day, not tomorrow.
    raw = re.sub(r"\b(?:por|en)\s+la\s+mañana\b", "", raw, flags=re.I)
    if not _DATE.search(raw):
        return
    today = today or lima_today()
    parsed = normalize_fecha(raw, today=today)
    if parsed and parsed >= today.isoformat():
        from app.harness.holidays import is_closed_delivery
        if is_closed_delivery(parsed) or not windows_for(parsed, breakfast=state.chosen_is_breakfast is True):
            return
        if parsed == today.isoformat() and state.chosen_is_breakfast is not False:
            return  # breakfast/unknown category requires advance ordering validation
        if state.date != parsed:
            state.time_slot = ""
        state.date = parsed


def time_confirmation_reply(state: ConversationState) -> str:
    hour, minute = map(int, state.requested_delivery_time.split(":"))
    label = f"{hour % 12 or 12}{f':{minute:02d}' if minute else ''} {'a. m.' if hour < 12 else 'p. m.'}"
    available = windows_for(state.date or None, breakfast=state.chosen_is_breakfast is True)
    containing = next((w for w in available if int(w.display[:2]) <= hour < _end_hour(w.display)), None)
    if containing:
        start = int(containing.display[:2])
        end = _end_hour(containing.display)
        marker = "a. m." if end < 12 else "p. m."
        prefix = f"El rango de entrega es de {start} a {end % 12 or 12} {marker}; "
    else:
        prefix = "Esa hora no está dentro de las franjas disponibles para la entrega; "
    return f"{prefix}las {label} en punto necesitan confirmación del equipo. ¿Es indispensable que llegue a esa hora?"


def _end_hour(display: str) -> int:
    end = display.split(" a ")[-1]
    hour = int(end[:2])
    return hour + 12 if "PM" in end and hour < 12 else hour
