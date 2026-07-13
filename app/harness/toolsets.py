"""Toolsets por specialty (subconjunto de TOOLS)."""
from __future__ import annotations

from app.tools.definitions import MEMORY_TOOL, TOOLS

_BY_NAME = {t["function"]["name"]: t for t in TOOLS}


def _pick(*names: str) -> list:
    return [_BY_NAME[n] for n in names if n in _BY_NAME]


TOOLSETS: dict[str, list] = {
    "catalog": _pick(
        "buscar_semantico",
        "productos_similares",
        "listar_categorias",
        "listar_ocasiones",
        "buscar_productos",
        "catalogo_categoria",
        "productos_destacados",
        "productos_oferta",
        "productos_por_ocasion",
        "tipo_cambio",
    ),
    "coverage": _pick("distritos_cobertura", "buscar_conocimiento_equipo"),
    "detail": _pick("detalle_producto", "productos_similares", "tipo_cambio"),
    "checkout": _pick("distritos_cobertura", "metodos_pago", "tipo_cambio"),
    "policy": _pick(
        "buscar_conocimiento_equipo",
        "metodos_pago",
        "distritos_cobertura",
        "rastrear_pedido",
    ),
    "track": _pick("rastrear_pedido"),
    "master": [],  # Master casi sin tools de catálogo
}


def tools_for(intent: str, *, with_memory: bool = False, with_handoff: bool = False) -> list:
    from app.tools.definitions import HUMAN_HANDOFF_TOOL

    mapping = {
        "catalog_search": "catalog",
        "coverage": "coverage",
        "product_detail": "detail",
        "checkout": "checkout",
        "policy_faq": "policy",
        "track_order": "track",
        "escalate": "policy",
        "greet": "master",
        "small_talk": "master",
    }
    key = mapping.get(intent, "catalog")
    out = list(TOOLSETS.get(key) or TOOLSETS["catalog"])
    if with_memory:
        out = out + [MEMORY_TOOL]
    if with_handoff and intent in ("escalate", "checkout", "policy_faq"):
        out = out + [HUMAN_HANDOFF_TOOL]
    return out
