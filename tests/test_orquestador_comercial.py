"""Regresiones del enrutamiento comercial y mínimo privilegio por especialista."""
from __future__ import annotations

import pytest

from app.harness import master as master_mod
from app.harness.coverage import explicit_delivery_destination
from app.harness.registry import AGENTS, assert_tool_allowed
from app.harness.router import classify, classify_rules
from app.harness.state import ConversationState


@pytest.mark.parametrize(
    "text",
    [
        "deseo hacer un pedido para cercado de lima",
        "quiero enviar un regalo a Miraflores",
        "el delivery es para San Isidro",
        "Cercado de Lima",
        "es en Miraflores",
    ],
)
def test_destino_explicito_entra_a_cobertura(text):
    got = classify_rules(text, ConversationState())

    assert explicit_delivery_destination(text)
    assert got.intent == "coverage"
    assert got.source == "rules"
    assert got.confidence >= 0.95


@pytest.mark.parametrize(
    "text",
    [
        "quiero flores inspiradas en Miraflores",
        "busco el desayuno Lima",
        "regalo para mi mamá",
    ],
)
def test_una_referencia_no_logistica_no_secuestra_el_catalogo(text):
    assert explicit_delivery_destination(text) is None
    assert classify_rules(text, ConversationState()).intent == "catalog_search"


@pytest.mark.parametrize(
    "text",
    [
        "mi esposa cumple años mañana",
        "es para mi mamá",
        "nuestro aniversario es el viernes",
    ],
)
def test_el_contexto_del_regalo_es_catalogo_sin_depender_del_llm(text):
    got = classify_rules(text, ConversationState())
    assert got.intent == "catalog_search"
    assert got.source == "rules"


@pytest.mark.asyncio
async def test_si_el_router_llm_falla_lo_desconocido_va_a_concierge(monkeypatch):
    async def unavailable(_text):
        return None

    monkeypatch.setattr("app.harness.router.classify_with_llm", unavailable)
    got = await classify("Necesito orientación sobre algo diferente")

    assert got.intent == "small_talk"
    assert got.source == "fallback"


def test_cobertura_tiene_una_sola_herramienta_real():
    assert AGENTS["coverage"].tool_names == ("distritos_cobertura",)
    assert_tool_allowed("coverage", "distritos_cobertura")
    with pytest.raises(PermissionError):
        assert_tool_allowed("coverage", "buscar_conocimiento_equipo")


@pytest.mark.asyncio
async def test_el_orquestador_delega_destino_a_cobertura_y_registra_la_tool(monkeypatch):
    async def fake_coverage(text, state):
        assert "cercado de lima" in text.casefold()
        return {
            "user_facing": (
                "Sí llegamos a Cercado de Lima 🚚 El envío es S/15.00. "
                "¿Qué regalo quieres enviar? 🎁"
            ),
            "state_patch": {"district": "LIMA - CERCADO", "shipping_fee_sol": 15.0},
        }

    monkeypatch.setattr(master_mod, "resolve_coverage", fake_coverage)

    result = await master_mod._handle(
        "coverage",
        master_mod.Turn(text="deseo hacer un pedido para cercado de lima"),
        ConversationState(),
    )

    assert "Cercado de Lima" in (result.user_facing or "")
    assert "¿Qué regalo" in (result.user_facing or "")
    assert result.tools_used == ["distritos_cobertura"]
