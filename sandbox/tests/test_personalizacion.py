"""Cambiar algo de un producto lo decide una persona, y el bot lo dice sin rodeos.

La API no modela personalización: no hay componentes, ni sustituciones, ni precios por
item. Pero "¿se puede cambiar el peluche por chocolates?" iba a catálogo por la palabra
"peluche", y ese especialista no puede ceder el chat: el modelo improvisaba o se quedaba
sin salida. Se reconoce en código y se deriva con el motivo y el producto.
"""
import pytest

from app.guardrails import handoff_policy
from app.guardrails.conversation import asks_customization
from app.harness import master as master_mod
from app.harness.contracts import AgentResult, EscalateReason
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state

PIDE_CAMBIOS = [
    "¿se puede cambiar el peluche por chocolates?",
    "se puede cambiar el peluche por chocolates y que llegue hoy antes de las 2?",
    "quiero cambiar el globo",
    "cambia el peluche por una taza",
    "pueden quitarle el croissant?",
    "¿Qué contiene? ¿Se puede quitar y aumentar otros, y qué opciones hay?",
    "¿puedo agregar chocolates?",
    "le pueden agregar una taza",
    "se puede aumentar otra taza",
    "quiero añadirle un peluche",
    "reemplacen el jugo por café",
    "sustituir las rosas por girasoles",
    "¿se puede personalizar?",
    "pueden personalizarlo con su nombre?",
    "¿pueden ponerle su nombre a la taza?",
    "lo quiero sin el globo",
]

NO_PIDE_CAMBIOS = [
    "quiero un regalo personalizado",
    "tienen regalos personalizados?",
    "es para una persona muy especial",
    "cambia la fecha",
    "quiero cambiar la fecha",
    "cambia la dirección a Av. Lima 500",
    "quiero cambiar el distrito",
    "cambia el nombre",
    "cambiar el producto",
    "quiero cambiar de regalo",
    "cambiar de opinión",
    "agregar una tarjeta",
    "sin tarjeta",
    "sin dedicatoria",
    "¿hay cambios y devoluciones?",
    "¿qué incluye?",
    "Jr. Quito 200",
    "aumentó el precio?",
    "saca mi pedido",
    "quiero un desayuno",
]


@pytest.mark.parametrize("texto", PIDE_CAMBIOS)
def test_reconoce_que_pide_cambiar_el_producto(texto):
    assert asks_customization(texto), texto


@pytest.mark.parametrize("texto", NO_PIDE_CAMBIOS)
def test_no_confunde_otras_cosas_con_personalizar(texto):
    assert not asks_customization(texto), texto


@pytest.mark.parametrize("texto", PIDE_CAMBIOS)
def test_el_router_lo_deriva_y_la_politica_lo_permite(texto):
    state = ConversationState(recent_products=[{"id_producto": 1, "nombre": "Desayuno"}])
    assert classify_rules(texto, state).intent == "escalate", texto
    assert handoff_policy([{"role": "user", "content": texto}]).allow, texto


@pytest.mark.parametrize(
    "paso,texto,intent",
    [
        ("card_text", "Quiero cambiar el mundo por ti", "checkout"),
        ("card_text", "Se puede agregar amor?", "checkout"),
        ("address", "Calle Cambia el Globo 123", "checkout"),
        ("date", "¿se puede cambiar el peluche por chocolates?", "escalate"),
        ("address", "¿pueden quitarle el globo?", "escalate"),
    ],
)
def test_dentro_del_cierre_el_texto_libre_no_es_un_pedido(paso, texto, intent):
    state = ConversationState(checkout_step=paso, chosen_product_id=1, chosen_product_name="Ramo")
    assert classify_rules(texto, state).intent == intent


@pytest.fixture
def cesion(monkeypatch):
    ev = {}

    async def handoff(**kw):
        ev.update(kw)
        return EscalateReason(motivo=kw.get("motivo") or "", ceded=ev.pop("_falla", True))

    async def sin_modelo(*_a, **_k):
        raise AssertionError("personalizar no pasa por el modelo")

    monkeypatch.setattr(master_mod, "perform_handoff", handoff)
    monkeypatch.setattr(master_mod, "run_specialist", sin_modelo)
    return ev


async def _turno(cid, texto, **kw):
    return await master_mod.run_master(
        [{"role": "user", "content": texto, **kw}], wa_id="51999", conversation_id=cid
    )


@pytest.mark.asyncio
async def test_cede_el_chat_diciendo_por_que_y_con_el_producto(cesion):
    await save_state(1, ConversationState(chosen_product_id=7, chosen_product_name="Desayuno Clásico"))
    salida = await _turno(1, "¿se puede cambiar el peluche por chocolates?")
    assert salida == master_mod.HANDOFF_DONE
    assert "equipo" in cesion["message"]
    assert "precio" in cesion["message"]
    assert "Desayuno Clásico" in cesion["motivo"]
    assert "chocolates" in cesion["motivo"]


@pytest.mark.asyncio
async def test_sin_producto_tambien_cede_y_lo_dice_al_equipo(cesion):
    await _turno(2, "pueden quitarle el croissant?")
    assert "croissant" in cesion["motivo"]
    assert "Personaliz" in cesion["motivo"]


@pytest.mark.asyncio
async def test_no_arrastra_un_motivo_anterior(cesion):
    await save_state(3, ConversationState(handoff_reason="cliente listo para pagar"))
    await _turno(3, "quiero cambiar el globo")
    assert "listo para pagar" not in cesion["motivo"]


@pytest.mark.asyncio
async def test_la_cesion_no_pierde_lo_que_llevaba_del_pedido(cesion):
    await save_state(4, ConversationState(
        checkout_step="address", chosen_product_id=7, chosen_product_name="Ramo", district="Miraflores",
    ))
    await _turno(4, "¿pueden quitarle el globo?")
    state = await load_state(4)
    assert state.checkout_step == "address"
    assert state.district == "Miraflores"
    assert state.chosen_product_id == 7


@pytest.mark.asyncio
async def test_si_la_cesion_falla_el_bot_sigue_hablando(cesion):
    cesion["_falla"] = False
    salida = await _turno(5, "quiero cambiar el globo")
    assert salida == master_mod.HANDOFF_FAILED_MSG
