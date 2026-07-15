"""Rangos horarios de entrega: fuente de verdad única.

Los consumen el FSM de cierre (`harness/checkout.py`) y los facts del prompt
(`prompts/facts.py`). Vive fuera de ambos paquetes porque `prompts` no puede
depender de `harness` sin crear un ciclo de importación.
"""
from __future__ import annotations

SCHEDULE_OPTIONS = (
    "1. Mañana temprano — 07:00 AM a 09:00 AM\n"
    "2. Mañana — 09:00 AM a 11:00 AM\n"
    "3. Mediodía — 11:00 AM a 02:00 PM\n"
    "4. Tarde — 02:00 PM a 05:00 PM\n"
    "5. Tarde-noche — 04:00 PM a 07:00 PM"
)

SCHEDULE_MAP = {
    "1": "07:00 AM a 09:00 AM",
    "2": "09:00 AM a 11:00 AM",
    "3": "11:00 AM a 02:00 PM",
    "4": "02:00 PM a 05:00 PM",
    "5": "04:00 PM a 07:00 PM",
}

# La API de pedidos (`POST /pedidos/temporales`) solo acepta cuatro franjas de
# inicio: 07:00, 10:00, 13:00 y 16:00 (API.md). Nuestras cinco opciones de cara al
# cliente se colapsan a esas cuatro. `hora_entrega_api` traduce del texto que
# guarda el FSM (`SCHEDULE_MAP`) al valor que la API espera.
SCHEDULE_API = {
    "1": "07:00",
    "2": "10:00",
    "3": "13:00",
    "4": "16:00",
    "5": "16:00",
}

# Texto de franja (lo que se guarda en `state.time_slot`) → hora de la API.
_SLOT_TO_API = {SCHEDULE_MAP[k]: SCHEDULE_API[k] for k in SCHEDULE_MAP}


def hora_entrega_api(time_slot: str) -> str | None:
    """Traduce la franja elegida al valor que acepta `POST /pedidos/temporales`.

    Devuelve `None` si no se puede reconocer: el que construye el pedido decide
    entonces no enviarlo (mejor no crear un pedido con una hora inválida que
    provocar un 422).
    """
    raw = (time_slot or "").strip()
    if not raw:
        return None
    if raw in _SLOT_TO_API:
        return _SLOT_TO_API[raw]
    # A veces llega ya como la hora canónica ("10:00") o como dígito de opción.
    if raw in SCHEDULE_API:  # "1".."5"
        return SCHEDULE_API[raw]
    if raw in {"07:00", "10:00", "13:00", "16:00"}:
        return raw
    return None
