"""Lo que importa al desplegar: los clientes que ya iban a medias.

El estado vive en el CRM y sobrevive al despliegue, así que el código nuevo recibe
documentos que escribió el viejo. También recoge lo que encontró la auditoría previa:
un distrito corregido con la API caída que conservaba el id y la tarifa del anterior.
"""
import json
from datetime import timedelta

import httpx
import pytest

from app.guardrails.conversation import asks_customization
from app.harness import coverage as cov
from app.harness import master as master_mod
from app.harness.contracts import EscalateReason
from app.harness.orders import build_body, lima_today
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state
from app.harness.trace import Trace
from app.tools import orders as orders_tool

_cobertura_real = master_mod._checkout_coverage
_datos_reales = master_mod._load_product_facts
_FUTURA = (lima_today() + timedelta(days=30)).isoformat()


def _resumen(**kw) -> ConversationState:
    base = dict(
        checkout_step="summary", chosen_product_id=1, chosen_product_name="Ramo",
        chosen_product_price_sol=95.0, product_facts_for=1,
        district="Miraflores", id_distrito=7, shipping_fee_sol=16.0, shipping_fee_usd=4.7,
        date=_FUTURA, time_slot="09:00 AM a 11:00 AM", dedicatoria="Sin dedicatoria",
        nombre_destinatario="Ana", apellidos_destinatario="Ruiz", telefono_destinatario="999888777",
        direccion="Jr. Ica 200", tipo=0, nombre_cliente="Pedro", apellidos_cliente="Díaz",
        email_cliente="p@x.pe",
    )
    base.update(kw)
    return ConversationState(**base)


@pytest.fixture
def mundo(monkeypatch):
    ev = {"handoff": [], "ventas": [], "pedidos": [], "fichas": 0}

    async def caida(_client, _args):
        raise RuntimeError("API caída")

    async def ficha(name, args=None):
        ev["fichas"] += 1
        return json.dumps({"data": {
            "id_producto": args["id_producto"], "nombre": "Ramo", "precio_sol": 95.0, "precio_usd": 28.0,
            "categoria": "Arreglos Florales", "categoria_slug": "arreglos-florales",
        }})

    async def handoff(**kw):
        ev["handoff"].append(kw)
        return EscalateReason(motivo=kw.get("motivo") or "")

    async def anuncia(_cid, state):
        ev["ventas"].append(state.chosen_product_name)
        return {}

    async def pedido(state, _wa):
        ev["pedidos"].append((state.id_distrito, state.shipping_fee_sol))
        return {"id_pedido_temporal": 1}

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", caida)
    monkeypatch.setattr(master_mod, "_checkout_coverage", _cobertura_real)
    monkeypatch.setattr(master_mod, "_load_product_facts", _datos_reales)
    monkeypatch.setattr(master_mod, "execute_tool", ficha)
    monkeypatch.setattr(master_mod, "perform_handoff", handoff)
    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "create_temporal_order", pedido)
    monkeypatch.setattr(master_mod.settings, "pedido_temporal_enabled", True, raising=False)
    return ev


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999000111", conversation_id=cid
    )


# ── Corregir el distrito con la API caída ─────────────────────────────

@pytest.mark.asyncio
async def test_un_distrito_sin_validar_no_hereda_id_ni_tarifa_del_anterior(mundo):
    await save_state(1, _resumen())
    await _turno(1, "cambia el distrito")
    reply = await _turno(1, "Surco")
    state = await load_state(1)
    assert state.district == "Surco"
    assert state.id_distrito is None
    assert state.shipping_fee_sol is None
    assert state.shipping_fee_usd is None
    assert "Total: S/" not in reply


@pytest.mark.asyncio
async def test_el_pedido_no_sale_con_el_id_del_distrito_anterior(mundo):
    await save_state(2, _resumen())
    await _turno(2, "cambia el distrito")
    await _turno(2, "Surco")
    await _turno(2, "sí")
    assert mundo["pedidos"] == [(None, None)]


def test_el_fsm_tampoco_hereda_al_guardar_un_distrito_sin_validar():
    from app.harness.checkout import advance_checkout

    state = ConversationState(
        checkout_step="district", chosen_product_id=1, id_distrito=7, shipping_fee_sol=16.0, shipping_fee_usd=4.7,
    )
    state, _, _ = advance_checkout(state, "Surco", today=lima_today())
    assert (state.id_distrito, state.shipping_fee_sol, state.shipping_fee_usd) == (None, None, None)


# ── Resumen que se mostró antes de este despliegue ────────────────────

def _resumen_antiguo() -> dict:
    """Un documento como lo guarda `HEAD`: sin precio ni los campos nuevos."""
    doc = _resumen().to_dict()
    for campo in (
        "chosen_product_price_sol", "chosen_is_breakfast", "product_facts_for", "district_options",
        "checkout_return_step", "budget_amount", "budget_currency", "last_turn_at",
    ):
        doc.pop(campo, None)
    doc["campaign_slug"] = ""
    return doc


