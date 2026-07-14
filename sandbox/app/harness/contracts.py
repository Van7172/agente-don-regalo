"""Contratos entre el orquestador y sus especialistas.

Regla del harness: un especialista NUNCA devuelve texto suelto. Devuelve un
`AgentResult` con lo que aprendió (`state_patch`, `artifacts`) además de lo que
le dirá al cliente. Si devolviera solo `str`, el orquestador no podría reducir
el estado y tendría que adivinarlo con regex sobre la prosa — que es justo el
bug que este módulo elimina.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Product:
    """Producto realmente citado por una tool. Fuente de verdad de los IDs."""

    id_producto: int
    nombre: str = ""
    precio_sol: Optional[float] = None
    precio_usd: Optional[float] = None
    imagen_url: str = ""
    descripcion: str = ""

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Product | None":
        pid = raw.get("id_producto") or raw.get("id")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        return cls(
            id_producto=pid,
            nombre=str(raw.get("nombre") or raw.get("name") or "").strip(),
            precio_sol=_as_float(raw.get("precio_sol") or raw.get("precio_soles")),
            precio_usd=_as_float(raw.get("precio") or raw.get("precio_usd")),
            imagen_url=str(raw.get("imagen_url") or "").strip(),
            descripcion=str(
                raw.get("descripcion_corta") or raw.get("descripcion") or ""
            ).strip(),
        )


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class EscalateReason:
    motivo: str
    is_payment: bool = False


@dataclass
class Turn:
    """Lo que el orquestador percibe del cliente en este turno."""

    text: str = ""
    quoted: str = ""          # producto citado en la respuesta de WhatsApp
    has_media: bool = False
    messages: list = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    """Veredicto de una política. `allow=False` trae siempre el motivo."""

    allow: bool
    reason: str = ""

    def __bool__(self) -> bool:  # `if decision:` == "se permite"
        return self.allow


@dataclass
class AgentResult:
    """Lo único que un especialista puede devolver."""

    user_facing: Optional[str] = None
    state_patch: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Product] = field(default_factory=list)
    escalate: Optional[EscalateReason] = None
    confidence: float = 1.0

    @property
    def product_ids(self) -> list[int]:
        return [p.id_producto for p in self.artifacts]

    @classmethod
    def reply(cls, text: str | None, **patch: Any) -> "AgentResult":
        return cls(user_facing=text, state_patch=patch)


# Claves de producto en los payloads de las tools de catálogo.
_PRODUCT_LIST_KEYS = ("data", "productos", "resultados", "items")


def extract_products(payload: Any) -> list[Product]:
    """Saca productos de un resultado de tool ya deserializado.

    Acepta las formas reales que devuelven la API de Don Regalo y Qdrant:
    `{"data": [...]}`, una lista suelta, o un `detalle_producto` (dict único).
    """
    out: list[Product] = []

    def _collect(items: Any) -> None:
        if isinstance(items, list):
            for raw in items:
                if isinstance(raw, dict):
                    product = Product.from_raw(raw)
                    if product is not None:
                        out.append(product)

    if isinstance(payload, list):
        _collect(payload)
        return out

    if not isinstance(payload, dict):
        return out

    for key in _PRODUCT_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            _collect(value)
        elif isinstance(value, dict):
            # detalle_producto devuelve el producto directamente bajo `data`.
            product = Product.from_raw(value)
            if product is not None:
                out.append(product)

    if not out:
        # Payload que YA es un producto (sin envoltorio).
        product = Product.from_raw(payload)
        if product is not None:
            out.append(product)

    return out
