"""Invariantes: lo que una respuesta del bot NUNCA debe hacer.

Cada regla de aquí nació de un incidente real en producción. Son funciones puras
sobre `(estado, respuesta, artifacts)`, así que sirven para dos cosas a la vez:

- en runtime, el orquestador las evalúa y deja la violación en el log/traza;
- en los evals, el corpus las aplica a conversaciones reales grabadas.

Antes, cada incidente se arreglaba con una regex nueva al lado de la anterior y
nadie sabía si el parche de hoy rompía el de la semana pasada. Esto es lo que
convierte esos parches en una red de regresión.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.harness.contracts import Product
from app.harness.state import ConversationState

# Reconoce una URL de imagen dentro de una línea (igual que el emisor de WhatsApp).
_IMG_URL = re.compile(r"https?://[^\s<>\"']+\.(?:jpe?g|png|webp|gif)", re.I)

# Medios de pago que NO existen en Don Regalo. El bot llegó a preguntar
# "¿prefieres pagar en línea (tarjeta/PSE) o contra entrega?": ninguna de las dos.
_FAKE_PAYMENT = re.compile(
    r"contra\s*entrega|contraentrega|contra\s*reembolso|"
    r"pago\s+en\s+efectivo\s+al\s+recibir|efectivo\s+al\s+recibir|"
    r"pagar\s+al\s+repartidor|\bPSE\b|\bNequi\b|\bDaviplata\b",
    re.I,
)

_PRICE_SOL = re.compile(r"S/\s?(\d+(?:[.,]\d{1,2})?)")


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formato de log
        return f"{self.rule}: {self.detail}"


def no_repeated_products(state: ConversationState, artifacts: list[Product]) -> Violation | None:
    """Un producto ya mostrado no se vuelve a mostrar.

    El cliente lo lee como que no le hicimos caso: "otras, no esas".
    """
    ya_vistos = set(state.shown_product_ids or [])
    repetidos = sorted({p.id_producto for p in artifacts if p.id_producto in ya_vistos})
    if repetidos:
        return Violation("no_repeated_products", f"ids ya mostrados: {repetidos}")
    return None


def no_duplicates_within_reply(artifacts: list[Product]) -> Violation | None:
    """Ni siquiera dos veces dentro del mismo paquete."""
    vistos: set[int] = set()
    for p in artifacts:
        if p.id_producto in vistos:
            return Violation("no_duplicates_within_reply", f"id {p.id_producto} duplicado")
        vistos.add(p.id_producto)
    return None


def no_cash_on_delivery(reply: str) -> Violation | None:
    """Don Regalo cobra por adelantado. No hay contraentrega, ni PSE (es colombiano)."""
    m = _FAKE_PAYMENT.search(reply or "")
    if m:
        return Violation("no_cash_on_delivery", f"ofrece un medio inexistente: {m.group(0)!r}")
    return None


def image_urls_on_own_line(reply: str) -> Violation | None:
    """La URL de imagen va sola en su línea, o llega al cliente como link, no como foto."""
    for line in (reply or "").split("\n"):
        m = _IMG_URL.search(line)
        if not m:
            continue
        resto = (line[: m.start()] + line[m.end():]).strip()
        if resto:
            return Violation(
                "image_urls_on_own_line",
                f"URL pegada al texto {resto[:40]!r}: llegará como link",
            )
    return None


def prices_are_sourced(reply: str, artifacts: list[Product]) -> Violation | None:
    """Todo precio en soles debe venir de una tool, no del modelo.

    Es el fallo más caro del negocio: un precio inventado que el cliente da por
    bueno. Si el turno no citó productos, no hay nada contra qué comparar.
    """
    if not reply or not artifacts:
        return None

    respaldados = {
        f"{float(v):.2f}"
        for p in artifacts
        for v in (p.precio_sol,)
        if v is not None
    }
    if not respaldados:
        return None

    for m in _PRICE_SOL.finditer(reply):
        citado = f"{float(m.group(1).replace(',', '.')):.2f}"
        if citado not in respaldados:
            return Violation(
                "prices_are_sourced",
                f"S/{m.group(1)} no salió de ninguna tool de este turno",
            )
    return None


def check_reply(
    reply: str | None,
    *,
    state: ConversationState | None = None,
    artifacts: list[Product] | None = None,
) -> list[Violation]:
    """Todas las invariantes aplicables a una respuesta. Lista vacía = limpia."""
    reply = reply or ""
    artifacts = artifacts or []
    state = state or ConversationState()

    candidatas = [
        no_cash_on_delivery(reply),
        image_urls_on_own_line(reply),
        prices_are_sourced(reply, artifacts),
        no_repeated_products(state, artifacts),
        no_duplicates_within_reply(artifacts),
    ]
    return [v for v in candidatas if v is not None]
