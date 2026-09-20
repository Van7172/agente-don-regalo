"""Datos oficiales de Don Regalo que nunca debe improvisar el modelo."""
from __future__ import annotations

import re


COMPANY_RUC = "20548364222"
PICKUP_ADDRESS = "Calle La Habana 595, San Isidro, Lima"

_RUC_RE = re.compile(r"\bruc\b|raz[oó]n\s+(?:social|fiscal)", re.I)
_INVOICE_RE = re.compile(r"\bfactura(?:ci[oó]n|s)?\b|\bfacturar\b", re.I)
_PICKUP_RE = re.compile(
    r"\b(?:punto|lugar|direcci[oó]n|sede)\s+(?:de\s+)?(?:recojo|recogida)\b|"
    r"\bd[oó]nde\s+(?:puedo\s+)?(?:recoger|recojo)\b",
    re.I,
)


def asks_for_business_data(text: str) -> bool:
    """Reconoce únicamente consultas cuya respuesta oficial está fijada aquí."""
    return bool(
        _RUC_RE.search(text) or _INVOICE_RE.search(text) or _PICKUP_RE.search(text)
    )


def business_data_reply(text: str) -> str:
    """Responde solo los datos preguntados, sin exponer información innecesaria."""
    asks_ruc = bool(_RUC_RE.search(text))
    asks_invoice = bool(_INVOICE_RE.search(text))
    asks_pickup = bool(_PICKUP_RE.search(text))

    parts: list[str] = []
    if asks_ruc:
        parts.append(f"Sí, somos una empresa formal 😊 Nuestro RUC es *{COMPANY_RUC}*.")
    if asks_invoice:
        parts.append("Sí, emitimos factura.")
    if asks_pickup:
        parts.append(f"Nuestro punto de recojo es *{PICKUP_ADDRESS}*.")

    # La función solo se llama tras ``asks_for_business_data``; este fallback
    # mantiene una respuesta útil si mañana se amplía el detector por separado.
    return "\n".join(parts) or f"Nuestro RUC es *{COMPANY_RUC}*."
