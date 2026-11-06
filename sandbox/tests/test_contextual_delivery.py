"""Test interpretation contracts and business outcomes, not literal wording."""
from dataclasses import asdict
from datetime import date

import pytest

from app.config import settings
from app.harness import contextual as c, coverage, master
from app.harness.contracts import Turn, EscalateReason
from app.harness.router import classify_rules
from app.harness.state import ConversationState, save_state, load_state


def proposal(action="reference", **values):
    data = {key: "" for key in c.SCHEMA["required"]}
    data.update(action=action, intent="coverage", confidence=.95)
    data.update(values)
    return data


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    monkeypatch.setattr(c, "lima_today", lambda: date(2026, 11, 6))


@pytest.fixture
def tools(monkeypatch):
    calls = []
    async def districts(*args):
        calls.append("districts")
        return {"data": [{"id_distrito": 1, "nombre": "Callao", "tarifa_sol": 19.99, "tarifa_usd": 5.88,
                          "informacion": "No cubre Ventanilla."}]}
    async def no_maps(*args):
        calls.append("maps")
        return ""
    async def no_composer(*args):
        return None
    monkeypatch.setattr(coverage.catalog, "distritos_cobertura", districts)
    monkeypatch.setattr(coverage, "resolve_maps_district", no_maps)
    monkeypatch.setattr(c, "compose", no_composer)
    return calls


@pytest.mark.parametrize("text,reference", [
    ("Donde trabaja ella, el taller El Roble", "el taller El Roble"),
    ("Lo recibirán en La Esquina Azul", "La Esquina Azul"),
    ("La casa comunal del parque", "La casa comunal del parque"),
])
@pytest.mark.asyncio
async def test_model_can_supply_reference_without_a_keyword_pattern(text, reference, tools):
    turn = Turn(text=text)
    plan = c.validate(proposal(reference=reference, reference_evidence=reference), turn, ConversationState(district="Callao"))
    assert plan
    state = ConversationState(district="Callao", shipping_fee_sol=19.99)
    result = await c.execute(plan, turn, state)
    assert state.delivery_reference == reference and state.district == "Callao"
    assert state.delivery_point_pending and tools == []
    assert not result.escalate


@pytest.mark.parametrize("changes", [
    {"reference": "Aeropuerto Jorge Chávez"},
    {"reference_evidence": "texto que no escribió"},
    {"confidence": float("nan")}, {"confidence": True},
    {"shipping_fee_sol": 1}, {"action": "confirm_payment"}, {"intent": "delete_order"},
])
def test_model_proposals_cannot_invent_facts_or_business_fields(changes):
    data = proposal(reference="El Roble", reference_evidence="El Roble")
    data.update(changes)
    assert c.validate(data, Turn(text="Es en El Roble"), ConversationState()) is None


def test_confirmations_require_the_corresponding_pending_decision():
    data = proposal("confirm_location", decision_evidence="claro")
    assert c.validate(data, Turn(text="claro"), ConversationState()) is None
    assert c.validate(data, Turn(text="claro"), ConversationState(maps_location_pending=True, maps_district_candidate="Callao"))
    data = proposal("essential_time", decision_evidence="sí")
    assert c.validate(data, Turn(text="sí"), ConversationState(delivery_time_pending=True)) is None


def test_low_confidence_clarifies_without_applying_proposed_facts():
    data = proposal(reference="El Roble", reference_evidence="El Roble", confidence=.2)
    plan = c.validate(data, Turn(text="El Roble"), ConversationState())
    assert plan.action == "clarify" and not plan.reference


def test_echoed_state_is_context_not_new_customer_evidence():
    data = proposal(reference="El Roble", reference_evidence="El Roble", district="Callao", district_evidence="Callao")
    plan = c.validate(data, Turn(text="El Roble"), ConversationState(district="Callao"))
    assert plan.reference == "El Roble" and not plan.district


@pytest.mark.asyncio
async def test_point_has_no_keyword_requirement_and_keeps_reference(tools):
    state = ConversationState(district="Callao", delivery_reference="La Esquina Azul", delivery_point_pending=True)
    text = "Con el vigilante del lado izquierdo"
    plan = c.validate(proposal("point", point=text, point_evidence=text), Turn(text=text), state)
    await c.execute(plan, Turn(text=text), state)
    assert state.delivery_point == text and not state.delivery_point_pending
    assert state.delivery_reference == "La Esquina Azul"


