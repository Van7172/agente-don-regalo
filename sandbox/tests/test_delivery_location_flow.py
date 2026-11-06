"""Delivery location evidence and exact-hour requests across consecutive turns."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.harness import coverage, master, maps_location
from app.harness.checkout import advance_checkout, clear_checkout, parse_schedule, revalidate_delivery
from app.harness.contracts import EscalateReason, Turn
from app.harness.delivery_request import remember_delivery_date, requested_clock_time
from app.harness.router import classify_rules
from app.harness.state import ConversationState


@pytest.fixture(autouse=True)
def fixed_delivery_calendar(monkeypatch):
    from app.harness import checkout, delivery_request
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 11, 6, 10, tzinfo=ZoneInfo("America/Lima"))
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(delivery_request, "lima_today", lambda: date(2026, 11, 6))
    monkeypatch.setattr(checkout, "lima_today", lambda: date(2026, 11, 6))
    monkeypatch.setattr(checkout, "datetime", FixedDatetime)


@pytest.fixture
def districts(monkeypatch):
    async def read(*args):
        return {"data": [{"id_distrito": 1, "nombre": "Callao", "tarifa_sol": 20, "tarifa_usd": 6},
                         {"id_distrito": 2, "nombre": "Miraflores", "tarifa_sol": 15, "tarifa_usd": 4}]}
    monkeypatch.setattr(coverage.catalog, "distritos_cobertura", read)
    monkeypatch.setattr(master, "_commercial_intent", _no_commercial)


async def _no_commercial(*args):
    return None


async def _turn(state, text):
    intent = classify_rules(text, state).intent
    result = await master._handle(intent, Turn(text=text), state)
    state.patch(result.state_patch)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Podrá hacer la entrega?", "¿Puede hacer la entrega?", "Podría hacer la entrega?", ".."])
async def test_question_or_punctuation_is_not_a_district(text, districts):
    result = await coverage.resolve_coverage(text, ConversationState())
    assert "No ubico" not in result["user_facing"]
    assert "https://maps" not in result["user_facing"]
    assert result["structured"].get("resolved_district") is None


def test_urls_are_never_place_candidates():
    assert coverage.extract_place_candidates("https://maps.app.goo.gl/abc") == []
    assert coverage.extract_place_candidates("https://maps.app.goo.gl/abc\nEs en Callao") == ["Callao"]


@pytest.mark.asyncio
async def test_maps_result_immediately_asks_confirmation_without_quoting_link(monkeypatch, districts):
    async def locate(url):
        return "Callao"
    monkeypatch.setattr(coverage, "resolve_maps_district", locate)
    state = ConversationState()
    result = await _turn(state, "https://maps.app.goo.gl/abc\nEsta es la dirección. La entrega para las 7 am del 11 de noviembre de 2026.")
    assert "Según el enlace de Maps proporcionado, ¿la dirección queda en Callao?" in result.user_facing
    assert "de 7 a 9 a. m." in result.user_facing
    assert "¿Es indispensable" in result.user_facing
    assert state.maps_district_candidate == "Callao"
    assert state.date == "2026-11-11"
    assert state.requested_delivery_time == "07:00"
    assert state.time_slot == "" and not state.district
    assert state.shipping_fee_sol is None


@pytest.mark.asyncio
async def test_maps_yes_confirms_only_district_even_if_team_was_mentioned(districts):
    state = ConversationState(maps_district_candidate="Callao", maps_url="https://maps.app.goo.gl/abc",
                              delivery_time_pending=True, requested_delivery_time="07:00", handoff_offered=True)
    assert classify_rules("si", state).intent == "coverage"
    result = await _turn(state, "si")
    assert not result.escalate
    assert state.district.casefold() == "callao"
    assert not state.maps_district_candidate
    assert state.shipping_fee_sol == 20
    assert "¿Es indispensable" in result.user_facing
    assert not state.time_slot


@pytest.mark.asyncio
async def test_confirmed_district_and_first_exact_hour_question(districts):
    state = ConversationState(maps_district_candidate="Callao")
    result = await _turn(state, "Sí Callao, solo quisiera que la entrega fuese a las 7 am.")
    assert state.district.casefold() == "callao"
    assert "¿Es indispensable" in result.user_facing
    assert not result.escalate


@pytest.mark.asyncio
async def test_essential_hour_performs_real_handoff_not_sale(monkeypatch):
    calls = []
    async def handoff(**kw):
        calls.append(kw)
        return EscalateReason(motivo=kw["motivo"])
    monkeypatch.setattr(master, "perform_handoff", handoff)
    state = ConversationState(delivery_time_pending=True, requested_delivery_time="07:00", district="Callao", date="2026-11-11")
    result = await _turn(state, "sí")
    assert result.escalate and len(calls) == 1
    assert calls[0]["is_payment"] is False
    assert "07:00" in calls[0]["motivo"]
    assert state.delivery_time_requires_team and not state.time_slot


@pytest.mark.asyncio
async def test_flexible_customer_explicitly_accepts_a_window():
    state = ConversationState(delivery_time_pending=True, requested_delivery_time="07:00", date="2026-11-11")
    result = await _turn(state, "No")
    assert "¿Qué rango" in result.user_facing
    assert state.time_slot == "" and state.delivery_time_pending
    result = await _turn(state, "de 7 a 9")
    assert state.time_slot == "07:00 AM a 09:00 AM"
    assert not state.delivery_time_pending
    assert "no está garantizada" in result.user_facing


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["No es indispensable", "No tiene que ser a las 7 en punto", "No necesito una hora exacta"])
async def test_negated_requirement_does_not_escalate(text):
    state = ConversationState(delivery_time_pending=True, requested_delivery_time="07:00")
    result = await _turn(state, text)
    assert not result.escalate and "¿Qué rango" in result.user_facing
    assert not state.time_slot


@pytest.mark.asyncio
async def test_product_number_is_not_an_accepted_delivery_window(monkeypatch):
    state = ConversationState(delivery_time_pending=True, requested_delivery_time="07:00")
    result = await master._handle_delivery_request(Turn(text="quiero el desayuno 2"), state, intent="catalog_search")
    assert result is None and not state.time_slot


@pytest.mark.asyncio
async def test_window_acceptance_resumes_checkout_once(monkeypatch):
    state = ConversationState(checkout_step="schedule", delivery_time_pending=True,
                              requested_delivery_time="07:00", date="2026-11-11",
                              district="Callao", chosen_product_id=1,
                              product_facts_for=1, chosen_is_breakfast=True)
    result = await _turn(state, "de 7 a 9")
    assert state.checkout_step == "card"
    assert state.time_slot == "07:00 AM a 09:00 AM"
    assert "tarjeta" in result.user_facing
    assert not state.delivery_time_pending


@pytest.mark.asyncio
async def test_unresolved_maps_asks_only_missing_district(monkeypatch):
    async def locate(url):
        return ""
    monkeypatch.setattr(coverage, "resolve_maps_district", locate)
    result = await _turn(ConversationState(), "https://maps.app.goo.gl/abc")
    assert "Recibí el enlace" in result.user_facing
    assert "¿En qué distrito" in result.user_facing
    assert "No ubico" not in result.user_facing


@pytest.mark.asyncio
async def test_maps_candidate_can_be_rejected_or_corrected(districts):
    state = ConversationState(maps_district_candidate="Callao")
    result = await _turn(state, "no")
    assert not state.maps_district_candidate and not state.district
    state.maps_district_candidate = "Callao"
    await _turn(state, "Es en Miraflores")
    assert state.district.casefold() == "miraflores"
    assert not state.maps_district_candidate


@pytest.mark.parametrize("text,expected", [("a las 7 en punto", "07:00"), ("7 am", "07:00"),
    ("a las 7:30 am", "07:30"), ("07:00", "07:00"), ("7 en punto", "07:00"),
    ("a las 5 pm", "17:00"), ("de 7 a 9", ""),
    ("9 am a 11 am", ""), ("11 de noviembre", ""), ("https://maps.app.goo.gl/7am", "")])
def test_requested_hour_is_separate_from_accepted_window(text, expected):
    assert requested_clock_time(text) == expected
    if expected:
        assert parse_schedule(text, "2026-11-11") is None


def test_checkout_does_not_advance_or_confirm_exact_hour():
    state = ConversationState(checkout_step="schedule", date="2026-11-11")
    state, reply, meta = advance_checkout(state, "a las 7 en punto", today=date(2026, 11, 6))
    assert state.checkout_step == "schedule" and not state.time_slot
    assert state.delivery_time_pending and "¿Es indispensable" in reply
    assert not meta.get("escalate")
    state.checkout_step = "summary"
    assert "¿Es indispensable" in revalidate_delivery(state, today=date(2026, 11, 6))


def test_slot_memory_and_clear_are_consistent():
    state = ConversationState(checkout_step="schedule", date="2026-11-11")
    remember_delivery_date(state, "mañana", today=date(2026, 11, 6))
    assert state.date == "2026-11-11"
    state.delivery_time_pending = True
    state.requested_delivery_time = "07:00"
    state.maps_district_candidate = "Callao"
    restored = ConversationState.from_dict(state.to_dict())
    assert restored.delivery_time_pending and restored.maps_district_candidate == "Callao"
    clear_checkout(restored)
    assert not restored.delivery_time_pending and not restored.requested_delivery_time
    assert not restored.maps_district_candidate


@pytest.mark.asyncio
async def test_maps_redirect_metadata_and_external_redirect_rejection():
    calls = []
    def serve(request):
        calls.append(str(request.url))
        if request.url.host == "maps.app.goo.gl":
            return httpx.Response(302, headers={"location": "https://www.google.com/maps/place/Ubicacion"})
        return httpx.Response(200, text='<meta property="og:title" content="Dirección, Callao, Perú">')
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        assert await maps_location.resolve_maps_district("https://maps.app.goo.gl/abc", client=client) == "Callao"
    assert len(calls) == 2
    calls.clear()
    def unsafe(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secrets"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(unsafe)) as client:
        assert await maps_location.resolve_maps_district("https://maps.app.goo.gl/abc", client=client) == ""
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["https://maps.app.goo.gl.evil.test/x", "http://maps.app.goo.gl/x",
    "https://www.google.com.evil.test/maps", "https://user@maps.app.goo.gl/x", "https://maps.app.goo.gl:8443/x"])
def test_only_google_maps_origins_are_fetched(url):
    assert not maps_location.allowed_maps_url(url)


def test_ambiguous_metadata_abstains_and_district_beats_province():
    assert maps_location.district_from_metadata(["Miraflores y San Isidro"]) == ""
    assert maps_location.district_from_metadata(["La Perla, Callao, Perú"]) == "La Perla"
    assert maps_location.district_from_metadata(["San Juan de Miraflores, Lima"]) == "San Juan de Miraflores"


@pytest.mark.asyncio
async def test_maps_new_destination_cannot_reuse_old_fee_or_confirm_sale(monkeypatch, districts):
    async def locate(url):
        return ""
    monkeypatch.setattr(coverage, "resolve_maps_district", locate)
    state = ConversationState(checkout_step="summary", district="Miraflores", id_distrito=2,
                              shipping_fee_sol=15, shipping_fee_usd=4, date="2026-11-11")
    await _turn(state, "https://maps.app.goo.gl/abc")
    assert not state.district and state.id_distrito is None
    assert state.shipping_fee_sol is None and state.maps_location_pending
    assert "¿Corresponde al punto de entrega" in revalidate_delivery(state)
    result = await _turn(state, "Es en Callao")
    assert state.district == "Callao" and state.shipping_fee_sol == 20
    assert not state.maps_location_pending


@pytest.mark.asyncio
async def test_public_master_sends_one_direct_question_and_preserves_slots(monkeypatch, districts):
    from app.harness.state import load_state, save_state
    async def locate(url):
        return "Callao"
    monkeypatch.setattr(coverage, "resolve_maps_district", locate)
    cid = 914661
    await save_state(cid, ConversationState(presented=True))
    messages = [{"role": "user", "content": "https://maps.app.goo.gl/abc\nLa entrega para las 7 am del 11 de noviembre de 2026."}]
    reply = await master.run_master(messages, wa_id="51999999999", conversation_id=cid)
    assert "Según el enlace de Maps proporcionado, ¿la dirección queda en Callao?" in reply
    assert "de 7 a 9 a. m." in reply and "¿Es indispensable" in reply
    state = await load_state(cid)
    assert state.date == "2026-11-11" and state.maps_district_candidate == "Callao"
    assert not state.handoff_offered
    messages.extend([{"role": "assistant", "content": reply}, {"role": "user", "content": "Sí Callao"}])
    reply = await master.run_master(messages, wa_id="51999999999", conversation_id=cid)
    state = await load_state(cid)
    assert state.district == "Callao" and not state.time_slot
    assert "¿Es indispensable" in reply and "No ubico" not in reply
