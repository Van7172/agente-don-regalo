"""OPENAI_REASONING_EFFORT llega al payload del especialista."""
from __future__ import annotations

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