@pytest.mark.asyncio
async def test_multi_fact_turn_keeps_district_reference_date_and_requested_hour(tools):
    text = "Callao, en El Roble, el 11 de noviembre de 2026 a las 7 am"
    state = ConversationState()
    plan = c.validate(proposal("reference", district="Callao", district_evidence="Callao",
        reference="El Roble", reference_evidence="El Roble", date="2026-11-11", date_evidence="11 de noviembre de 2026",
        requested_time="07:00", time_evidence="7 am"), Turn(text=text), state)
    result = await c.execute(plan, Turn(text=text), state)
    assert state.district == "Callao" and state.delivery_reference == "El Roble"
    assert state.date == "2026-11-11" and state.requested_delivery_time == "07:00"
    assert state.delivery_time_pending and not state.time_slot
    assert state.shipping_fee_sol == 19.99 and tools == ["districts"]
    assert result.sourced_prices == [19.99]


@pytest.mark.asyncio
async def test_contextual_confirmation_rechecks_actual_coverage_not_model_price(tools):
    state = ConversationState(maps_location_pending=True, maps_district_candidate="Callao", maps_url="https://maps.app.goo.gl/test")
    turn = Turn(text="Ese mismo lugar, correcto")
    plan = c.validate(proposal("confirm_location", decision_evidence=turn.text), turn, state)
    await c.execute(plan, turn, state)
    assert state.district == "Callao" and state.shipping_fee_sol == 19.99
    assert state.maps_location_confirmed and tools == ["districts"]


@pytest.mark.asyncio
async def test_essential_hour_executes_handoff_without_creating_order(monkeypatch, tools):
    handoffs = []
    async def handoff(**kwargs):
        handoffs.append(kwargs)
        return EscalateReason(motivo=kwargs["motivo"])
    monkeypatch.setattr(master, "perform_handoff", handoff)
    state = ConversationState(requested_delivery_time="07:00", delivery_time_pending=True)
    turn = Turn(text="Si pasa de esa hora ya no habrá nadie")
    plan = c.validate(proposal("essential_time", decision_evidence=turn.text), turn, state)
    result = await c.execute(plan, turn, state)
    assert result.escalate and len(handoffs) == 1
    assert handoffs[0]["is_payment"] is False and not state.time_slot


@pytest.mark.asyncio
async def test_flexible_hour_does_not_silently_accept_window(tools):
    state = ConversationState(requested_delivery_time="07:00", delivery_time_pending=True)
    turn = Turn(text="No, puede llegar más tarde")
    plan = c.validate(proposal("flexible_time", decision_evidence=turn.text), turn, state)
    await c.execute(plan, turn, state)
    assert not state.time_slot and state.delivery_time_pending


def test_no_model_can_select_friday_early_window_or_expired_date():
    state = ConversationState(date="2026-11-13")
    data = proposal("accept_window", window="07:00 AM a 09:00 AM", decision_evidence="de 7 a 9")
    assert c.validate(data, Turn(text="de 7 a 9"), state) is None
    data = proposal("delivery_question", date="2020-01-01", date_evidence="1 de enero de 2020")
    assert c.validate(data, Turn(text="1 de enero de 2020"), state) is None


@pytest.mark.asyncio
async def test_breakfast_same_day_is_still_rejected_by_code(tools):
    state = ConversationState(chosen_is_breakfast=True)
    turn = Turn(text="Lo necesito hoy")
    plan = c.validate(proposal("delivery_question", date="2026-11-06", date_evidence="hoy"), turn, state)
    await c.execute(plan, turn, state)
    assert not state.date


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery_date,expected", [("2026-11-07", "07:00 AM a 09:00 AM"), ("2026-11-13", "")])
async def test_adding_date_keeps_accepted_window_only_when_available(tools, delivery_date, expected):
    state = ConversationState(time_slot="07:00 AM a 09:00 AM")
    text = delivery_date
    plan = c.validate(proposal("delivery_question", date=delivery_date, date_evidence=text), Turn(text=text), state)
    await c.execute(plan, Turn(text=text), state)
    assert state.date == delivery_date and state.time_slot == expected


def test_model_input_contains_context_but_no_coordinates_or_contact_fields():
    state = ConversationState(district="Callao", delivery_reference="El Roble", telefono_destinatario="999111222",
                              email_cliente="private@example.test", maps_url="https://maps.google.com/?q=-12,-77", direccion="Calle privada 12")
    data = c.perception(Turn(text="https://maps.google.com/?q=-12,-77 mi teléfono 999111222 y correo private@example.test",
                             messages=[{"role":"assistant","content":"¿Es el mismo punto?"}]), state)
    assert data["state"]["district"] == "Callao" and data["has_maps"]
    text = str(data)
    assert "999111222" not in text and "private@example.test" not in text
    assert "Calle privada" not in text and "-12,-77" not in text


