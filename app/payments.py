"""Respuesta determinista para consultas sobre medios de pago."""
from __future__ import annotations

import re


PAYMENT_METHODS_IMAGE_URL = (
    "https://donregalo.pe/crm/public/assets/metodos-pago-qr.png"
)
PAYMENT_RECEIPT_WHATSAPP = "977 174 485"

_PAYMENT_METHODS_RE = re.compile(
    r"\b(?:m[eé]todos?|formas?|medios?)\s+de\s+pago\b|"
    r"\b(?:c[oó]mo|d[oó]nde)\s+(?:puedo\s+)?pagar\b|"
    r"\b(?:aceptan|reciben)\s+(?:pago\s+(?:con|por)\s+)?"
    r"(?:yape|plin|tarjeta|transferencia|dep[oó]sito)\b|"
    r"\bpuedo\s+pagar\s+(?:con|por|mediante|en)\b|"
    r"\b(?:pago|pagar)\s+(?:con|por)\s+(?:yape|plin|tarjeta|transferencia)\b|"
    r"\b(?:datos?|cuentas?)\s+bancari|"
    r"\b(?:qr|c[oó]digo\s+qr)\s+(?:de\s+)?(?:pago|yape|plin)\b|"
    r"\b(?:cu[aá]l\s+es|p[aá]same|dame|env[ií]ame)\b[^\n]{0,30}"
    r"\b(?:yape|plin|qr|cuenta)\b|"
    r"\bn[uú]mero\s+(?:de\s+)?(?:yape|plin)\b|"
    r"\b(?:yape|plin)\s+o\s+(?:yape|plin)\b|"
    r"^\s*¿?\s*(?:yape|plin|tarjeta|transferencia)\s*\??\s*$|"
    r"\bcontra\s*entrega\b|\bcontraentrega\b",
    re.I,
)


def asks_for_payment_methods(text: str) -> bool:
    """Distingue una consulta/selección de medio de pago de un pago ya hecho."""
    return bool(_PAYMENT_METHODS_RE.search(text or ""))


def _image_block() -> str:
    return (
        f"{PAYMENT_METHODS_IMAGE_URL}\n\n"
        "Escanea el QR de Yape o Plin que prefieras, o utiliza una de las "
        "cuentas bancarias. Cuando pagues, envía el comprobante al WhatsApp "
        f"del equipo: *{PAYMENT_RECEIPT_WHATSAPP}* para validarlo 😊"
    )


def payment_methods_reply(text: str = "") -> str:
    """La URL queda sola en su línea para que WhatsApp la convierta en imagen."""
    if re.search(r"\bcontra\s*entrega\b|\bcontraentrega\b", text or "", re.I):
        intro = (
            "Trabajamos únicamente con pago anticipado. Te comparto nuestros "
            "medios de pago oficiales 💳"
        )
    else:
        intro = (
            "¡Claro! 💳 Te comparto nuestros medios de pago oficiales con QR "
            "y cuentas bancarias:"
        )
    return f"{intro}\n\n{_image_block()}"


def append_payment_methods_image(reply: str) -> str:
    """Añade la imagen a una respuesta mixta sin duplicarla."""
    clean = (reply or "").strip()
    if PAYMENT_METHODS_IMAGE_URL in clean:
        return clean
    return f"{clean}\n\n{_image_block()}" if clean else payment_methods_reply()
