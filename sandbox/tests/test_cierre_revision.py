"""Hallazgos de la revisión cruzada: secuencias que los tests por paso no veían.

Cada paso era correcto por separado. Fallaba lo que ocurre ENTRE pasos: la ficha que
responde tarde y revela que era un desayuno, la franja que empieza mientras el cliente
escribe, el CRM que rechaza la cesión justo después de anunciar la venta.
"""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.harness import master as master_mod
from app.harness.budget import extract_budget
from app.harness.checkout import advance_checkout
from app.harness.contracts import EscalateReason
from app.harness.orders import lima_today
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state
from app.payments import asks_for_payment_methods

LIMA = ZoneInfo("America/Lima")
HOY = date(2026, 11, 4)
_datos_reales = master_mod._load_product_facts
_FUTURA = (lima_today() + timedelta(days=30)).isoformat()
FRANJA = "09:00 AM a 11:00 AM"


def _resumen(**kw) -> ConversationState:
    base = dict(
        checkout_step="summary", chosen_product_id=1, chosen_product_name="Ramo de Rosas",
        chosen_product_price_sol=95.0, district="Miraflores", id_distrito=7, shipping_fee_sol=12.0,
        date="2026-11-11", time_slot=FRANJA, dedicatoria="Sin dedicatoria",
        nombre_destinatario="Ana", apellidos_destinatario="Ruiz", telefono_destinatario="999888777",
        direccion="Jr. Ica 200", tipo=0, nombre_cliente="Pedro", email_cliente="p@x.pe",
    )
    base.update(kw)
    return ConversationState(**base)


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999000111", conversation_id=cid
    )


# ── La entrega se comprueba justo antes de confirmar ──────────────────

def _a_las(hora, minuto=0):
    return datetime(2026, 11, 4, hora, minuto, tzinfo=LIMA)


def test_una_franja_que_empezo_mientras_escribia_no_se_confirma():
    state = _resumen(date=HOY.isoformat(), time_slot="07:00 AM a 09:00 AM")
    state, reply, meta = advance_checkout(state, "sí", today=HOY, now=_a_las(10, 30))
    assert not meta.get("create_order")
    assert state.checkout_step == "schedule"
    assert state.time_slot == ""
    assert state.checkout_return_step == "summary"
    assert "11:00 AM a 02:00 PM" in reply


def test_elegir_otra_franja_vuelve_al_resumen_y_entonces_se_confirma():
    state = _resumen(date=HOY.isoformat(), time_slot="07:00 AM a 09:00 AM")
    state, _, _ = advance_checkout(state, "sí", today=HOY, now=_a_las(10, 30))
    state, reply, _ = advance_checkout(state, "1", today=HOY, now=_a_las(10, 30))
    assert state.checkout_step == "summary"
    assert state.time_slot == "11:00 AM a 02:00 PM"
    assert "Resumen" in reply
    _, _, meta = advance_checkout(state, "sí", today=HOY, now=_a_las(10, 30))
    assert meta.get("create_order") is True


def test_una_fecha_que_paso_mientras_tanto_no_se_confirma():
    state, _, meta = advance_checkout(_resumen(date="2026-11-03"), "sí", today=HOY)
    assert not meta.get("create_order")
    assert state.checkout_step == "date"
    assert state.checkout_return_step == "summary"


def test_si_la_franja_sigue_viva_se_confirma():
    state = _resumen(date=HOY.isoformat(), time_slot="11:00 AM a 02:00 PM")
    _, _, meta = advance_checkout(state, "sí", today=HOY, now=_a_las(10, 30))
    assert meta.get("create_order") is True


def test_un_desayuno_con_franja_de_la_tarde_no_se_confirma():
    state = _resumen(chosen_is_breakfast=True, time_slot="04:00 PM a 07:00 PM")
    state, _, meta = advance_checkout(state, "sí", today=HOY)
    assert not meta.get("create_order")
    assert state.checkout_step == "schedule"


@pytest.mark.asyncio
async def test_si_la_ficha_revela_tarde_que_es_desayuno_se_corrige_la_fecha(monkeypatch):
    """La ficha falló al elegir (el nombre no dice "desayuno") y se aceptó hoy."""
    async def ficha(name, args=None):
        return json.dumps({"data": {
            "id_producto": args["id_producto"], "nombre": "1era Sorpresa del día", "precio_sol": 119.0,
            "precio_usd": 35.0, "categoria": "Desayunos", "categoria_slug": "desayunos",
        }})

    monkeypatch.setattr(master_mod, "_load_product_facts", _datos_reales)
    monkeypatch.setattr(master_mod, "execute_tool", ficha)
    await save_state(1, _resumen(
        checkout_step="contact", chosen_product_name="1era Sorpresa del día", chosen_is_breakfast=None,
        chosen_product_price_sol=None, product_facts_for=1, date=lima_today().isoformat(),
        nombre_cliente="", email_cliente="",
    ))
    reply = await _turno(1, "Pedro Díaz pedro@gmail.com")
    state = await load_state(1)
    assert state.chosen_is_breakfast is True
    assert state.checkout_step == "date"
    assert state.date == ""
    assert state.checkout_return_step == "summary"
    assert "Resumen" not in reply


