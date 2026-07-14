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
