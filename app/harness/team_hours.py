"""Horario de atención del equipo humano: fuente de verdad en código.

Lunes a viernes de 7:00 am a 10:00 pm y sábados de 7:00 am a 8:00 pm. Los domingos no
hay atención. Es lo que permite no prometer "en seguida" cuando no hay nadie.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_LIMA = ZoneInfo("America/Lima")
_OPEN_HOUR = 7
# Hora de cierre por día de la semana (lunes = 0). Domingo no abre.
_CLOSE_HOUR = {0: 22, 1: 22, 2: 22, 3: 22, 4: 22, 5: 20}
_DAY_NAMES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

TEAM_HOURS_TEXT = "lunes a viernes de 7:00 am a 10:00 pm y sábados de 7:00 am a 8:00 pm"


def _lima(now: datetime | None) -> datetime:
    return now.astimezone(_LIMA) if now is not None else datetime.now(_LIMA)


def team_is_open(now: datetime | None = None) -> bool:
    current = _lima(now)
    close = _CLOSE_HOUR.get(current.weekday())
    return close is not None and _OPEN_HOUR <= current.hour < close


def next_opening(now: datetime | None = None) -> str:
    """Cuándo vuelve a haber alguien, tal como se le dice al cliente."""
    current = _lima(now)
    if current.weekday() in _CLOSE_HOUR and current.hour < _OPEN_HOUR:
        return f"hoy a las {_OPEN_HOUR}:00 am"
    for days in range(1, 8):
        day = current + timedelta(days=days)
        if day.weekday() in _CLOSE_HOUR:
            cuando = "mañana" if days == 1 else f"el {_DAY_NAMES[day.weekday()]}"
            return f"{cuando} a las {_OPEN_HOUR}:00 am"
    return f"mañana a las {_OPEN_HOUR}:00 am"
