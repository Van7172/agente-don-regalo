"""Landmarks and pins refine a district; neither is a new district name."""
from datetime import date

import pytest

from app.harness import coverage, master
from app.harness.checkout import advance_checkout, revalidate_delivery, clear_checkout, summary_prompt
from app.harness.contracts import Turn
from app.harness.delivery_location import delivery_reference, delivery_address
from app.harness.orders import build_body
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state

PIN = "https://maps.google.com/?q=-12.00000,-77.10000"  # synthetic, never fetched


@pytest.fixture(autouse=True)
def isolated_tools(monkeypatch):
    async def districts(*args):
        return {"data": [{"id_distrito": 1, "nombre": "Callao", "tarifa_sol": 20, "tarifa_usd": 6},
                         {"id_distrito": 2, "nombre": "Miraflores", "tarifa_sol": 15, "tarifa_usd": 4}]}
    async def maps(*args):
        return ""
    async def commercial(*args):
        return None
    monkeypatch.setattr(coverage.catalog, "distritos_cobertura", districts)
    monkeypatch.setattr(coverage, "resolve_maps_district", maps)
    monkeypatch.setattr(master, "_commercial_intent", commercial)


async def turn(state, text):
    result = await master._handle(classify_rules(text, state).intent, Turn(text=text), state)
    state.patch(result.state_patch)
    return result


@pytest.mark.asyncio
async def test_airport_dialogue_district_reference_pin_confirmation():
    state = ConversationState()
    result = await turn(state, "Hacen deliver")
    assert "Hacemos delivery" in result.user_facing
    await turn(state, "Callao")
    assert state.district == "Callao" and state.shipping_fee_sol == 20
    result = await turn(state, "Aeropuerto Jorge chavez")
    assert "referencia" in result.user_facing and "Callao" in result.user_facing
    assert "¿En qué punto" in result.user_facing and "No ubico" not in result.user_facing
    assert state.delivery_reference == "Aeropuerto Jorge chavez"
    assert state.district == "Callao" and state.shipping_fee_sol == 20
    assert result.tools_used == []
    result = await turn(state, PIN)
    assert "Recibí la ubicación. ¿Corresponde al punto de entrega" in result.user_facing
    assert "Aeropuerto Jorge chavez, en Callao" in result.user_facing
    assert "Según el enlace" not in result.user_facing  # context, not geocoding evidence
    assert state.maps_previous_district == "Callao"
    assert state.shipping_fee_sol is None and state.maps_location_pending
    result = await turn(state, "Sí")
    assert state.district == "Callao" and state.shipping_fee_sol == 20
    assert state.maps_location_confirmed and not state.maps_location_pending
    assert not state.delivery_point_pending
    assert PIN in delivery_address(state)
    assert "¿Qué regalo" in result.user_facing


@pytest.mark.asyncio
async def test_public_harness_does_not_append_another_address_question():
    cid = 914662
    await save_state(cid, ConversationState(presented=True, district="Callao", id_distrito=1,
                                          shipping_fee_sol=20, chosen_product_id=1,
                                          checkout_step="address"))
    history = [{"role": "user", "content": "Aeropuerto Jorge Chavez"}]
    reply = await master.run_master(history, wa_id="synthetic-reference", conversation_id=cid)
    assert reply.count("?") == 1 and "¿En qué punto" in reply
    history.extend([{"role": "assistant", "content": reply}, {"role": "user", "content": PIN}])
    reply = await master.run_master(history, wa_id="synthetic-reference", conversation_id=cid)
    assert reply.count("?") == 1 and "¿Corresponde" in reply
    state = await load_state(cid)
    assert state.maps_location_pending and state.delivery_reference


@pytest.mark.asyncio
async def test_reference_without_district_asks_missing_district_not_maps():
    state = ConversationState()
    result = await turn(state, "Hospital de la Solidaridad")
    assert "¿En qué distrito" in result.user_facing and "Maps" not in result.user_facing
    assert not state.district
    await turn(state, "Callao")
    assert state.delivery_reference == "Hospital de la Solidaridad"
    assert state.delivery_point_pending


@pytest.mark.asyncio
async def test_precise_point_without_pin_is_kept():
    state = ConversationState(district="Callao", delivery_reference="Aeropuerto Jorge Chávez", delivery_point_pending=True)
    result = await turn(state, "Puerta 3 de llegadas")
    assert state.delivery_point == "Puerta 3 de llegadas" and not state.delivery_point_pending
    assert "Anotado el punto" in result.user_facing
    assert "Callao" == state.district


@pytest.mark.asyncio
async def test_district_in_business_name_does_not_override_customer_district():
    state = ConversationState(district="Callao", shipping_fee_sol=20)
    await turn(state, "Hotel Miraflores")
    assert state.district == "Callao" and state.shipping_fee_sol == 20
    assert state.delivery_reference == "Hotel Miraflores"


@pytest.mark.asyncio
async def test_reference_can_explicitly_change_district():
    state = ConversationState(district="Callao", shipping_fee_sol=20)
    result = await turn(state, "Hotel Central, en Miraflores")
    assert state.district == "Miraflores" and state.shipping_fee_sol == 15
    assert state.delivery_reference == "Hotel Central, en Miraflores"
    assert "¿En qué punto" in result.user_facing


