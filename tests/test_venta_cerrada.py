"""Venta cerrada: la señal que el agente le deja al vendedor.

Cuando el cliente confirma el resumen, el bot ya tiene el pedido entero. Antes eso
se disolvía en el hilo y el asesor lo reconstruía leyendo veinte mensajes. Ahora
queda como un objeto: el CRM pinta el chat en verde y el watchdog reclama si nadie
entra a cobrarlo.
"""
import json

import pytest

from app.harness import sale as sale_mod
from app.harness.state import ConversationState

CERRADA = ConversationState(
    chosen_product_name="Desayuno Dulce Rosita",
    chosen_product_id=11,
    district="Miraflores",
    shipping_fee_sol=14.18,
    date="viernes 18",
    time_slot="09:00 AM a 11:00 AM",
    checkout_step="payment",
    handoff_reason="cliente listo para pagar",
)


def test_la_venta_lleva_todo_el_pedido():
    venta = sale_mod.build_sale(CERRADA)

    assert venta["producto"] == "Desayuno Dulce Rosita"
    assert venta["distrito"] == "Miraflores"
    assert venta["envio_sol"] == 14.18
    assert venta["fecha"] == "viernes 18"
    assert venta["horario"] == "09:00 AM a 11:00 AM"


@pytest.mark.parametrize(
    "estado",
    [
        ConversationState(district="Miraflores", date="viernes"),          # sin producto
        ConversationState(chosen_product_name="Desayuno", date="viernes"),  # sin distrito
        ConversationState(chosen_product_name="Desayuno", district="Surco"),  # sin fecha
    ],
)
def test_un_cierre_incompleto_no_se_anuncia(estado):
    """Un chat verde sin pedido dentro es peor que ninguno.

    El asesor entra, no encuentra nada, y deja de fiarse del color.
    """
    assert sale_mod.is_complete(sale_mod.build_sale(estado)) is False


def test_una_venta_completa_si_se_anuncia():
    assert sale_mod.is_complete(sale_mod.build_sale(CERRADA)) is True


@pytest.mark.asyncio
async def test_announce_deja_el_pedido_en_el_crm(monkeypatch):
    guardado = {}
    avisos = []

    async def put_setting(key, value):
        guardado[key] = value

    async def notify_team(text):
        avisos.append(text)

    monkeypatch.setattr(sale_mod.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(sale_mod.crm_http, "put_setting", put_setting)
    monkeypatch.setattr("app.services.messenger.notify_team", notify_team)

    venta = await sale_mod.announce(42, CERRADA)

    assert venta is not None
    assert "sale_42" in guardado
    guardada = json.loads(guardado["sale_42"])
    assert guardada["producto"] == "Desayuno Dulce Rosita"

    # Y el equipo se entera en el momento, con el pedido delante.
    assert len(avisos) == 1
    assert "VENTA CERRADA" in avisos[0]
    assert "Desayuno Dulce Rosita" in avisos[0]
    assert "Miraflores" in avisos[0]


@pytest.mark.asyncio
async def test_si_el_crm_falla_el_handoff_sigue_adelante(monkeypatch):
    """El aviso es importante; el cliente que espera para pagar, más."""
    async def put_setting(key, value):
        raise RuntimeError("CRM caído")

    monkeypatch.setattr(sale_mod.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(sale_mod.crm_http, "put_setting", put_setting)

    venta = await sale_mod.announce(42, CERRADA)

    assert venta is not None  # no explota: el cierre continúa


def test_el_resumen_es_legible_de_un_vistazo():
    texto = sale_mod.summary(sale_mod.build_sale(CERRADA), 42)

    assert "VENTA CERRADA" in texto
    assert "Desayuno Dulce Rosita" in texto
    assert "S/14.18" in texto
    assert "09:00 AM a 11:00 AM" in texto
