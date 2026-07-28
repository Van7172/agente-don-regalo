"""Fiestas Patrias 2026: sin asesores el 28–29; pedidos desde el 30."""
from datetime import date

import pytest

from app.harness.checkout import advance_checkout
from app.harness.holidays import (
    CLOSED_DELIVERY_DATES,
    NEXT_OPEN_DELIVERY,
    closed_delivery_reply,
    is_closed_delivery,
    staff_offline,
    staff_offline_reply,
)
from app.harness.state import ConversationState


def test_28_y_29_estan_cerrados():
    assert is_closed_delivery(date(2026, 7, 28))
    assert is_closed_delivery("2026-07-29")
    assert not is_closed_delivery(date(2026, 7, 30))
    assert CLOSED_DELIVERY_DATES == frozenset({date(2026, 7, 28), date(2026, 7, 29)})
    assert NEXT_OPEN_DELIVERY == date(2026, 7, 30)


def test_staff_offline_solo_en_feriado():
    assert staff_offline(date(2026, 7, 28))
    assert staff_offline(date(2026, 7, 29))
    assert not staff_offline(date(2026, 7, 27))
    assert not staff_offline(date(2026, 7, 30))


@pytest.mark.parametrize(
    "hoy,texto",
    [
        (date(2026, 7, 28), "hoy"),
        (date(2026, 7, 28), "mañana"),
        (date(2026, 7, 28), "28/07"),
        (date(2026, 7, 28), "29 de julio"),
        (date(2026, 7, 29), "hoy"),
        (date(2026, 7, 29), "28/07/2026"),
    ],
)
def test_checkout_rechaza_entrega_en_feriado(hoy, texto):
    state = ConversationState(
        checkout_step="date",
        district="Miraflores",
        chosen_product_id=1,
        chosen_product_name="Ramo",
    )
    state, reply, meta = advance_checkout(state, texto, today=hoy)

    assert state.checkout_step == "date"
    assert state.date in ("", None) or is_closed_delivery(state.date) is False
    assert state.date != "2026-07-28" and state.date != "2026-07-29"
    assert "feriado" in reply.casefold() or "28" in reply
    assert "30" in reply
    assert not meta.get("escalate")


def test_checkout_acepta_desde_el_30():
    state = ConversationState(
        checkout_step="date",
        district="Miraflores",
        chosen_product_id=1,
        chosen_product_name="Ramo",
    )
    state, reply, _ = advance_checkout(state, "30/07", today=date(2026, 7, 28))

    assert state.date == "2026-07-30"
    assert state.checkout_step == "schedule"
    assert "horario" in reply.casefold()


@pytest.mark.asyncio
async def test_handoff_en_feriado_no_cede_el_chat(monkeypatch):
    from app.crm import http_client as crm_http
    from app.services import agent as agent_mod
    import app.harness.holidays as hol

    sent = []
    modes = []

    async def fake_say(wa_id, text, persist):
        sent.append(text)
        return "wamid.x"

    async def fake_set_mode(conversation_id, mode, **kw):
        modes.append((conversation_id, mode))

    monkeypatch.setattr(agent_mod, "_say", fake_say)
    monkeypatch.setattr(agent_mod, "notify_team", lambda *a, **k: None)
    monkeypatch.setattr(hol, "lima_today", lambda: date(2026, 7, 28))
    monkeypatch.setattr(crm_http, "set_mode", fake_set_mode)

    result = await agent_mod.perform_handoff(
        wa_id="51999",
        conversation_id=7,
        motivo="quiero un asesor",
        use_external_crm=True,
        persist=None,
    )

    assert result is not None
    assert sent, "debe avisar al cliente"
    assert "30" in sent[0]
    assert modes == [], "no debe pasar a HUMAN en feriado"


def test_mensajes_mencionan_el_30():
    assert "30" in closed_delivery_reply()
    assert "30" in staff_offline_reply()
    assert "pago" in staff_offline_reply(for_payment=True).casefold()
