"""Resolver un producto cuando el cliente pega su URL de donregalo.pe.

Sin esto el turno caía al catálogo LLM: `buscar_productos` devolvía una bandeja
de hits (gourmet, cestas, packs…) y `compose_product_reply` los volcaba todos
con "¿Quieres más detalles de alguno…?", justo cuando el cliente YA había
elegido el producto del link.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Solo hosts de Don Regalo. Un link de otra tienda no se interpreta como ficha.
_HOSTS = frozenset({"donregalo.pe", "www.donregalo.pe"})

# Path típico: /cestas/pikeo-en-tabla-gourmet o /producto/gustito-criollo
_URL_RE = re.compile(
    r"https?://(?:www\.)?donregalo\.pe/[^\s<>\")\]]+",
    re.IGNORECASE,
)

# Segmentos de path que NO son un slug de producto (categorías, estáticos…).
_NOT_PRODUCT = frozenset(
    {
        "",
        "c",
        "f",
        "p",
        "producto",
        "productos",
        "categoria",
        "categorias",
        "blog",
        "cart",
        "checkout",
        "buscar",
        "search",
        "clienteapiapp",
        "app",
        "api",
    }
)


def extract_product_url_slug(text: str) -> str | None:
    """Último segmento de path de una URL donregalo.pe, o `None`."""
    if not text:
        return None
    for match in _URL_RE.finditer(text):
        slug = _slug_from_url(match.group(0))
        if slug:
            return slug
    return None


def _slug_from_url(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip().rstrip(").,;]"))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _HOSTS:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return None
    candidate = parts[-1].strip().lower()
    # A veces el listado trae guion final ("pikeo-en-tabla-gourmet-").
    candidate = candidate.rstrip("-")
    if not candidate or candidate in _NOT_PRODUCT:
        return None
    # Un slug de producto real lleva guiones o es suficientemente específico.
    if "-" not in candidate and len(candidate) < 6:
        return None
    return candidate


def normalize_url_slug(value: str) -> str:
    """Comparable: minúsculas, sin slash, sin guion final."""
    text = (value or "").strip().strip("/").lower()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.rstrip("-")


def pick_by_url_slug(
    products: list[dict[str, Any]], slug: str
) -> dict[str, Any] | None:
    """El hit cuya `url` / `url_producto` coincide con el slug del link."""
    objetivo = normalize_url_slug(slug)
    if not objetivo:
        return None
    matches: list[dict[str, Any]] = []
    for raw in products:
        if not isinstance(raw, dict):
            continue
        candidato = normalize_url_slug(
            str(raw.get("url") or raw.get("url_producto") or "")
        )
        if not candidato:
            continue
        if candidato == objetivo or candidato.startswith(objetivo) or objetivo.startswith(
            candidato
        ):
            matches.append(raw)
    if len(matches) == 1:
        return matches[0]
    # Varias coincidencias parciales: gana la igualdad exacta.
    exactos = [
        m
        for m in matches
        if normalize_url_slug(str(m.get("url") or m.get("url_producto") or ""))
        == objetivo
    ]
    return exactos[0] if len(exactos) == 1 else (matches[0] if len(matches) == 1 else None)
