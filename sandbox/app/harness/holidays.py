"""Fiestas Patrias 2026: sin personal humano, sí pedidos desde el 30.

El 28 y 29 de julio 2026 Don Regalo no tiene asesores. El bot sigue: catálogo,
cobertura y cierre, pero la entrega más temprana es el 30. Si alguien pide
hablar con una persona, se avisa y NO se cede el chat (cola AYUDA vacía no
ayuda a nadie).
"""
from __future__ import annotations

from datetime import date

from app.harness.orders import display_fecha, lima_today

# Entrega cerrada estos días (feriado). Pedidos se programan desde NEXT_OPEN.
CLOSED_DELIVERY_DATES: frozenset[date] = frozenset(
    {
        date(2026, 7, 28),
        date(2026, 7, 29),
    }
)
NEXT_OPEN_DELIVERY: date = date(2026, 7, 30)


def is_closed_delivery(day: date | str | None) -> bool:
    if day is None:
        return False
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day[:10])
        except ValueError:
            return False
    return day in CLOSED_DELIVERY_DATES


def staff_offline(today: date | None = None) -> bool:
    """Hoy (Lima) es feriado sin asesores."""
    return (today or lima_today()) in CLOSED_DELIVERY_DATES


def next_open_display() -> str:
    return display_fecha(NEXT_OPEN_DELIVERY.isoformat())


def closed_delivery_reply() -> str:
    abierto = next_open_display()
    return (
        "El *28 y 29 de julio* son feriados (Fiestas Patrias) y no hacemos "
        f"entregas esos días 🇵🇪 ¿Te programo el pedido desde el *{abierto}*? "
        "Dime la fecha que prefieras 📅"
    )


def staff_offline_reply(*, for_payment: bool = False) -> str:
    abierto = next_open_display()
    if for_payment:
        return (
            "Tu pedido quedó registrado ✅ El *28 y 29 de julio* no hay asesores "
            f"por Fiestas Patrias. Desde el *{abierto}* el equipo te escribe para "
            "confirmar el pago. Si quieres, te indico ya los medios de pago "
            "(Yape, transferencia o tarjeta)."
        )
    return (
        "Hoy *28 y 29 de julio* no hay asesores por Fiestas Patrias 🇵🇪 "
        f"Puedo ayudarte yo a armar tu pedido para entrega desde el *{abierto}*. "
        "¿Qué regalo buscas o para qué fecha lo necesitas?"
    )
