"""Un turno completo del orquestador, con OpenAI y las tools simuladas.

Verifica el lazo entero: percibir → clasificar → delegar → reducir → persistir.
Sin esto, el contrato `AgentResult` estaría probado en las piezas pero no en el
recorrido real, que es donde vivía el bug.
"""
import json

import pytest

from app.harness import master as master_mod
from app.harness import state as state_mod
from app.harness.state import ConversationState, clear_local_cache, load_state
from app.prompts.core import SAFETY_MARKER
from app.services import agent as agent_mod

CATALOGO = {
    "data": [
        {"id_producto": 11, "nombre": "Terrario Familia Panditas", "precio_sol": 87.5},
        {"id_producto": 22, "nombre": "Ramo de Girasoles", "precio_sol": 120.0},
    ]
}


def _tool_call(name, args=None):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args or {}),
                    },
                }],
            }
        }]
    }


def _final(text):
    return {"choices": [{"message": {"content": text, "tool_calls": None}}]}


@pytest.fixture
def harness(monkeypatch):
    """Captura lo que se manda a OpenAI y a las tools; nada sale a la red."""
    espia = {"systems": [], "tool_args": [], "enviados": []}

    async def fake_execute_tool(name, args):
        espia["tool_args"].append((name, args))
        return json.dumps(CATALOGO, ensure_ascii=False)

    async def fake_send(wa_id, text, *a, **kw):
        espia["enviados"].append(text)
        return "wamid.fake"

    async def fake_typing(*a, **kw):
        return None

    monkeypatch.setattr(agent_mod, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(agent_mod, "send_message", fake_send)
    monkeypatch.setattr(agent_mod, "set_typing", fake_typing)
    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: False)
    clear_local_cache()
    yield espia
    clear_local_cache()


def _mock_llm(monkeypatch, espia, respuestas):
    async def fake_chat(_client, payload):
        espia["systems"].append(payload["messages"][0]["content"])
        return respuestas.pop(0)

    monkeypatch.setattr(agent_mod, "_chat_completion", fake_chat)


@pytest.mark.asyncio
async def test_una_busqueda_deja_los_ids_en_el_estado(harness, monkeypatch):
    _mock_llm(monkeypatch, harness, [
        _tool_call("buscar_semantico", {"q": "peluches"}),
        _final("• 🎁 *Terrario Familia Panditas* — S/87.50"),
    ])

    reply = await master_mod.run_master(
        [{"role": "user", "content": "busco peluches para mi novia"}],
        wa_id="51999",
        conversation_id=1,
    )

    assert reply is not None
    state = await load_state(1)
    # Los ids salen del resultado de la tool, no de la prosa de la respuesta.
    assert state.shown_product_ids == [11, 22]
    assert state.intent_last == "catalog_search"

    # Y el especialista recibió el CORE con las restricciones.
    assert SAFETY_MARKER in harness["systems"][0]
    assert "ESPECIALISTA: CATÁLOGO" in harness["systems"][0]


@pytest.mark.asyncio
async def test_la_segunda_busqueda_ve_los_ids_ya_mostrados(harness, monkeypatch):
    """'otras opciones' tiene que llegar al especialista con lo ya mostrado."""
    await state_mod.save_state(1, ConversationState(shown_product_ids=[11, 22]))

    _mock_llm(monkeypatch, harness, [_final("Por ahora eso es todo 😊")])

    await master_mod.run_master(
        [{"role": "user", "content": "tienes otras opciones, no esas"}],
        wa_id="51999",
        conversation_id=1,
    )

    system = harness["systems"][0]
    assert "11" in system and "22" in system
    assert "excluir_ids" in system


@pytest.mark.asyncio
async def test_un_producto_ya_mostrado_no_se_repite(harness, monkeypatch):
    await state_mod.save_state(1, ConversationState(shown_product_ids=[11]))

    _mock_llm(monkeypatch, harness, [
        _tool_call("buscar_semantico", {"q": "mas opciones"}),
        _final("Te muestro otras opciones"),
    ])

    await master_mod.run_master(
        [{"role": "user", "content": "muéstrame más peluches"}],
        wa_id="51999",
        conversation_id=1,
    )

    state = await load_state(1)
    # 11 ya estaba; el turno solo aporta el 22 como novedad.
    assert state.shown_product_ids == [11, 22]


@pytest.mark.asyncio
async def test_un_saludo_lo_atiende_el_concierge_sin_tools(harness, monkeypatch):
    """El orquestador no responde él: delega, incluso un 'hola'."""
    _mock_llm(monkeypatch, harness, [_final("¡Hola! 😊 ¿En qué te ayudo hoy?")])

    reply = await master_mod.run_master(
        [{"role": "user", "content": "hola"}],
        wa_id="51999",
        conversation_id=1,
    )

    assert reply == "¡Hola! 😊 ¿En qué te ayudo hoy?"
    assert harness["tool_args"] == [], "el concierge no debe consultar nada"
    assert "ESPECIALISTA: RECEPCIÓN" in harness["systems"][0]
    assert SAFETY_MARKER in harness["systems"][0]


@pytest.mark.asyncio
async def test_el_cierre_arranca_con_el_producto_resuelto(harness, monkeypatch):
    """'quiero el panditas' → el FSM sabe qué se está vendiendo."""
    await state_mod.save_state(
        1,
        ConversationState(
            shown_product_ids=[11, 22],
            recent_products=[
                {"id_producto": 11, "nombre": "Terrario Familia Panditas"},
                {"id_producto": 22, "nombre": "Ramo de Girasoles"},
            ],
        ),
    )

    reply = await master_mod.run_master(
        [{"role": "user", "content": "quiero el panditas"}],
        wa_id="51999",
        conversation_id=1,
    )

    state = await load_state(1)
    assert state.chosen_product_id == 11
    assert state.chosen_product_name == "Terrario Familia Panditas"
    assert state.checkout_step == "district"
    assert "distrito" in (reply or "").casefold()
    # El cierre es determinista: ni una llamada al LLM.
    assert harness["systems"] == []


@pytest.mark.asyncio
async def test_una_referencia_ambigua_pregunta_en_vez_de_adivinar(harness, monkeypatch):
    await state_mod.save_state(
        1,
        ConversationState(
            recent_products=[
                {"id_producto": 11, "nombre": "Terrario Familia Panditas"},
                {"id_producto": 22, "nombre": "Ramo de Girasoles"},
            ],
        ),
    )

    reply = await master_mod.run_master(
        [{"role": "user", "content": "ese lo quiero"}],
        wa_id="51999",
        conversation_id=1,
    )

    assert "Panditas" in reply and "Girasoles" in reply
    state = await load_state(1)
    assert state.chosen_product_id is None
    assert state.checkout_step == "idle", "no se arranca un cierre a ciegas"
