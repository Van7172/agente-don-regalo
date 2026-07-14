"""Auditoría: cada subagente tiene exactamente las tools que le tocan.

Un desajuste aquí no falla ruidosamente: el modelo intenta llamar una tool que no
tiene y el turno se degrada en silencio, o una tool queda declarada sin que nadie
la ejecute nunca. Estos tests fijan la correspondencia agente ↔ API.
"""
import re

import pytest

from app.harness.registry import AGENTS, INTENT_TO_AGENT, ORCHESTRATOR, spec_for
from app.tools import executor
from app.tools.definitions import HUMAN_HANDOFF_TOOL, MEMORY_TOOL, TOOLS

TOOLS_DEFINIDAS = (
    {t["function"]["name"] for t in TOOLS}
    | {MEMORY_TOOL["function"]["name"], HUMAN_HANDOFF_TOOL["function"]["name"]}
)
# Las que el executor sabe ejecutar de verdad.
TOOLS_IMPLEMENTADAS = set(executor._CATALOG_TOOLS) | {
    "buscar_semantico",
    "productos_similares",
    "buscar_conocimiento_equipo",
}
# El loop del agente las atiende aparte (no pasan por `execute_tool`).
TOOLS_ESPECIALES = {"escalar_a_humano", "guardar_datos_cliente"}

ASIGNADAS = {name for spec in AGENTS.values() for name in spec.tool_names}


@pytest.mark.parametrize("nombre", sorted(AGENTS))
def test_ningun_agente_declara_una_tool_que_no_existe(nombre):
    desconocidas = set(AGENTS[nombre].tool_names) - TOOLS_DEFINIDAS
    assert not desconocidas, f"{nombre} declara tools inexistentes: {desconocidas}"


@pytest.mark.parametrize("nombre", sorted(AGENTS))
def test_toda_tool_asignada_tiene_implementacion(nombre):
    """Declarar una tool que el executor no sabe ejecutar es un fallo silencioso."""
    sin_implementar = set(AGENTS[nombre].tool_names) - TOOLS_IMPLEMENTADAS
    assert not sin_implementar, f"{nombre}: sin implementación en el executor: {sin_implementar}"


@pytest.mark.parametrize("nombre", sorted(AGENTS))
def test_el_playbook_no_cita_tools_que_el_agente_no_tiene(nombre):
    spec = AGENTS[nombre]
    citadas = {t for t in re.findall(r"`([a-z_]+)`", spec.playbook) if t in TOOLS_DEFINIDAS}
    fantasma = citadas - set(spec.tool_names) - TOOLS_ESPECIALES
    assert not fantasma, f"{nombre} cita tools que no tiene: {fantasma}"


def test_la_unica_tool_huerfana_es_tipo_cambio():
    """`tipo_cambio` no es de nadie a propósito: la moneda la convierte el adapter.

    Cualquier OTRA huérfana es un descuido: una API que mantenemos y que ningún
    agente puede llegar a usar.
    """
    huerfanas = TOOLS_DEFINIDAS - ASIGNADAS - TOOLS_ESPECIALES
    assert huerfanas == {"tipo_cambio"}


def test_el_orquestador_no_tiene_tools():
    """Clasifica y delega. Si ejecuta algo, deja de ser un orquestador."""
    assert ORCHESTRATOR.tool_names == ()
    assert ORCHESTRATOR.customer_facing is False


def test_toda_intencion_tiene_un_agente():
    for intent, agente in INTENT_TO_AGENT.items():
        assert agente in AGENTS, f"{intent} apunta a un agente inexistente"


@pytest.mark.parametrize("intent,agente", sorted(INTENT_TO_AGENT.items()))
def test_el_router_y_el_registro_no_se_contradicen(intent, agente):
    assert spec_for(intent).name == agente


def test_los_agentes_deterministas_estan_marcados():
    """Cobertura y cierre los resuelve el código: el LLM nunca ve sus specs.

    Si alguien añade tools a uno de ellos esperando que el modelo las llame, no va
    a pasar nada. Marcarlos evita esa confusión.
    """
    deterministas = {n for n, s in AGENTS.items() if s.deterministic}
    assert deterministas == {"coverage", "checkout"}


def test_solo_escalan_los_agentes_que_deben():
    """El catálogo NO puede escalar: buscar productos nunca es motivo de handoff."""
    pueden = {n for n, s in AGENTS.items() if s.can_handoff}
    assert pueden == {"checkout", "policy", "tracking", "escalate"}
    assert "catalog" not in pueden
    assert "concierge" not in pueden
