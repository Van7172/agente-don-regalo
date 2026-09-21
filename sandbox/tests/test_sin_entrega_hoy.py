"""Por capacidad ya no se aceptan entregas para el mismo día."""
from __future__ import annotations

from datetime import date

import pytest

from app.harness import master as master_mod
from app.harness.campaigns import (
    YELLOW_FLOWERS_CATALOG_URL,
    yellow_flowers_catalog_reply,
)
from app.harness.checkout import advance_checkout
from app.harness.same_day import (
    SAME_DAY_CUTOFF_REPLY,
    asks_for_same_day_delivery,
    is_same_day_delivery,
    same_day_cutoff_reply,
)
from app.harness.state import ConversationState, clear_local_cache


def test_mensajes_oficiales_cuadran_con_lo_acordado():
    assert same_day_cutoff_reply() == SAME_DAY_CUTOFF_REPLY
    assert "ya no estamos tomando pedidos con entrega para hoy" in same_day_cutoff_reply()
    assert "mañana" in same_day_cutoff_reply().casefold()

    reply = yellow_flowers_catalog_reply()
    assert YELLOW_FLOWERS_CATALOG_URL not in reply
    assert "Flores Amarillas" in reply
    assert "ya no estamos tomando pedidos" in reply
    assert "mañana" in reply.casefold()


@pytest.mark.parametrize(
    "text",
    [
        "Quiero un desayuno para hoy",
        "¿Pueden entregarlo hoy?",
        "Necesito el envío para hoy mismo",
        "¿Llegan hoy a Miraflores?",
        "Lo quiero hoy",
    ],
)
def test_reconoce_pedido_para_hoy(text):
    assert asks_for_same_day_delivery(text)


@pytest.mark.parametrize(
    "text",
    [
        "todo en orden hoy",
        "gracias",
        "quiero un desayuno para mañana",
        "¿Qué tienen hoy disponible en el catálogo?",
    ],
)
def test_no_secuestra_usos_inofensivos_de_hoy(text):
    assert not asks_for_same_day_delivery(text)


def test_misma_fecha_local_es_entrega_hoy():
    hoy = date(2026, 9, 21)
    assert is_same_day_delivery(hoy, today=hoy)
    assert is_same_day_delivery("2026-09-21", today=hoy)
    assert not is_same_day_delivery("2026-09-22", today=hoy)


@pytest.mark.parametrize("texto", ["hoy", "hoy mismo", "21/09", "21 de septiembre"])
def test_checkout_rechaza_entrega_para_hoy(texto):
    hoy = date(2026, 9, 21)
    state = ConversationState(
        checkout_step="date",
        district="Miraflores",
        chosen_product_id=1,
        chosen_product_name="Ramo",
    )
    state, reply, meta = advance_checkout(state, texto, today=hoy)

    assert state.checkout_step == "date"
    assert state.date in ("", None)
    assert "ya no estamos tomando pedidos con entrega para hoy" in reply
    assert "mañana" in reply.casefold()
    assert not meta.get("escalate")


def test_checkout_sigue_aceptando_manana():
    hoy = date(2026, 9, 21)
    state = ConversationState(
        checkout_step="date",
        district="Miraflores",
        chosen_product_id=1,
        chosen_product_name="Ramo",
    )
    state, reply, _ = advance_checkout(state, "mañana", today=hoy)

    assert state.date == "2026-09-22"
    assert state.checkout_step == "schedule"
    assert "horario" in reply.casefold()


@pytest.mark.asyncio
async def test_pedido_para_hoy_fuera_del_cierre_responde_sin_llm(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        raise AssertionError("El corte de hoy no debe depender del LLM")

    monkeypatch.setattr(master_mod, "_run_specialty", no_llm)
    clear_local_cache()

    reply = await master_mod.run_master(
        [{"role": "user", "content": "Quiero un desayuno para hoy"}],
        wa_id="51999",
        conversation_id=1201,
    )

    assert reply is not None
    assert "ya no estamos tomando pedidos con entrega para hoy" in reply
    assert "mañana" in reply.casefold()


@pytest.mark.asyncio
async def test_flores_amarillas_avisan_el_corte_sin_pdf(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        raise AssertionError("Flores Amarillas no debe depender del LLM")

    async def no_tool(*_args, **_kwargs):
        raise AssertionError("Flores Amarillas no debe consultar el catálogo")

    monkeypatch.setattr(master_mod, "_run_specialty", no_llm)
    monkeypatch.setattr(master_mod, "execute_tool", no_tool)
    clear_local_cache()

    reply = await master_mod.run_master(
        [{"role": "user", "content": "Hola, ¿tienen flores amarillas?"}],
        wa_id="51999",
        conversation_id=1202,
    )

    assert reply is not None
    assert YELLOW_FLOWERS_CATALOG_URL not in reply
    assert "Flores Amarillas" in reply
    assert "ya no estamos tomando pedidos" in reply
    assert "mañana" in reply.casefold()
