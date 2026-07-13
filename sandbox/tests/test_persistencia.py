"""Todo lo que el bot envía por WhatsApp debe quedar guardado en el CRM.

Regresión del 13-07-2026: el hilo del inbox tenía huecos respecto al WhatsApp
del cliente. El filler ("Un momento, ya te ayudo") y el mensaje de espera del
handoff se enviaban con send_message() sin persistir, así que el asesor no los
veía y no entendía qué había leído el cliente.
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


@pytest.fixture
def espias(monkeypatch):
    """Registra lo enviado a WhatsApp y lo persistido en el CRM."""
    estado = {"enviados": [], "guardados": []}

    async def fake_send(_wa_id, text):
        estado["enviados"].append(text)
        return f"wamid.{len(estado['enviados'])}"

    async def fake_typing(*_a, **_k):
        return None

    async def fake_notify(_t):
        return None

    monkeypatch.setattr(agent_mod, "send_message", fake_send)
    monkeypatch.setattr(agent_mod, "set_typing", fake_typing)
    monkeypatch.setattr(agent_mod, "notify_team", fake_notify)

    async def persist(*, content, wa_message_id=None, media_url=None):
        estado["guardados"].append(content)

    estado["persist"] = persist
    return estado


def _tool_call(name: str) -> dict:
    return {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps({"motivo": "pago"})},
                }],
            }
        }]
    }


def _user(text: str) -> list:
    return [{"role": "user", "content": text}]


@pytest.mark.asyncio
async def test_el_mensaje_de_espera_del_handoff_queda_en_el_crm(espias, monkeypatch):
    async def fake_chat(_c, _p):
        return _tool_call("escalar_a_humano")

    monkeypatch.setattr(agent_mod, "_chat_completion", fake_chat)

    reply = await agent_mod.run_agent(
        _user("ya les pagué, aquí está el comprobante"),
        wa_id="51999",
        conversation_id=7,
        persist=espias["persist"],
    )

    assert reply == agent_mod.HANDOFF_DONE
    # Lo que leyó el cliente es exactamente lo que ve el asesor: sin huecos.
    assert espias["enviados"] == [agent_mod._HANDOFF_WAIT_MSG]
    assert espias["guardados"] == [agent_mod._HANDOFF_WAIT_MSG]


@pytest.mark.asyncio
async def test_el_filler_de_tool_queda_en_el_crm(espias, monkeypatch):
    """Antes 'Un momento, ya te ayudo 😊' salía por WhatsApp pero no en el inbox."""
    respuestas = [
        _tool_call("buscar_semantico"),
        {"choices": [{"message": {"content": "Aquí tienes 3 opciones", "tool_calls": None}}]},
    ]

    async def fake_chat(_c, _p):
        return respuestas.pop(0)

    async def fake_tool(_fn, _args):
        return json.dumps({"ok": True})

    monkeypatch.setattr(agent_mod, "_chat_completion", fake_chat)
    monkeypatch.setattr(agent_mod, "execute_tool", fake_tool)

    reply = await agent_mod.run_agent(
        _user("busco un desayuno sorpresa"),
        wa_id="51999",
        conversation_id=7,
        persist=espias["persist"],
    )

    assert reply == "Aquí tienes 3 opciones"
    assert len(espias["enviados"]) == 1  # el filler
    # Se envió y se guardó: son el mismo texto.
    assert espias["guardados"] == espias["enviados"]


@pytest.mark.asyncio
async def test_si_el_crm_falla_el_mensaje_igual_sale(espias, monkeypatch):
    """Guardar es importante, pero no a costa de dejar al cliente sin respuesta."""
    async def persist_roto(**_kw):
        raise RuntimeError("CRM caído")

    async def fake_chat(_c, _p):
        return _tool_call("escalar_a_humano")

    monkeypatch.setattr(agent_mod, "_chat_completion", fake_chat)

    reply = await agent_mod.run_agent(
        _user("quiero un asesor"),
        wa_id="51999",
        conversation_id=7,
        persist=persist_roto,
    )

    assert reply == agent_mod.HANDOFF_DONE
    assert espias["enviados"] == [agent_mod._HANDOFF_WAIT_MSG]
