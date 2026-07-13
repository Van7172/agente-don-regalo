"""El bot no debe derivar a un humano cuando el cliente no pide nada.

Regresión del 13-07-2026: al cliente escribir "Todo en orden hoy", el modelo
llamó a escalar_a_humano ("no hay tarea → excede mis capacidades") y el chat
quedó aparcado esperando a un asesor que no hacía falta.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_sandbox.db")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
os.environ.setdefault("DEFAULT_TENANT_SLUG", "test-tenant")
os.environ.setdefault("CRM_MODE", "local")
os.environ.setdefault("WATCHDOG_ENABLED", "0")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.services import agent as agent_mod  # noqa: E402


def _user(text: str) -> list:
    return [{"role": "system", "content": "..."}, {"role": "user", "content": text}]


# ── detección ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "Todo en orden hoy",   # el caso que rompió
        "ok gracias",
        "Muchas gracias",
        "Listo",
        "perfecto",
        "Todo bien",
        "jajaja",
        "👍",
        "😊",
        "Hola",
        "Buenos días",
        "de acuerdo",
        "ya",
        "no",
    ],
)
def test_reconoce_charla_sin_pedido(text):
    assert agent_mod._is_small_talk(_user(text)) is True


@pytest.mark.parametrize(
    "text",
    [
        "quiero hablar con un asesor",
        "necesito ayuda con mi pedido",
        "¿cuánto cuesta el ramo de rosas?",
        "esto no sirve, qué mala atención",
        "ya les pagué, aquí está el comprobante",
        "quiero cancelar mi pedido",
        "me pueden hacer un descuento",
        "gracias pero necesito cambiar la dirección de entrega",
        "todo bien pero quiero hablar con una persona",
    ],
)
def test_no_confunde_un_pedido_real_con_charla(text):
    """Si esto fallara, estaríamos silenciando escalaciones legítimas."""
    assert agent_mod._is_small_talk(_user(text)) is False


def test_una_imagen_no_es_charla_trivial():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "ok"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        ],
    }]
    assert agent_mod._is_small_talk(messages) is False


# ── el bucle del agente ─────────────────────────────────────

def _tool_call(name: str, call_id: str = "call_1") -> dict:
    return {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"motivo": "x"})},
                }],
            }
        }]
    }


def _final(text: str) -> dict:
    return {"choices": [{"message": {"content": text, "tool_calls": None}}]}


@pytest.fixture
def espias(monkeypatch):
    """Intercepta los envíos a WhatsApp y el cambio de modo del CRM."""
    estado = {"enviados": [], "modo": None}

    async def fake_send(wa_id, text):
        estado["enviados"].append(text)
        return "wamid.test"

    async def fake_typing(*_a, **_k):
        return None

    async def fake_notify(_text):
        return None

    monkeypatch.setattr(agent_mod, "send_message", fake_send)
    monkeypatch.setattr(agent_mod, "set_typing", fake_typing)
    monkeypatch.setattr(agent_mod, "notify_team", fake_notify)
    return estado


@pytest.mark.asyncio
async def test_no_escala_ante_charla_trivial_y_responde_con_calidez(espias, monkeypatch):
    """El caso de la captura: 'Todo en orden hoy' debe recibir una respuesta, no un handoff."""
    respuestas = [
        _tool_call("escalar_a_humano"),                                  # el modelo se equivoca
        _final("¡Me alegra saberlo! 😊 Cualquier cosa, aquí estoy."),     # tras el rechazo, responde
    ]

    async def fake_chat(_client, _payload):
        return respuestas.pop(0)

    monkeypatch.setattr(agent_mod, "_chat_completion", fake_chat)

    reply = await agent_mod.run_agent(
        _user("Todo en orden hoy"),
        wa_id="51999",
        conversation_id=7,
        use_external_crm=False,
    )

    assert reply == "¡Me alegra saberlo! 😊 Cualquier cosa, aquí estoy."
    assert reply != agent_mod.HANDOFF_DONE
    # Nada de "Te conecto con un asesor", ni siquiera el filler.
    assert espias["enviados"] == []


@pytest.mark.asyncio
async def test_si_pide_un_asesor_si_escala(espias, monkeypatch):
    """La red de seguridad no debe romper la escalación de verdad."""
    async def fake_chat(_client, _payload):
        return _tool_call("escalar_a_humano")

    monkeypatch.setattr(agent_mod, "_chat_completion", fake_chat)

    reply = await agent_mod.run_agent(
        _user("quiero hablar con un asesor humano"),
        wa_id="51999",
        conversation_id=7,
        use_external_crm=False,
    )

    assert reply == agent_mod.HANDOFF_DONE
    assert any("asesor" in t for t in espias["enviados"])
