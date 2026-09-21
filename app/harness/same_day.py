"""Corte de capacidad: no se aceptan entregas para el mismo día.

Ventas cerró la ventana de producción/delivery de hoy. El aviso es
determinista: el cliente lo recibe igual en el cierre, al pedir "para hoy" o
al consultar Flores Amarillas, sin depender de que un modelo copie bien la
regla.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from app.harness.orders import lima_today

SAME_DAY_CUTOFF_REPLY = (
    "Por capacidad de producción y delivery, *ya no estamos tomando pedidos "
    "con entrega para hoy* 🙏\n"
    "Sí podemos agendar tu pedido para *mañana* u otra fecha.\n"
    "¿Para qué día te gustaría recibirlo?"
)

_SAME_DAY_INTENT_RE = re.compile(
    r"(?:"
    r"(?:para|por|de)\s+hoy(?:\s+mismo)?|"
    r"(?:entrega|entregarlo|entregar|envio|env[ií]o|enviarlo|"
    r"llega|llegan|llegue|llegar|recibirlo|recibir)\s+(?:para\s+)?hoy|"
    r"(?:lo\s+)?(?:quiero|necesito|pido)\s+(?:para\s+)?hoy|"
    r"hoy\s+mismo|"
    r"can\s+you\s+deliver\s+today"
    r")",
    re.IGNORECASE,
)

# "todo en orden hoy" / "¿qué tienen hoy?" no piden entrega same-day.
_BENIGN_HOY_RE = re.compile(
    r"\b(?:todo\s+en\s+orden|buenos?\s+d[ií]as?|qu[eé]\s+tienen|"
    r"disponible|cat[aá]logo|opciones?)\b.*\bhoy\b|"
    r"\bhoy\b.*\b(?:cat[aá]logo|disponible|opciones?)\b",
    re.IGNORECASE,
)


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or ""))
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).casefold()


def same_day_cutoff_reply() -> str:
    return SAME_DAY_CUTOFF_REPLY


def is_same_day_delivery(day: date | str | None, *, today: date | None = None) -> bool:
    """True si la fecha pedida es el día calendario actual en Lima."""
    if day is None:
        return False
    effective = today or lima_today()
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day[:10])
        except ValueError:
            return False
    return day == effective


def asks_for_same_day_delivery(text: str) -> bool:
    """Detecta pedido/consulta de entrega para hoy, sin cortesía genérica."""
    plain = _plain(text)
    if not plain or _BENIGN_HOY_RE.search(plain):
        return False
    return bool(_SAME_DAY_INTENT_RE.search(plain))
