"""Rangos horarios de entrega: fuente de verdad única.

Los consumen el FSM de cierre (`harness/checkout.py`) y los facts del prompt
(`prompts/facts.py`). Vive fuera de ambos paquetes porque `prompts` no puede
depender de `harness` sin crear un ciclo de importación.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DeliveryWindow:
    label: str
    display: str
    api_hour: str


_WINDOWS = (
    DeliveryWindow("Mañana temprano", "07:00 AM a 09:00 AM", "07:00"),
    DeliveryWindow("Mañana", "09:00 AM a 11:00 AM", "10:00"),
    DeliveryWindow("Mediodía", "11:00 AM a 02:00 PM", "13:00"),
    DeliveryWindow("Tarde", "02:00 PM a 05:00 PM", "16:00"),
    DeliveryWindow("Tarde-noche", "04:00 PM a 07:00 PM", "16:00"),
)


def _as_date(value: date | str | None) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def windows_for(delivery_date: date | str | None = None) -> tuple[DeliveryWindow, ...]:
    parsed = _as_date(delivery_date)
    return _WINDOWS[1:] if parsed and parsed.weekday() == 4 else _WINDOWS


def schedule_map_for(delivery_date: date | str | None = None) -> dict[str, str]:
    return {
        str(index): window.display
        for index, window in enumerate(windows_for(delivery_date), start=1)
    }


def schedule_options_for(delivery_date: date | str | None = None) -> str:
    return "\n".join(
        f"{index}. {window.label} — {window.display}"
        for index, window in enumerate(windows_for(delivery_date), start=1)
    )


SCHEDULE_OPTIONS = schedule_options_for()
SCHEDULE_MAP = schedule_map_for()

# La API de pedidos (`POST /pedidos/temporales`) solo acepta cuatro franjas de
# inicio: 07:00, 10:00, 13:00 y 16:00 (API.md). Nuestras cinco opciones de cara al
# cliente se colapsan a esas cuatro. `hora_entrega_api` traduce del texto que
# guarda el FSM (`SCHEDULE_MAP`) al valor que la API espera.
SCHEDULE_API = {
    str(index): window.api_hour for index, window in enumerate(_WINDOWS, start=1)
}

# Texto de franja (lo que se guarda en `state.time_slot`) → hora de la API.
_SLOT_TO_API = {window.display: window.api_hour for window in _WINDOWS}


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
