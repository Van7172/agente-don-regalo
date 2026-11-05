"""Cuando el bot cede el chat, el cliente sabe qué sigue y es verdad.

Dos fallos: quien confirmaba el pedido se quedaba esperando a que alguien le dijera
cómo pagar, y "en seguida continúan contigo" salía también a las 11 de la noche o en
domingo, cuando no hay nadie. Y lo que el cierre decía al ceder ("Entendido, sin
problema…") no llegaba: el cliente recibía "¡Claro! Te conecto".
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.harness import master as master_mod
from app.harness.checkout import _ABANDON_REPLY, payment_handoff_message
from app.harness.orders import lima_today
from app.harness.state import ConversationState, load_state, save_state
from app.harness.team_hours import next_opening, team_is_open
from app.payments import PAYMENT_METHODS_IMAGE_URL
from app.services import agent as agent_mod

LIMA = ZoneInfo("America/Lima")


def _dt(dia, hora, minuto=0):
    # Noviembre de 2026: el lunes es el 2, el sábado el 7 y el domingo el 8.
    return datetime(2026, 11, dia, hora, minuto, tzinfo=LIMA)


@pytest.mark.parametrize(
    "momento,abierto",
    [
        (_dt(4, 7), True),
        (_dt(4, 6, 59), False),
        (_dt(4, 21, 59), True),
        (_dt(4, 22), False),
        (_dt(7, 7), True),
        (_dt(7, 19, 59), True),
        (_dt(7, 20), False),
        (_dt(8, 12), False),
    ],
)
def test_horario_de_atencion(momento, abierto):
    assert team_is_open(momento) is abierto


@pytest.mark.parametrize(
    "momento,esperado",
    [
        (_dt(4, 5), "hoy a las 7:00 am"),
        (_dt(4, 23), "mañana a las 7:00 am"),
        (_dt(7, 21), "el lunes a las 7:00 am"),
        (_dt(8, 12), "mañana a las 7:00 am"),
    ],
)
def test_proxima_apertura(momento, esperado):
    assert next_opening(momento) == esperado


@pytest.fixture
def espias(monkeypatch):
    ev = {"enviados": [], "imagenes": [], "persistidos": []}

    async def texto(_wa, mensaje):
        ev["enviados"].append(mensaje)
        return "wamid.t"

    async def imagen(_wa, url, caption=""):
        ev["imagenes"].append((url, caption))
        return "wamid.i"

    async def sin_ruido(*_a, **_k):
        return True

    async def cede(*_a, **_k):
        return True

    monkeypatch.setattr(agent_mod, "send_message", texto)
    monkeypatch.setattr(agent_mod, "send_image", imagen)
    monkeypatch.setattr(agent_mod, "notify_team", sin_ruido)
    monkeypatch.setattr(agent_mod, "_cede_a_humano", cede)
    return ev


@pytest.mark.asyncio
async def test_en_horario_promete_que_siguen_en_seguida(espias, monkeypatch):
    monkeypatch.setattr(agent_mod, "team_is_open", lambda *_: True)
    await agent_mod.perform_handoff(wa_id="51999", conversation_id=1, motivo="pidió asesor")
    assert "en seguida" in espias["enviados"][0]


@pytest.mark.asyncio
async def test_fuera_de_horario_dice_cuando_responden(espias, monkeypatch):
    monkeypatch.setattr(agent_mod, "team_is_open", lambda *_: False)
    monkeypatch.setattr(agent_mod, "next_opening", lambda *_: "mañana a las 7:00 am")
    await agent_mod.perform_handoff(wa_id="51999", conversation_id=1, motivo="pidió asesor")
    mensaje = espias["enviados"][0]
    assert "en seguida" not in mensaje
    assert "mañana a las 7:00 am" in mensaje


@pytest.mark.asyncio
async def test_un_mensaje_propio_tambien_avisa_fuera_de_horario(espias, monkeypatch):
    monkeypatch.setattr(agent_mod, "team_is_open", lambda *_: False)
    monkeypatch.setattr(agent_mod, "next_opening", lambda *_: "el lunes a las 7:00 am")
    await agent_mod.perform_handoff(
        wa_id="51999", conversation_id=1, motivo="x", message="Entendido, sin problema"
    )
    assert "Entendido, sin problema" in espias["enviados"][0]
    assert "el lunes a las 7:00 am" in espias["enviados"][0]


@pytest.mark.asyncio
async def test_lo_que_dice_el_cierre_al_ceder_llega_al_cliente(espias, monkeypatch):
    monkeypatch.setattr(agent_mod, "team_is_open", lambda *_: True)
    await save_state(2, ConversationState(checkout_step="date", chosen_product_id=1, chosen_product_name="X"))
    await master_mod.run_master(
        [{"role": "user", "content": "ya no deseo el pedido"}], wa_id="51999", conversation_id=2
    )
    assert espias["enviados"] == [_ABANDON_REPLY]


def _resumen(**kw):
    base = dict(
        checkout_step="summary", chosen_product_id=1, chosen_product_name="Ramo de Rosas",
        chosen_product_price_sol=95.0, district="Santiago De Surco", id_distrito=5,
        shipping_fee_sol=15.0, date=(lima_today() + timedelta(days=30)).isoformat(),
        time_slot="09:00 AM a 11:00 AM",
    )
    base.update(kw)
    return ConversationState(**base)


def test_con_total_el_mensaje_trae_cuanto_y_como_pagar():
    mensaje = payment_handoff_message(_resumen())
    assert "S/110.00" in mensaje
    assert PAYMENT_METHODS_IMAGE_URL in mensaje
    assert "tarjeta" in mensaje.casefold()


def test_sin_total_no_invita_a_pagar_una_cifra_que_no_sabe():
    mensaje = payment_handoff_message(_resumen(chosen_product_price_sol=None))
    assert PAYMENT_METHODS_IMAGE_URL not in mensaje
    assert "total" in mensaje.casefold()


@pytest.mark.asyncio
async def test_al_confirmar_llegan_el_total_y_los_medios_de_pago(espias, monkeypatch):
    monkeypatch.setattr(agent_mod, "team_is_open", lambda *_: True)
    await save_state(3, _resumen())

    async def anuncia(*_a, **_k):
        return None

    async def pedido(*_a, **_k):
        return {"id_pedido_temporal": 7}

    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "create_temporal_order", pedido)
    monkeypatch.setattr(master_mod.settings, "pedido_temporal_enabled", True, raising=False)

    salida = await master_mod.run_master(
        [{"role": "user", "content": "sí"}], wa_id="51999", conversation_id=3
    )

    assert salida == agent_mod.HANDOFF_DONE
    assert any("S/110.00" in m for m in espias["enviados"])
    assert espias["imagenes"][0][0] == PAYMENT_METHODS_IMAGE_URL
    state = await load_state(3)
    assert state.checkout_step == "payment"
    assert state.pedido_temporal_id == 7
