from __future__ import annotations

import json

import pytest

from app.guardrails import (
    ParameterValidationError,
    minimize_historical_messages,
    protect_json_for_model,
    protect_profile,
    redact_personal_data,
    validate_mcp_arguments,
)
from app.services import agent as agent_mod
from app.tools import mcp_client


def test_redacta_identificadores_directos_sin_tocar_precios_o_ids():
    result = redact_personal_data(
        "Mi correo es ana@example.com, celular +51 987 654 321 y DNI 12345678. "
        "El producto 734 cuesta 74.80."
    )
    assert "ana@example.com" not in result.value
    assert "987 654 321" not in result.value
    assert "12345678" not in result.value
    assert "734" in result.value
    assert "74.80" in result.value
    assert result.redacted_count == 3


def test_resultado_json_protege_campos_sensibles_y_conserva_catalogo():
    protected = protect_json_for_model(
        json.dumps(
            {
                "codigo": "AB12",
                "estado": "en reparto",
                "email": "ana@example.com",
                "direccion": "Av. Primavera 123",
                "productos": [{"id": 734, "nombre": "Ramo de rosas"}],
            }
        )
    )
    payload = json.loads(protected.value)
    assert payload["email"] == "[correo protegido]"
    assert payload["direccion"] == "[dirección protegida]"
    assert payload["codigo"] == "AB12"
    assert payload["productos"][0]["nombre"] == "Ramo de rosas"
    assert protected.redacted_count == 2


def test_historial_redacta_pii_antigua_pero_conserva_ultimo_turno_operativo():
    messages = [
        {"role": "user", "content": "Mi correo anterior era viejo@example.com"},
        {"role": "assistant", "content": "Entendido"},
        {"role": "user", "content": "Rastrea AB12 con nuevo@example.com"},
    ]
    protected, count = minimize_historical_messages(messages)
    assert "viejo@example.com" not in protected[0]["content"]
    assert "nuevo@example.com" in protected[2]["content"]
    assert count == 1


def test_perfil_no_expone_contacto_en_system_prompt():
    protected, count = protect_profile(
        {
            "nombre": "Ana",
            "email": "ana@example.com",
            "telefono": "987654321",
            "objetivo": "rosas blancas",
        }
    )
    assert protected == {"nombre": "Ana", "objetivo": "rosas blancas"}
    assert count == 2


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("donregalo_desconocida", {}),
        ("donregalo_detalle_producto", {"id": "734"}),
        ("donregalo_detalle_producto", {"id": 734, "admin": True}),
        ("donregalo_buscar_productos", {"limite": 31}),
        ("donregalo_buscar_productos", {"categoria": "../secretos"}),
        (
            "donregalo_rastrear_pedido",
            {"email": "correo-invalido", "codigo": "AB12"},
        ),
    ],
)
def test_mcp_rechaza_tool_campos_tipos_limites_y_formatos(tool, arguments):
    with pytest.raises(ParameterValidationError):
        validate_mcp_arguments(tool, arguments)


def test_mcp_acepta_solo_el_contrato_necesario_para_rastreo():
    arguments = {"email": "ana@example.com", "codigo": "AB12_2026"}
    assert validate_mcp_arguments("donregalo_rastrear_pedido", arguments) == arguments


@pytest.mark.asyncio
async def test_mcp_bloquea_antes_de_inicializar_o_abrir_la_red():
    with pytest.raises(ParameterValidationError):
        await mcp_client._call_unobserved(
            None,
            "donregalo_detalle_producto",
            {"id": 734, "campo_inesperado": "no"},
        )


@pytest.mark.asyncio
async def test_agente_no_ejecuta_tool_con_parametros_extra(monkeypatch):
    responses = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-invalid",
                                    "type": "function",
                                    "function": {
                                        "name": "buscar_productos",
                                        "arguments": '{"q":"rosas","admin":true}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {"choices": [{"message": {"role": "assistant", "content": "¿Qué estilo prefieres?"}}]},
        ]
    )

    async def fake_completion(_client, _payload):
        return next(responses)

    async def forbidden_execute(_name, _args):
        raise AssertionError("los parámetros inválidos no deben llegar al executor")

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
    monkeypatch.setattr(agent_mod, "_chat_completion", fake_completion)
    monkeypatch.setattr(agent_mod, "execute_tool", forbidden_execute)

    result = await agent_mod.run_specialist(
        [{"role": "system", "content": "atiende"}, {"role": "user", "content": "rosas"}],
        wa_id="51900000000",
        tools_override=[tool],
        include_handoff=False,
        include_memory=False,
    )
    assert result.user_facing == "¿Qué estilo prefieres?"
    assert result.tools_used == []
