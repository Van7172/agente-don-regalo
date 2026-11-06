"""District, customer landmark and handoff point are separate delivery facts."""
from __future__ import annotations

import re

from app.harness.delivery_request import URL_RE
from app.harness.state import ConversationState
from app.harness.aliases import resolve_alias

_LANDMARK = re.compile(
    r"^(?:(?:es|ser[ií]a|queda|est[aá])\s+(?:en\s+)?|(?:para|en|al)\s+|"
    r"(?:la\s+)?(?:direcci[oó]n|referencia|entrega)\s*(?::|es|ser[ií]a)?\s*(?:en\s+)?)?"
    r"(?:el\s+|la\s+)?(?:aeropuerto|hospital|cl[ií]nica|centro\s+comercial|mall|"
    r"universidad|colegio|hotel|edificio|condominio|urbanizaci[oó]n|"
    r"calle|avenida|av\.?|jir[oó]n|jr\.?)\b[^\n?!]*$", re.I,
)
_POINT = re.compile(r"\b(?:puerta|ingreso|entrada|salida|llegadas|salidas|recepci[oó]n|"
                    r"porter[ií]a|piso|local|m[oó]dulo|estacionamiento|zona\s+p[uú]blica)\b", re.I)
_COMMERCIAL = re.compile(r"\b(?:desayuno|regalo|ramo|rosas|peluche|flores|precio|cu[aá]nto|"
                         r"contiene|venden|busco|quiero\s+comprar)\b", re.I)


def delivery_reference(text: str) -> str:
    raw = " ".join(URL_RE.sub("", text or "").split()).strip(" .")
    if not 5 <= len(raw) <= 180 or _COMMERCIAL.search(raw) or not _LANDMARK.fullmatch(raw):
        return ""
    return re.sub(r"^(?:(?:es|ser[ií]a|queda|est[aá])\s+(?:en\s+)?|(?:para|en|al)\s+|"
                  r"(?:la\s+)?(?:direcci[oó]n|referencia|entrega)\s*(?::|es|ser[ií]a)?\s*(?:en\s+)?)", "", raw, flags=re.I).strip()


def delivery_point(text: str) -> str:
    raw = " ".join(URL_RE.sub("", text or "").split()).strip(" .")
    return raw if 3 <= len(raw) <= 180 and _POINT.search(raw) and not _COMMERCIAL.search(raw) and "?" not in raw else ""


def reference_district(text: str) -> str:
    # 'Hotel San Isidro' is a business name, not an asserted district. Only
    # explicit destination suffixes can change a district alongside a landmark.
    match = re.search(r"(?:,\s*|\s+en\s+|\bdistrito\s+(?:de\s+)?)([^,\n]+)$", text or "", re.I)
    return resolve_alias(match[1]) or "" if match else ""


def capture_reference(text: str, state: ConversationState) -> bool:
    reference = delivery_reference(text)
    if not reference or state.checkout_step in ("card_text", "recipient", "contact"):
        return False
    # A street address answering the address form already has a parser. Do not
    # turn it into a new landmark question and delay the remaining form fields.
    return not (state.checkout_step == "address" and re.match(
        r"^(?:el\s+|la\s+)?(?:calle|avenida|av\.?|jir[oó]n|jr\.?)\b", reference, re.I,
    ))


def point_question(state: ConversationState) -> str:
    return f"¿En qué punto de {state.delivery_reference} recibirían el regalo?"


def maps_confirmation_question(state: ConversationState) -> str:
    if state.maps_confirmation_kind == "destination":
        context = f"{state.delivery_reference}, en {state.maps_district_candidate}" if state.delivery_reference else state.maps_district_candidate
        return f"Recibí la ubicación. ¿Corresponde al punto de entrega que me indicaste en {context}?"
    if state.maps_district_candidate:
        return f"Según el enlace de Maps proporcionado, ¿la dirección queda en {state.maps_district_candidate}?"
    return "¿En qué distrito queda la dirección del enlace de Maps?"


def delivery_address(state: ConversationState) -> str:
    """Carry confirmed location facts into the order, not only chat memory."""
    parts = [state.direccion] if state.direccion else []
    for value in (state.delivery_reference, state.delivery_point):
        if value and not any(value.casefold() in part.casefold() for part in parts):
            parts.append(value)
    if state.maps_location_confirmed and state.maps_url and not state.maps_location_pending and not any(state.maps_url in part for part in parts):
        parts.append(state.maps_url)
    return " | ".join(parts)
