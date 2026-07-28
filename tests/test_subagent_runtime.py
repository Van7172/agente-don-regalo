"""Contratos operativos que no deben depender de que el modelo obedezca."""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.harness import master
from app.harness.contracts import AgentResult, EscalateReason, Turn
from app.harness.registry import AGENTS
from app.harness.state import ConversationState
from app.prompts.compose import build_system
from app.guardrails.response import check_reply, guard_reply
from app.guardrails import response as response_guardrail
from app.tools.definitions import HUMAN_HANDOFF_TOOL, MEMORY_TOOL, TOOLS
from app.services import agent as agent_mod


def test_el_contrato_operativo_queda_en_el_registro():
    catalog = AGENTS["catalog"]
    assert catalog.max_tool_calls == 1
    assert catalog.max_tool_rounds == 2
    assert catalog.parallel_tool_calls is False
    assert catalog.output_policy == "catalog"
    assert AGENTS["concierge"].model_tier == "fast"
    assert AGENTS["detail"].model_tier == "fast"
    assert AGENTS["escalate"].model_tier is None


@pytest.mark.asyncio
async def test_catalogo_ejecuta_como_maximo_una_tool_por_turno(monkeypatch):
    payloads: list[dict] = []
    responses = iter(
        [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "one",
                                "type": "function",
                                "function": {
                                    "name": "buscar_productos",
                                    "arguments": '{"q":"rosas"}',
                                },
                            },
                            {
                                "id": "two",
                                "type": "function",
                                "function": {
                                    "name": "buscar_productos",
                                    "arguments": '{"q":"peluches"}',
                                },
                            },
                        ],
                    }
                }]
            },
            {"choices": [{"message": {"role": "assistant", "content": "Te muestro opciones"}}]},
        ]
    )
    executed: list[str] = []

    async def completion(_client, payload):
        payloads.append(payload)
        return next(responses)

    async def execute(name, args):
        executed.append(args["q"])
        return json.dumps({"ok": True, "productos": []})

    tool = {
        "type": "function",
        "function": {
            "name": "buscar_productos",
            "parameters": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    }
    monkeypatch.setattr(agent_mod, "_chat_completion", completion)
    monkeypatch.setattr(agent_mod, "execute_tool", execute)

    result = await agent_mod.run_specialist(
        [{"role": "user", "content": "muéstrame rosas"}],
        wa_id="519",
        tools_override=[tool],
        include_handoff=False,
        include_memory=False,
        max_tool_rounds=2,
        max_tool_calls=1,
        parallel_tool_calls=False,
    )

    assert result.user_facing == "Te muestro opciones"
    assert executed == ["rosas"]
    assert payloads[0]["parallel_tool_calls"] is False
    assert "tools" not in payloads[1]


@pytest.mark.asyncio
async def test_master_pasa_modelo_y_limites_del_spec(monkeypatch):
    captured: dict = {}

    async def specialist(*_args, **kwargs):
        captured.update(kwargs)
        return AgentResult(user_facing="Hola")

    monkeypatch.setattr(master, "run_specialist", specialist)
    await master._run_specialty(
        "small_talk",
        Turn(text="gracias", messages=[]),
        ConversationState(),
        wa_id="519",
    )

    spec = AGENTS["concierge"]
    assert captured["model"] == settings.openai_fast_model
    assert captured["max_tool_rounds"] == spec.max_tool_rounds
    assert captured["max_tool_calls"] == spec.max_tool_calls
    assert captured["parallel_tool_calls"] == spec.parallel_tool_calls


@pytest.mark.asyncio
async def test_prompt_y_runner_reciben_el_mismo_toolset_real(monkeypatch):
    captured: dict = {}

    async def specialist(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        captured["tools"] = kwargs["tools_override"]
        return AgentResult(user_facing="Te ayudo")

    monkeypatch.setattr(master, "run_specialist", specialist)
    await master._run_specialty(
        "policy_faq",
        Turn(text="¿puedo pagar contra entrega?", messages=[]),
        ConversationState(),
        wa_id="519",
        conversation_id=10,
    )

    names = {
        tool["function"]["name"]
        for tool in captured["tools"]
    }
    system = captured["system"]
    assert names == {
        "buscar_conocimiento_equipo",
        "metodos_pago",
        "distritos_cobertura",
        "rastrear_pedido",
        "escalar_a_humano",
    }
    for name in names:
        assert f"`{name}`" in system
    assert "`guardar_datos_cliente`" not in system
    compact = " ".join(system.split())
    assert "el sistema decide si el transporte real es MCP o API" in compact
    assert "base de conocimiento Qdrant" in compact


def test_concierge_conoce_el_entorno_pero_no_inventa_capacidades():
    system = build_system(
        AGENTS["concierge"],
        ConversationState(),
        available_tool_names=(),
    )
    assert "Trabajas únicamente dentro del chat actual de WhatsApp" in system
    assert "Ninguna" in system
    assert "no está habilitada en este turno" in system
    assert "No puedes guardar ni modificar datos" in system


@pytest.mark.parametrize(
    "reply,rule",
    [
        (
            "Perfecto, consulto con un asesor y vuelvo con la respuesta.",
            "unsupported_advisor_promise",
        ),
        (
            "Listo, confirmé tu pago y el pedido.",
            "unsupported_sensitive_action",
        ),
        (
            "Te enviaré el enlace por correo en unos minutos.",
            "unsupported_external_promise",
        ),
        (
            "Ya revisé el estado de tu pedido.",
            "unsupported_tracking_claim",
        ),
        (
            "Guardé tus datos para la próxima compra.",
            "unsupported_memory_claim",
        ),
    ],
)
def test_promesas_sin_evidencia_se_bloquean(reply, rule):
    guarded = guard_reply(reply, agent="policy")
    assert guarded.blocked
    assert rule in {violation.rule for violation in guarded.violations}


def test_tracking_y_memoria_con_evidencia_si_pueden_afirmar_la_accion():
    assert not check_reply(
        "Ya revisé el estado de tu pedido.",
        tools_used=["rastrear_pedido"],
        agent="tracking",
    )
    assert not check_reply(
        "Guardé tus datos para la próxima compra.",
        tools_used=["guardar_datos_cliente"],
        agent="concierge",
    )


def test_explicar_que_no_se_hizo_una_accion_no_es_una_promesa():
    assert not check_reply("No confirmé tu pago; eso lo valida el equipo.")


@pytest.mark.parametrize("term", ["CRM", "MCP", "Qdrant", "modo HUMAN", "tools"])
def test_la_arquitectura_interna_no_llega_al_cliente(term):
    violations = check_reply(f"Internamente usamos {term} para atenderte.")
    assert "no_system_prompt_leak" in {violation.rule for violation in violations}


def test_todos_los_nombres_de_tools_estan_protegidos_de_fuga():
    defined = {
        tool["function"]["name"]
        for tool in [*TOOLS, MEMORY_TOOL, HUMAN_HANDOFF_TOOL]
    }
    assert set(response_guardrail._INTERNAL_TOOL_NAMES) == defined


@pytest.mark.asyncio
async def test_pago_deriva_directo_sin_especialista_llm(monkeypatch):
    calls: list[str] = []

    async def handoff(**kwargs):
        calls.append(kwargs["motivo"])
        return EscalateReason(motivo=kwargs["motivo"], is_payment=True)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("el pago no debe ejecutar un especialista LLM")

    monkeypatch.setattr(master, "perform_handoff", handoff)
    monkeypatch.setattr(master, "_run_specialty", forbidden)

    state = ConversationState(
        checkout_step="summary",
        chosen_product_id=10,
        chosen_product_name="Desayuno",
    )
    result = await master._handle_checkout(
        Turn(text="sí", messages=[]),
        state,
        wa_id="519",
    )

    assert len(calls) == 1
    assert result.escalate is not None
    assert result.escalate.is_payment is True


def test_bloques_de_catalogo_se_inyectan_solo_cuando_aplican():
    spec = AGENTS["catalog"]
    common = build_system(spec, ConversationState(), turn_text="quiero un regalo")
    assert "## CAMPAÑAS DE TEMPORADA" not in common
    assert "## ARREGLOS FÚNEBRES" not in common
    assert "## HONESTIDAD CON ATRIBUTOS" not in common

    campaign = build_system(
        spec, ConversationState(), turn_text="regalos por Fiestas Patrias"
    )
    funeral = build_system(
        spec, ConversationState(), turn_text="arreglo para un velorio"
    )
    attributes = build_system(
        spec, ConversationState(), turn_text="quiero rosas blancas personalizadas"
    )
    media = build_system(
        spec, ConversationState(), turn_text="", has_media=True
    )

    assert "## CAMPAÑAS DE TEMPORADA" in campaign
    assert "## ARREGLOS FÚNEBRES" in funeral
    assert "## ARREGLOS FÚNEBRES" in media
    assert "## HONESTIDAD CON ATRIBUTOS" in attributes