@pytest.mark.asyncio
async def test_conflicting_maps_district_does_not_reuse_landmark(monkeypatch):
    async def maps(*args):
        return "Miraflores"
    monkeypatch.setattr(coverage, "resolve_maps_district", maps)
    state = ConversationState(district="Callao", shipping_fee_sol=20, delivery_reference="Aeropuerto Jorge Chávez")
    result = await turn(state, PIN)
    assert "indica Miraflores, pero antes me indicaste Callao" in result.user_facing
    assert state.shipping_fee_sol is None
    await turn(state, "Sí")
    assert state.district == "Miraflores" and state.shipping_fee_sol == 15
    assert not state.delivery_reference


@pytest.mark.asyncio
async def test_rejected_pin_is_not_a_confirmed_address():
    state = ConversationState(district="Callao", delivery_reference="Aeropuerto Jorge Chávez")
    await turn(state, PIN)
    result = await turn(state, "No")
    assert "Gracias por aclararlo" in result.user_facing
    assert state.maps_location_pending and not state.maps_location_confirmed
    assert PIN not in delivery_address(state)
    assert "¿En qué distrito" in revalidate_delivery(state)
    response = await turn(state, "Callao")
    assert "¿En qué punto" in response.user_facing
    assert not state.maps_location_confirmed and not state.maps_url
    assert state.delivery_point_pending
    assert PIN not in delivery_address(state)


@pytest.mark.asyncio
async def test_new_reference_invalidates_old_pin_but_keeps_district():
    state = ConversationState(district="Callao", maps_url=PIN, maps_location_confirmed=True,
                              delivery_reference="Aeropuerto Jorge Chávez", direccion="Antigua dirección")
    await turn(state, "Hotel Costa Azul")
    assert state.district == "Callao"
    assert not state.maps_url and not state.maps_location_confirmed and not state.direccion
    assert state.delivery_reference == "Hotel Costa Azul"


@pytest.mark.asyncio
async def test_changing_district_clears_stale_location():
    state = ConversationState(district="Callao", maps_url=PIN, maps_location_confirmed=True,
                              delivery_reference="Aeropuerto Jorge Chávez", delivery_point="Puerta 3", direccion="Antigua")
    await turn(state, "Miraflores")
    assert state.district == "Miraflores"
    assert not state.delivery_reference and not state.delivery_point
    assert not state.maps_url and not state.direccion


@pytest.mark.parametrize("text", ["Aeropuerto Jorge Chávez", "Hospital Central", "Clínica San Pablo",
                                  "Centro comercial Plaza Norte", "Hotel Costa Azul", "Es en el Aeropuerto Jorge Chávez"])
def test_reference_routes_deterministically(text):
    assert delivery_reference(text)
    assert classify_rules(text, ConversationState(district="Callao")).intent == "coverage"


@pytest.mark.parametrize("text", ["Quiero un desayuno para el aeropuerto", "¿Hacen entregas al hospital?",
                                  "Hotel de rosas que precio tiene", "gracias", "Callao"])
def test_product_or_question_is_not_silently_stored_as_reference(text):
    assert not delivery_reference(text)


def test_card_dedication_is_not_location():
    state = ConversationState(checkout_step="card_text", district="Callao")
    assert classify_rules("Hotel de mis sueños", state).intent == "checkout"


@pytest.mark.asyncio
async def test_regular_street_address_still_advances_checkout_without_landmark_question():
    state = ConversationState(checkout_step="address", chosen_product_id=1, product_facts_for=1,
                              district="Callao", date="2026-11-11", time_slot="07:00 AM a 09:00 AM")
    result = await turn(state, "Calle Cambia el Globo 123")
    assert state.checkout_step == "contact"
    assert state.direccion == "Calle Cambia el Globo 123"
    assert not state.delivery_reference and not state.delivery_point_pending


def test_reference_blocked_order_cannot_be_created_until_point_confirmed():
    state = complete_state()
    state.delivery_point_pending = True
    assert build_body(state, "51999999999") is None
    assert "¿En qué punto" in revalidate_delivery(state, today=date(2026, 11, 6))
    state, reply, meta = advance_checkout(state, "sí", today=date(2026, 11, 6))
    assert not meta.get("create_order") and state.checkout_step == "summary"


def complete_state():
    return ConversationState(checkout_step="summary", district="Callao", id_distrito=1, date="2026-11-11",
        time_slot="07:00 AM a 09:00 AM", chosen_product_id=1, chosen_product_name="Desayuno",
        nombre_cliente="Ana", apellidos_cliente="Prueba", email_cliente="test@example.test",
        nombre_destinatario="Luis", apellidos_destinatario="Prueba", tipo=1,
        delivery_reference="Aeropuerto Jorge Chávez", maps_url=PIN, maps_location_confirmed=True)


def test_confirmed_pin_is_carried_to_order_and_summary_without_duplicates():
    state = complete_state()
    body = build_body(state, "51999999999")
    assert body and "Aeropuerto Jorge Chávez" in body["direccion"] and PIN in body["direccion"]
    assert PIN in summary_prompt(state)
    state.checkout_step = "address"
    state, _, _ = advance_checkout(state, "Es oficina", today=date(2026, 11, 6))
    assert state.checkout_step == "contact" and PIN in state.direccion
    assert delivery_address(state).count(PIN) == 1
    assert build_body(state, "51999999999")["direccion"].count(PIN) == 1


def test_new_fields_persist_and_clear():
    state = complete_state()
    restored = ConversationState.from_dict(state.to_dict())
    assert restored.delivery_reference == state.delivery_reference
    clear_checkout(restored)
    assert not restored.delivery_reference and not restored.maps_location_confirmed
    assert not restored.delivery_point and not restored.maps_previous_district
