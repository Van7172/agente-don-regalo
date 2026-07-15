"""Registro de agentes: prompt + facts + toolset + contrato, en una sola pieza.

Antes el playbook vivía en `prompts/` y el toolset en `toolsets.py`, sin nada que
los obligara a estar de acuerdo: un playbook podía citar una tool que su toolset
no incluía y el modelo alucinaba la llamada en silencio. `AgentSpec` los ata, y
`test_prompts_architecture.py` verifica la coherencia.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.prompts import playbooks
from app.tools.definitions import HUMAN_HANDOFF_TOOL, MEMORY_TOOL, TOOLS

_BY_NAME = {t["function"]["name"]: t for t in TOOLS}

# `tipo_cambio` ya NO es una tool de ningún agente: los precios llegan en ambas
# monedas desde `tools/adapters.py`. Antes el modelo pedía el tipo de cambio y
# multiplicaba él mismo los precios — aritmética de dinero a cargo de un LLM, en
# un prompt que a la vez le prohíbe inventar precios.
# `explorar_catalogo` es la ÚNICA puerta a la taxonomía. Se quitaron
# `listar_categorias` y `listar_ocasiones`: devolvían una taxonomía parcial (sin
# filtros ni landings) y competían con `explorar_catalogo` — el modelo veía dos
# tools "para ver el catálogo" y podía elegir la incompleta, luego inventar el resto.
CATALOG_TOOLS = (
    "explorar_catalogo",
    "buscar_semantico",
    "productos_similares",
    "buscar_productos",
    "catalogo_categoria",
    "productos_destacados",
    "productos_oferta",
    "productos_por_ocasion",
)


@dataclass(frozen=True)
class AgentSpec:
    """Un especialista del harness. `customer_facing=False` ⇒ no lleva CORE."""

    name: str
    playbook: str
    facts: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    customer_facing: bool = True
    can_handoff: bool = False
    # `deterministic`: el orquestador lo resuelve en código y NUNCA llama al LLM
    # con este spec. Su playbook y sus facts son documentación del flujo, no un
    # prompt: no los lee ningún modelo. Ver `master._handle`.
    deterministic: bool = False

    def tools(self, *, with_memory: bool = False, with_handoff: bool = False) -> list:
        out = [_BY_NAME[n] for n in self.tool_names if n in _BY_NAME]
        if with_memory and self.customer_facing:
            out.append(MEMORY_TOOL)
        if with_handoff and self.can_handoff:
            out.append(HUMAN_HANDOFF_TOOL)
        return out


ORCHESTRATOR = AgentSpec(
    name="orchestrator",
    playbook=playbooks.ORCHESTRATOR,
    customer_facing=False,  # clasifica y delega; no genera texto de cara al cliente
)

AGENTS: dict[str, AgentSpec] = {
    "concierge": AgentSpec(
        name="concierge",
        playbook=playbooks.CONCIERGE,
        facts=(),
        tool_names=(),
    ),
    "catalog": AgentSpec(
        name="catalog",
        playbook=playbooks.CATALOG,
        facts=("catalog_taxonomy", "pricing"),
        tool_names=CATALOG_TOOLS,
    ),
    "detail": AgentSpec(
        name="detail",
        playbook=playbooks.DETAIL,
        facts=("pricing",),
        tool_names=("detalle_producto", "productos_similares"),
    ),
    # Cobertura y cierre los resuelve el código (`harness/coverage.py` y
    # `harness/checkout.py`). Sus tools las llama el orquestador directamente, no
    # un LLM: por eso `deterministic=True`.
    "coverage": AgentSpec(
        name="coverage",
        playbook=playbooks.COVERAGE,
        facts=("delivery",),
        tool_names=("distritos_cobertura", "buscar_conocimiento_equipo"),
        deterministic=True,
    ),
    "checkout": AgentSpec(
        name="checkout",
        playbook=playbooks.CHECKOUT,
        facts=("delivery", "pricing", "payment"),
        tool_names=("distritos_cobertura", "metodos_pago"),
        can_handoff=True,
        deterministic=True,
    ),
    "policy": AgentSpec(
        name="policy",
        playbook=playbooks.POLICY,
        facts=("payment", "returns", "delivery", "contact"),
        tool_names=(
            "buscar_conocimiento_equipo",
            "metodos_pago",
            "distritos_cobertura",
            "rastrear_pedido",
        ),
        can_handoff=True,
    ),
    "tracking": AgentSpec(
        name="tracking",
        playbook=playbooks.TRACKING,
        facts=("contact",),
        tool_names=("rastrear_pedido",),
        can_handoff=True,
    ),
    "escalate": AgentSpec(
        name="escalate",
        playbook=playbooks.ESCALATE,
        facts=("contact", "payment"),
        tool_names=("buscar_conocimiento_equipo",),
        can_handoff=True,
    ),
}

# Intención (lo que dice el cliente) → especialista (quién la atiende).
INTENT_TO_AGENT: dict[str, str] = {
    "greet": "concierge",
    "small_talk": "concierge",
    "catalog_search": "catalog",
    "product_detail": "detail",
    "coverage": "coverage",
    "checkout": "checkout",
    "policy_faq": "policy",
    "track_order": "tracking",
    "escalate": "escalate",
}


def spec_for(intent: str) -> AgentSpec:
    return AGENTS[INTENT_TO_AGENT.get(intent, "catalog")]


def tools_for(
    intent: str, *, with_memory: bool = False, with_handoff: bool = False
) -> list:
    return spec_for(intent).tools(with_memory=with_memory, with_handoff=with_handoff)
