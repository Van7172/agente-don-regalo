"""Presupuesto del cliente: se reconoce en código y se aplica en código.

Es dinero, así que sigue la misma regla que el precio: ni lo interpreta ni lo convierte
el modelo. Solo cuenta un número con moneda o con una palabra que lo señale como tope:
"hasta las 10", "2 desayunos" o "Av. Lima 100" no son presupuestos.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from app.harness.contracts import Product

# Lo que se enseña cuando nada cabe: pocas opciones, las más cercanas.
_CLOSEST_LIMIT = 3
_MIN_AMOUNT = 10.0

_AMOUNT = r"(\d{2,5}(?:[.,]\d{1,2})?)"
_USD_RE = re.compile(rf"\$\s*{_AMOUNT}|\b{_AMOUNT}\s*(?:dolares|dolar|usd)\b")
_PEN_RE = re.compile(rf"\bs/\.?\s*{_AMOUNT}|\b{_AMOUNT}\s*(?:soles|sol|lucas|pen)\b")
_TOPE_RE = re.compile(
    rf"\bpresupuesto\b[^\d\n]{{0,20}}{_AMOUNT}|"
    r"\b(?:hasta|maximo|maxima|no\s+mas\s+de|menos\s+de|como\s+mucho)\s+(?:de\s+)?"
    rf"(?:\$\s*|s/\.?\s*)?{_AMOUNT}\b"
    r"(?!\s*(?:personas?|unidades|de\s+\w+|am\b|pm\b))"
)
_USD_NEAR_RE = re.compile(r"\$|\bdolar|\busd\b")


def _plain(text: str) -> str:
    nfd = unicodedata.normalize("NFD", (text or "").casefold())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _first_amount(match: re.Match) -> Optional[float]:
    for group in match.groups():
        if group:
            return float(group.replace(",", "."))
    return None


def extract_budget(text: str) -> Optional[tuple[float, str]]:
    """`(importe, moneda)` si el mensaje da un presupuesto; `None` si no.

    El tope explícito manda sobre cualquier otra cifra: en "tengo 200 soles pero puedo
    gastar hasta 100", el presupuesto son 100.
    """
    plain = _plain(text)
    tope = _TOPE_RE.search(plain)
    if tope:
        amount = _first_amount(tope)
        if amount is not None and amount >= _MIN_AMOUNT:
            contexto = plain[tope.start() : tope.end() + 9]
            return amount, "USD" if _USD_NEAR_RE.search(contexto) else "PEN"
    for pattern, currency in ((_USD_RE, "USD"), (_PEN_RE, "PEN")):
        match = pattern.search(plain)
        if match:
            amount = _first_amount(match)
            if amount is not None and amount >= _MIN_AMOUNT:
                return amount, currency
    return None


def _price(product: Product, currency: str) -> Optional[float]:
    return product.precio_usd if currency == "USD" else product.precio_sol


def fit_budget(
    products: list[Product],
    amount: Optional[float],
    currency: Optional[str],
    *,
    shipping: Optional[float] = None,
) -> tuple[list[Product], bool]:
    """`(productos, excedido)`: lo que cabe o, si nada cabe, lo más cercano.

    El envío cuenta si ya se conoce: quien dice "tengo 100" habla de lo que va a
    pagar. `excedido` obliga a avisar de que no son lo que pidió. Un producto sin
    precio se conserva: no hay forma de saber que se pasa.
    """
    if not amount or not currency or not products:
        return products, False
    limit = amount - (shipping or 0)
    if limit <= 0:
        limit = amount
    fit = [p for p in products if (_price(p, currency) or 0) <= limit]
    if fit:
        return fit, False
    closest = sorted(products, key=lambda p: _price(p, currency) or 0)
    return closest[:_CLOSEST_LIMIT], True


def budget_label(amount: float, currency: Optional[str]) -> str:
    return f"${amount:.2f}" if currency == "USD" else f"S/{amount:.2f}"
