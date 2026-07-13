"""El bucle del agente no debe morir por un 429 pasajero de OpenAI.

Regresión del incidente del 13-07-2026: OpenAI devolvió 429, run_agent devolvió
None y el buffer aparcó la conversación en HUMAN para siempre por un saludo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
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


def _reply(text: str) -> dict:
    return {"choices": [{"message": {"content": text, "tool_calls": None}}]}


class _FakeClient:
    """Devuelve las respuestas programadas, una por llamada."""

    def __init__(self, responses: list[httpx.Response]):
        self._responses = responses
        self.calls = 0

    async def post(self, url, headers=None, json=None):
        self.calls += 1
        return self._responses.pop(0)


def _resp(status: int, *, body: str = "", headers: dict | None = None, payload=None):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    if payload is not None:
        return httpx.Response(status, json=payload, request=request, headers=headers or {})
    return httpx.Response(status, text=body, request=request, headers=headers or {})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Sin esperas reales: el backoff no debe alargar los tests."""
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(agent_mod.asyncio, "sleep", fake_sleep)


@pytest.mark.asyncio
async def test_reintenta_tras_429_y_devuelve_la_respuesta():
    client = _FakeClient([
        _resp(429, body='{"error":{"type":"rate_limit_exceeded"}}'),
        _resp(200, payload=_reply("¡Hola! ¿Para quién es el regalo?")),
    ])

    data = await agent_mod._chat_completion(client, {"model": "gpt-4o", "messages": []})

    assert client.calls == 2
    assert data["choices"][0]["message"]["content"].startswith("¡Hola!")


@pytest.mark.asyncio
async def test_respeta_retry_after_y_reintenta_errores_5xx():
    client = _FakeClient([
        _resp(429, body="slow down", headers={"retry-after": "2"}),
        _resp(503, body="upstream"),
        _resp(200, payload=_reply("listo")),
    ])

    data = await agent_mod._chat_completion(client, {"model": "gpt-4o", "messages": []})

    assert client.calls == 3
    assert data["choices"][0]["message"]["content"] == "listo"


@pytest.mark.asyncio
async def test_cuota_agotada_no_reintenta():
    """Esperar no recarga la tarjeta: escalar de una vez, sin hacer esperar al cliente."""
    client = _FakeClient([
        _resp(429, body='{"error":{"code":"insufficient_quota"}}'),
    ])

    with pytest.raises(httpx.HTTPStatusError):
        await agent_mod._chat_completion(client, {"model": "gpt-4o", "messages": []})

    assert client.calls == 1


@pytest.mark.asyncio
async def test_se_rinde_tras_agotar_los_intentos():
    client = _FakeClient([_resp(429, body="rate limit") for _ in range(agent_mod._LLM_MAX_ATTEMPTS)])

    with pytest.raises(httpx.HTTPStatusError):
        await agent_mod._chat_completion(client, {"model": "gpt-4o", "messages": []})

    assert client.calls == agent_mod._LLM_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_error_no_reintentable_falla_de_inmediato():
    """Un 400 (payload malo) no mejora reintentando."""
    client = _FakeClient([_resp(400, body='{"error":{"message":"bad request"}}')])

    with pytest.raises(httpx.HTTPStatusError):
        await agent_mod._chat_completion(client, {"model": "gpt-4o", "messages": []})

    assert client.calls == 1
