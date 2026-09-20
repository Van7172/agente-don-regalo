"""Atributos duros del pedido (color, etc.).

Misma lección que `enforce_category`: si el cliente nombra un color, no es una
sugerencia para el ranking — es un límite. El bonus léxico de Qdrant (+0.12) no
basta: un "Ramo de 1 rosa" semánticamente cercano a "flores" se colaba delante
de las amarillas reales.
"""
from __future__ import annotations

import logging
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass

log = logging.getLogger(__name__)

# El turno del cliente viaja aquí porque el modelo a veces busca con `q="flores"`
# o `catalogo_categoria` y se come el color. Sin el texto original no hay forma
# de imponer el límite en esos caminos.
_TURN_TEXT: ContextVar[str] = ContextVar("donregalo_attr_turn", default="")


def set_turn_text(text: str):
    return _TURN_TEXT.set(text or "")


def reset_turn_text(token) -> None:
    _TURN_TEXT.reset(token)


def turn_text() -> str:
    return _TURN_TEXT.get()


def colors_from(*texts: str) -> list[ColorAttribute]:
    """Une varias fuentes (args de la tool + turno) sin duplicar colores."""
    merged: list[ColorAttribute] = []
    seen: set[str] = set()
    for text in texts:
        for attr in extract_color_attributes(text or ""):
            if attr.id not in seen:
                merged.append(attr)
                seen.add(attr.id)
    return merged


def _norm(value: object) -> str:
    text = str(value or "").lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    # "Flores amarillas?" → no dejar el ? pegado a la palabra.
    text = "".join(
        c if (c.isalnum() or c.isspace()) else " " for c in text.replace("-", " ")
    )
    return " ".join(text.split())


@dataclass(frozen=True)
class ColorAttribute:
    """Un color pedido por el cliente.

    `match` — raíces que deben aparecer en el producto (amarill → amarillas).
    `aliases` — flores/cosas que IMPLICAN ese color aunque no lo digan
    (girasol → amarillo). No inventamos alias peligrosos: "rosa" la flor no
    cuenta como rojo.
    """

    id: str
    match: tuple[str, ...]
    aliases: tuple[str, ...] = ()


# Disparadores → atributo. Orden: formas largas antes que cortas cuando importan.
_COLOR_TRIGGERS: tuple[tuple[tuple[str, ...], ColorAttribute], ...] = (
    (
        ("amarillas", "amarillos", "amarilla", "amarillo"),
        ColorAttribute(
            id="amarillo",
            match=("amarill",),
            aliases=("girasol",),
        ),
    ),
    (
        ("blancas", "blancos", "blanca", "blanco"),
        ColorAttribute(id="blanco", match=("blanc",)),
    ),
    (
        ("rojas", "rojos", "roja", "rojo"),
        ColorAttribute(id="rojo", match=("roj",)),
    ),
    (
        ("rosadas", "rosados", "rosada", "rosado"),
        # Solo "rosad*": "rosa" a secas es la flor, no el color.
        ColorAttribute(id="rosado", match=("rosad",)),
    ),
    (
        ("moradas", "morados", "morada", "morado", "lila", "lilas", "violeta", "violetas"),
        ColorAttribute(
            id="morado",
            match=("morad", "lila", "violet"),
        ),
    ),
    (
        ("naranjas", "naranja"),
        ColorAttribute(id="naranja", match=("naranj",)),
    ),
    (
        ("azules", "azul"),
        ColorAttribute(id="azul", match=("azul",)),
    ),
    (
        ("verdes", "verde"),
        ColorAttribute(id="verde", match=("verd",)),
    ),
)


def extract_color_attributes(text: str) -> list[ColorAttribute]:
    """Colores que el cliente nombró. Vacío si no hay ninguno."""
    norm = _norm(text)
    if not norm:
        return []
    # Palabra completa: "rojo" no dispara dentro de otra palabra.
    padded = f" {norm} "
    found: list[ColorAttribute] = []
    seen: set[str] = set()
    for triggers, attr in _COLOR_TRIGGERS:
        if attr.id in seen:
            continue
        if any(f" {t} " in padded for t in triggers):
            found.append(attr)
            seen.add(attr.id)
    return found


def _product_blob(producto: dict) -> str:
    return _norm(
        " ".join(
            str(producto.get(k) or "")
            for k in ("nombre", "descripcion_corta", "descripcion", "categoria")
        )
    )


def product_matches_attributes(
    producto: dict, attrs: list[ColorAttribute]
) -> bool:
    """True si el producto cumple TODOS los colores pedidos."""
    if not attrs:
        return True
    blob = _product_blob(producto)
    if not blob:
        return False
    for attr in attrs:
        hit = any(stem in blob for stem in attr.match)
        if not hit and attr.aliases:
            hit = any(alias in blob for alias in attr.aliases)
        if not hit:
            return False
    return True


def enforce_attributes(result: object, attrs: list[ColorAttribute]) -> object:
    """Descarta productos que no cumplen el color pedido."""
    if not attrs or not isinstance(result, dict):
        return result
    data = result.get("data")
    if not isinstance(data, list):
        return result

    filtrados = [
        p for p in data
        if isinstance(p, dict) and product_matches_attributes(p, attrs)
    ]
    descartados = len(data) - len(filtrados)
    if descartados:
        log.info(
            "[tool] color=%s: %d resultado(s) descartado(s) por no coincidir",
            ",".join(a.id for a in attrs),
            descartados,
        )
    return {**result, "data": filtrados, "total": len(filtrados)}