@pytest.mark.asyncio
async def test_natural_rendering_can_vary_without_changing_tariff(monkeypatch):
    async def fake(*args):
        return {"message": "Llegamos al distrito indicado. ¿Qué regalo te gustaría enviar?"}
    monkeypatch.setattr(c, "_json_call", fake)
    plan = c.Interpretation("destination", "coverage", .95, district="Callao")
    raw = {"structured":{"fee_sol":19.99,"coverage_note":"No cubre Ventanilla."}}
    reply = await c.compose(plan, Turn(text="Callao"), ConversationState(district="Callao"), raw, "respaldo")
    assert "S/19.99" in reply and "Ventanilla" in reply


@pytest.mark.parametrize("message", ["El envío es S/1", "Te garantizo la entrega", "Entrega confirmada a las 7 en punto", "https://evil.test", "La ubicación ya confirmada"])
@pytest.mark.asyncio
async def test_unsafe_rendering_reverts_to_operational_response(monkeypatch, message):
    async def fake(*args):
        return {"message": message}
    monkeypatch.setattr(c, "_json_call", fake)
    reply = await c.compose(c.Interpretation("clarify","coverage",.9), Turn(text="sí"),
                            ConversationState(maps_location_pending=True), None, "respaldo")
    assert reply is None


@pytest.mark.asyncio
async def test_public_master_uses_context_before_keyword_rules(monkeypatch, tools):
    monkeypatch.setattr(settings, "contextual_delivery_enabled", True)
    monkeypatch.setattr(settings, "contextual_delivery_percent", 100)
    text = "Donde ella trabaja, en El Roble"
    plan = c.validate(proposal(reference="El Roble", reference_evidence="El Roble"), Turn(text=text), ConversationState(district="Callao"))
    async def model(*args):
        return plan
    monkeypatch.setattr(c, "interpret", model)
    cid = 914663
    await save_state(cid, ConversationState(presented=True, district="Callao"))
    await master.run_master([{"role":"user","content":text}], wa_id="synthetic-context", conversation_id=cid)
    state = await load_state(cid)
    assert state.delivery_reference == "El Roble" and state.delivery_point_pending


def test_literal_form_fields_and_explicit_escalations_keep_existing_boundary(monkeypatch):
    monkeypatch.setattr(settings, "contextual_delivery_enabled", True)
    monkeypatch.setattr(settings, "contextual_delivery_percent", 100)
    for step in ("card_text", "recipient", "contact", "payment", "done"):
        state = ConversationState(checkout_step=step, district="Callao")
        assert not c.eligible(Turn(text="El Roble"), state, classify_rules("El Roble",state))
    state = ConversationState(district="Callao")
    assert not c.eligible(Turn(text="Quiero hablar con un asesor"), state, classify_rules("Quiero hablar con un asesor",state))
    for step in ("card", "resume_confirm", "summary"):
        state = ConversationState(checkout_step=step, district="Callao")
        assert not c.eligible(Turn(text="sí"), state, classify_rules("sí", state))


@pytest.mark.asyncio
async def test_unavailable_model_returns_none_without_side_effects(monkeypatch):
    async def unavailable(*args):
        return None
    monkeypatch.setattr(c, "_json_call", unavailable)
    state = ConversationState(district="Callao")
    before = state.to_dict()
    assert await c.interpret(Turn(text="allí mismo"), state) is None
    assert state.to_dict() == before


@pytest.mark.asyncio
async def test_public_master_abstains_instead_of_repeating_keyword_guess(monkeypatch, tools):
    monkeypatch.setattr(settings, "contextual_delivery_enabled", True)
    monkeypatch.setattr(settings, "contextual_delivery_percent", 100)
    async def unavailable(*args):
        return None
    monkeypatch.setattr(c, "interpret", unavailable)
    cid = 914664
    await save_state(cid, ConversationState(presented=True, district="Callao", shipping_fee_sol=19.99))
    reply = await master.run_master([{"role":"user","content":"Aeropuerto Jorge Chávez"}],
                                    wa_id="synthetic-context", conversation_id=cid)
    state = await load_state(cid)
    assert state.district == "Callao" and state.shipping_fee_sol == 19.99
    assert not state.delivery_reference and not tools
    assert reply and "No ubico" not in reply
