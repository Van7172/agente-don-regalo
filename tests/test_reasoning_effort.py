"""OPENAI_REASONING_EFFORT llega al payload del especialista."""
from __future__ import annotations

import pytest

from app.config import _parse_reasoning_effort
from app.services import agent as agent_mod


def test_parse_acepta_high():
    assert _parse_reasoning_effort("high") == "high"
    assert _parse_reasoning_effort("HIGH") == "high"


def test_parse_vacio_o_invalido_no_rompe():
    assert _parse_reasoning_effort("") == ""
    assert _parse_reasoning_effort("super-ultra") == ""


def test_with_reasoning_effort_inyecta(monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "openai_reasoning_effort", "high")
    out = agent_mod.with_reasoning_effort({"model": "gpt-5.6-luna", "messages": []})
    assert out["reasoning_effort"] == "high"
    assert out["model"] == "gpt-5.6-luna"


def test_sin_effort_no_añade_campo(monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "openai_reasoning_effort", "")
    out = agent_mod.with_reasoning_effort({"model": "gpt-4o-mini"})
    assert "reasoning_effort" not in out


def test_timeout_se_estira_con_high(monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "openai_reasoning_effort", "high")
    assert agent_mod._llm_http_timeout() == agent_mod._LLM_TIMEOUT_REASONING
    monkeypatch.setattr(agent_mod.settings, "openai_reasoning_effort", "")
    assert agent_mod._llm_http_timeout() == agent_mod._LLM_TIMEOUT_DEFAULT


# ── Auditoría de roleplay (20-09-2026): `reasoning_effort` + `tools` ────
#
# OpenAI rechaza con 400 cualquier ronda que combine `tools` con un nivel de
# razonamiento distinto de "none" en /v1/chat/completions para gpt-5.6-luna
# ("Function tools with reasoning_effort are not supported... set
# reasoning_effort to 'none'") — y el rechazo llega IGUAL con el campo
# OMITIDO: el modelo trae un nivel de razonamiento por defecto que no es
# "none". Con `OPENAI_REASONING_EFFORT=medium` configurado (como en
# producción) y `tools` presente en casi toda ronda mientras hay presupuesto,
# CUALQUIER especialista con tools —catalog, detail, checkout— fallaba en
# TODOS sus turnos y degradaba en silencio a "destacados" genéricos o a un
# handoff fallido. Confirmado en vivo simulando clientes reales: "cuánto
# cuesta un ramo de rosas" devolvía peluches y cajas de regalo sin ninguna
# rosa, sin avisar que la búsqueda real nunca corrió.


def _tool() -> dict:
    return {
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


@pytest.mark.asyncio
async def test_la_ronda_con_tools_manda_reasoning_effort_none(monkeypatch):
    monkeypatch.setattr(agent_mod.settings, "openai_reasoning_effort", "medium")
    payloads: list[dict] = []

    async def completion(_client, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"role": "assistant", "content": "listo"}}]}

    monkeypatch.setattr(agent_mod, "_chat_completion", completion)

    await agent_mod.run_specialist(
        [{"role": "user", "content": "busco rosas"}],
        wa_id="519",
        tools_override=[_tool()],
        include_handoff=False,
        include_memory=False,
        max_tool_rounds=1,
    )

    assert payloads, "no se llamó al modelo"
    payload = payloads[0]
    assert "tools" in payload
    assert payload["reasoning_effort"] == "none", (
        "con tools presentes, el 'medium' configurado tiene que degradar a "
        "'none' EXPLÍCITO — omitir el campo no basta, el modelo lo rechaza igual"
    )


@pytest.mark.asyncio
async def test_la_ronda_sin_tools_respeta_el_effort_configurado(monkeypatch):
    """El último intento («respuesta final sin tools») y los especialistas sin
    toolset (concierge) sí deben llevar el nivel configurado: perderlo ahí
    apagaría el dial de calidad para todo lo que NO usa tools."""
    monkeypatch.setattr(agent_mod.settings, "openai_reasoning_effort", "medium")
    payloads: list[dict] = []

    async def completion(_client, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"role": "assistant", "content": "listo"}}]}

    monkeypatch.setattr(agent_mod, "_chat_completion", completion)

    await agent_mod.run_specialist(
        [{"role": "user", "content": "gracias, todo bien"}],
        wa_id="519",
        tools_override=[],
        include_handoff=False,
        include_memory=False,
        max_tool_rounds=1,
    )

    assert payloads, "no se llamó al modelo"
    payload = payloads[0]
    assert "tools" not in payload
    assert payload["reasoning_effort"] == "medium"