@pytest.mark.asyncio
async def test_un_resumen_antiguo_muestra_el_total_antes_de_aceptar_el_si(mundo):
    await save_state(3, ConversationState.from_dict(_resumen_antiguo()))
    reply = await _turno(3, "sí")
    state = await load_state(3)
    assert "Total: S/111.00" in reply
    assert state.checkout_step == "summary"
    assert mundo["pedidos"] == []
    assert mundo["ventas"] == []

    await _turno(3, "sí")
    assert (await load_state(3)).checkout_step == "payment"
    assert len(mundo["pedidos"]) == 1


@pytest.mark.asyncio
async def test_sin_ficha_un_resumen_antiguo_no_se_queda_atrapado(mundo, monkeypatch):
    async def sin_ficha(_name, _args=None):
        raise RuntimeError("API caída")

    monkeypatch.setattr(master_mod, "execute_tool", sin_ficha)
    await save_state(4, ConversationState.from_dict(_resumen_antiguo()))
    await _turno(4, "sí")
    assert (await load_state(4)).checkout_step == "payment"
    assert len(mundo["ventas"]) == 1


@pytest.mark.asyncio
async def test_un_resumen_nuevo_se_confirma_a_la_primera(mundo):
    await save_state(5, _resumen())
    await _turno(5, "sí")
    assert (await load_state(5)).checkout_step == "payment"


def test_un_documento_antiguo_se_carga_sin_campos_nuevos_ni_el_eliminado():
    state = ConversationState.from_dict(_resumen_antiguo())
    assert state.checkout_step == "summary"
    assert state.district_options == []
    assert state.checkout_return_step == ""
    assert state.chosen_is_breakfast is None
    assert not hasattr(state, "campaign_slug")


# ── Cambiar algo DEL producto no es cambiar DE producto ───────────────

@pytest.mark.parametrize(
    "texto",
    [
        "¿Pueden agregar chocolates al producto?",
        "¿Se puede cambiar el color del regalo?",
        "quiero cambiar el globo de mi pedido",
    ],
)
def test_cambiar_algo_del_producto_es_personalizar(texto):
    assert asks_customization(texto), texto
    assert classify_rules(texto, ConversationState()).intent == "escalate", texto


@pytest.mark.parametrize(
    "texto",
    [
        "cambiar el producto",
        "quiero cambiar de regalo",
        "cambiar el regalo",
        "¿puedo agregar otro regalo?",
        "quiero cambiar mi pedido por otro producto",
        "saca mi pedido",
        "cambiar de producto",
    ],
)
def test_cambiar_de_producto_no_es_personalizar(texto):
    assert not asks_customization(texto), texto


# ── La llamada más importante del cierre también se mide ──────────────

@pytest.mark.asyncio
async def test_crear_el_pedido_cuenta_en_la_medicion(monkeypatch):
    from app.observability import collect_turn_usage, current_turn_api

    class Cliente:
        async def post(self, url, json=None):
            return httpx.Response(201, json={"success": True, "data": {"id_pedido_temporal": 1}},
                                  request=httpx.Request("POST", url))

    with collect_turn_usage():
        await orders_tool.crear_pedido_temporal(Cliente(), {"nombre_cliente": "x"})
        assert current_turn_api()["calls"] == 1


@pytest.mark.asyncio
async def test_un_pedido_rechazado_tambien_cuenta(monkeypatch):
    from app.observability import collect_turn_usage, current_turn_api

    class Cliente:
        async def post(self, url, json=None):
            return httpx.Response(422, json={"success": False}, request=httpx.Request("POST", url))

    with collect_turn_usage():
        with pytest.raises(httpx.HTTPStatusError):
            await orders_tool.crear_pedido_temporal(Cliente(), {"nombre_cliente": "x"})
        assert current_turn_api()["calls"] == 1


# ── Sin consultas repetidas en el mismo turno ─────────────────────────

@pytest.mark.asyncio
async def test_al_llegar_al_resumen_la_ficha_se_consulta_una_vez(mundo):
    await save_state(6, _resumen(
        checkout_step="contact", product_facts_for=None, chosen_product_price_sol=None,
        nombre_cliente="", apellidos_cliente="", email_cliente="",
    ))
    await _turno(6, "Pedro Díaz pedro@gmail.com")
    assert mundo["fichas"] == 1


@pytest.mark.asyncio
async def test_al_reanudar_la_ficha_se_consulta_una_vez(mundo):
    hace_dias = lima_today() - timedelta(days=3)
    import datetime as dt

    marca = dt.datetime(hace_dias.year, hace_dias.month, hace_dias.day, 12).timestamp()
    await save_state(7, _resumen(
        checkout_step="address", product_facts_for=None, checkout_updated_at=marca, last_turn_at=marca,
    ))
    await _turno(7, "hola")
    mundo["fichas"] = 0
    await _turno(7, "sí")
    assert mundo["fichas"] == 1
