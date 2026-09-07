"""Un asesor que toma el chat tiene prioridad sobre un turno del bot en vuelo."""
from __future__ import annotations

import pytest

from app.services import buffer


@pytest.mark.asyncio
async def test_turno_en_vuelo_no_responde_despues_de_que_asesor_toma_chat(
    monkeypatch,
):
    """La IA empezó en AI, pensó, y durante ese tiempo el asesor tomó el chat."""
    consultas = 0
    enviados: list[str] = []

    async def get_conversation(_conversation_id):
        nonlocal consultas
        consultas += 1
        if consultas == 1:
            return {
                "conversation": {
                    "id": 77,
                    "mode": "AI",
                    "bot_active": True,
                    "human_support": False,
                    "assigned": None,
                },
                "messages": [],
            }
        return {
            "conversation": {
                "id": 77,
                "mode": "HUMAN",
                "bot_active": False,
                "human_support": False,
                "assigned": {"id": 9, "name": "Joel"},
            },
            "messages": [],
        }

    async def get_memory(_wa_id):
        return {}

    async def run_master(*_args, **_kwargs):
        return "¿A qué distrito de Lima lo enviamos?"

    async def send_message(_wa_id, content, **_kwargs):
        enviados.append(content)
        return "wamid.BOT"

    async def append_outbound(*_args, **_kwargs):
        raise AssertionError("una respuesta bloqueada tampoco se persiste")

    async def set_typing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(buffer.crm_http, "get_conversation", get_conversation)
    monkeypatch.setattr(buffer.crm_http, "get_memory", get_memory)
    monkeypatch.setattr(buffer.crm_http, "append_outbound", append_outbound)
    monkeypatch.setattr(buffer, "run_master", run_master)
    monkeypatch.setattr(buffer, "send_message", send_message)
    monkeypatch.setattr(buffer, "set_typing", set_typing)

    await buffer._flush_external(
        77, contact_id=3, wa_id="51999", user_content="qué datos necesita"
    )

    assert consultas >= 2, "hay que releer el control justo antes de responder"
    assert enviados == [], "el asesor tomó el chat: el bot debe callarse"


@pytest.mark.asyncio
async def test_asignacion_bloquea_aunque_mode_aun_diga_ai(monkeypatch):
    """Cierra la ventana entre POST /claim y el PATCH que cambia a HUMAN."""
    from app.services.conversation_control import bot_controls_external

    async def get_conversation(_conversation_id):
        return {
            "conversation": {
                "mode": "AI",
                "bot_active": True,
                "human_support": False,
                "assigned": {"id": 9, "name": "Joel"},
            }
        }

    monkeypatch.setattr(
        "app.services.conversation_control.crm_http.get_conversation",
        get_conversation,
    )
    assert not await bot_controls_external(77)


@pytest.mark.asyncio
async def test_ai_sin_asignacion_puede_responder(monkeypatch):
    from app.services.conversation_control import bot_controls_external

    async def get_conversation(_conversation_id):
        return {
            "conversation": {
                "mode": "AI",
                "bot_active": True,
                "human_support": False,
                "assigned": None,
            }
        }

    monkeypatch.setattr(
        "app.services.conversation_control.crm_http.get_conversation",
        get_conversation,
    )
    assert await bot_controls_external(77)