# ── No se anuncia una venta que nadie va a atender ────────────────────

@pytest.fixture
def venta(monkeypatch):
    ev = {"ventas": [], "pedidos": [], "cede": False}

    async def anuncia(_cid, state):
        ev["ventas"].append(state.chosen_product_name)

    async def pedido(state, _wa):
        ev["pedidos"].append(state.chosen_product_id)
        return {"id_pedido_temporal": 77}

    async def handoff(**kw):
        return EscalateReason(motivo=kw.get("motivo") or "", is_payment=True, ceded=ev["cede"])

    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "create_temporal_order", pedido)
    monkeypatch.setattr(master_mod, "perform_handoff", handoff)
    monkeypatch.setattr(master_mod.settings, "pedido_temporal_enabled", True, raising=False)
    return ev


@pytest.mark.asyncio
async def test_si_la_cesion_falla_no_se_anuncia_la_venta(venta):
    await save_state(2, _resumen(date=_FUTURA))
    reply = await _turno(2, "sí")
    state = await load_state(2)
    assert venta["ventas"] == []
    assert reply != master_mod.HANDOFF_DONE
    assert state.checkout_step == "summary", "puede volver a confirmar"


@pytest.mark.asyncio
async def test_reintentar_tras_una_cesion_fallida_no_duplica_el_pedido(venta):
    await save_state(3, _resumen(date=_FUTURA))
    await _turno(3, "sí")
    venta["cede"] = True
    salida = await _turno(3, "sí")
    assert salida == master_mod.HANDOFF_DONE
    assert venta["ventas"] == ["Ramo de Rosas"]
    assert venta["pedidos"] == [1], "un solo pedido temporal"


@pytest.mark.asyncio
async def test_con_cesion_correcta_se_anuncia(venta):
    venta["cede"] = True
    await save_state(4, _resumen(date=_FUTURA))
    await _turno(4, "sí")
    assert venta["ventas"] == ["Ramo de Rosas"]


# ── Cancelar un pedido, se mencione o no cómo se pagó ─────────────────

@pytest.mark.parametrize(
    "texto",
    ["quiero cancelar mi pedido con yape", "quiero cancelar mi pedido, pagué con yape",
     "cancelo la compra que pagué con tarjeta"],
)
def test_cancelar_un_pedido_deriva_aunque_mencione_el_pago(texto):
    assert classify_rules(texto, ConversationState()).intent == "escalate"
    assert not asks_for_payment_methods(texto)


# ── El tope explícito manda ───────────────────────────────────────────

@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("tengo 200 soles pero puedo gastar hasta 100", (100.0, "PEN")),
        ("tengo 300 pero máximo 150 soles", (150.0, "PEN")),
        ("cuesta 50 dólares? mi presupuesto es 40 dólares", (40.0, "USD")),
        ("hasta 40 dólares", (40.0, "USD")),
        ("hasta $30", (30.0, "USD")),
        ("hasta 100 soles", (100.0, "PEN")),
    ],
)
def test_el_tope_explicito_gana_a_otra_cifra(texto, esperado):
    assert extract_budget(texto) == esperado


# ── Quitar de una lista también sobrevive a una carrera ───────────────

@pytest.mark.asyncio
async def test_un_producto_retirado_no_resucita_si_otro_escribe_a_la_vez():
    productos = [{"id_producto": 1, "nombre": "A"}, {"id_producto": 2, "nombre": "B"}]
    await save_state(5, ConversationState(recent_products=productos, shown_product_ids=[1, 2]))

    turno = await load_state(5)
    foto = turno.to_dict()

    otro = await load_state(5)
    base_otro = otro.to_dict()
    otro.handoff_at = 123.0
    await save_state(5, otro, base=base_otro)

    turno.recent_products = [p for p in turno.recent_products if p["id_producto"] != 1]
    turno.shown_product_ids = [i for i in turno.shown_product_ids if i != 1]
    await save_state(5, turno, base=foto)

    final = await load_state(5)
    assert [p["id_producto"] for p in final.recent_products] == [2]
    assert final.shown_product_ids == [2]
    assert final.handoff_at == 123.0, "lo del otro escritor se conserva"


@pytest.mark.asyncio
async def test_vaciar_lo_mostrado_sobrevive_a_una_carrera():
    await save_state(6, ConversationState(shown_product_ids=[1, 2]))
    turno = await load_state(6)
    foto = turno.to_dict()

    otro = await load_state(6)
    base_otro = otro.to_dict()
    otro.handoff_at = 123.0
    await save_state(6, otro, base=base_otro)

    turno.shown_product_ids = []
    await save_state(6, turno, base=foto)
    assert (await load_state(6)).shown_product_ids == []


@pytest.mark.asyncio
async def test_agregar_a_la_lista_se_sigue_uniendo():
    await save_state(7, ConversationState(shown_product_ids=[1]))
    turno = await load_state(7)
    foto = turno.to_dict()

    otro = await load_state(7)
    base_otro = otro.to_dict()
    otro.shown_product_ids = [1, 2]
    await save_state(7, otro, base=base_otro)

    turno.shown_product_ids = [1, 3]
    await save_state(7, turno, base=foto)
    assert sorted((await load_state(7)).shown_product_ids) == [1, 2, 3]
