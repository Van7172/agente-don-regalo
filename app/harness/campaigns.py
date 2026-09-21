"""Campañas que viven fuera del catálogo regular.

Algunas preventas se publican como un PDF curado antes de que sus productos
entren a la API. En esos casos buscar semánticamente en el catálogo general
mezcla productos que no pertenecen a la campaña. La detección y la entrega del
documento se mantienen deterministas para que el cliente reciba exactamente el
catálogo indicado por ventas.
"""
from __future__ import annotations

import re
import unicodedata


YELLOW_FLOWERS_SLUG = "flores-amarillas"
YELLOW_FLOWERS_CATALOG_URL = (
    "https://www.donregalo.pe/catalogo/"
    "catalogodepreventaFLORESAMARILLAS_.pdf"
)

_YELLOW_FLOWERS_RE = re.compile(
    r"\bflor(?:es)?\s+(?:de\s+color\s+)?amarill(?:o|os|a|as)\b",
    re.IGNORECASE,
)


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or ""))
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).casefold()


def is_yellow_flowers_query(text: str) -> bool:
    """Reconoce la campaña sin confundirla con flores de cualquier color."""
    return bool(_YELLOW_FLOWERS_RE.search(_plain(text)))


def yellow_flowers_catalog_reply() -> str:
    """Aviso de corte: ya no hay capacidad de entrega same-day para la campaña."""
    return (
        "Por capacidad de producción y delivery, *ya no estamos tomando pedidos "
        "de Flores Amarillas con entrega para hoy* 🌻\n"
        "Si quieres, te ayudo a coordinar una entrega para *mañana* u otra fecha.\n"
        "¿Te parece bien avanzar con otra fecha?"
    )
