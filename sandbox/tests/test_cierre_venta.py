"""El cierre vende lo que existe, a donde llegamos y al precio que es.

Salió de la misma auditoría que `test_cierre_criterio.py`: el paso de distrito
guardaba cualquier texto ("Arequipa", "cuánto es el delivery?"), el resumen pedía
confirmar sin decir cuánto se pagaba, "quiero comprar un regalo" abría el formulario
sin producto y quien ya compró volvía a anunciar su venta anterior al escribir.
"""
import json
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.delivery_windows import schedule_options_for, windows_for
from app.harness import coverage as cov
from app.harness import master as master_mod
from app.harness import orders, sale
from app.harness.checkout import advance_checkout, parse_schedule
from app.harness.contracts import AgentResult, EscalateReason
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state

LIMA = ZoneInfo("America/Lima")
HOY = date(2026, 11, 4)  # miércoles
VIERNES = "2026-11-06"
# conftest las sustituye para que la suite no salga a la red; aquí se prueban.
_cobertura_real = master_mod._checkout_coverage
_datos_reales = master_mod._load_product_facts

DISTRITOS = {
    "data": [
        {"id_distrito": 5, "nombre": "Santiago De Surco", "tarifa_sol": 15.0, "tarifa_usd": 4.41},
        {"id_distrito": 7, "nombre": "Miraflores", "tarifa_sol": 12.0, "tarifa_usd": 3.53},
    ]
}


def _en_paso(step: str, **kw) -> ConversationState:
    base = dict(checkout_step=step, chosen_product_id=101, chosen_product_name="Ramo de Rosas")
    base.update(kw)
    return ConversationState(**base)


@pytest.fixture
def mundo(monkeypatch):
    """API de prueba y efectos irreversibles espiados."""
    ev = {"handoff": [], "ventas": [], "pedidos": [], "especialista": [], "precio": 95.0, "cat": "Arreglos Florales"}

    async def distritos(_client, _args):
        return DISTRITOS

    async def herramienta(name, args=None):
        if name == "detalle_producto":
            if ev["precio"] is None:
                raise RuntimeError("API caída")
            return json.dumps({"data": {
                "id_producto": args["id_producto"],
                "nombre": "Producto",
                "precio_sol": ev["precio"],
                "precio_usd": 28.0,
                "categoria": ev["cat"],
                "categoria_slug": "x",
            }})
        return json.dumps({"data": []})

    async def handoff(**kw):
        ev["handoff"].append(kw.get("motivo"))
        return EscalateReason(motivo=kw.get("motivo") or "")

    async def anuncia(_cid, state):
        ev["ventas"].append(state.chosen_product_name)

    async def pedido(state, _wa):
        ev["pedidos"].append(state.chosen_product_id)
        return {"id_pedido_temporal": 777}

    async def especialista(intent, turn, state, **_):
        ev["especialista"].append(intent)
        return AgentResult(user_facing="Te muestro opciones 🎁")

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", distritos)
    monkeypatch.setattr(master_mod, "_checkout_coverage", _cobertura_real)
    monkeypatch.setattr(master_mod, "_load_product_facts", _datos_reales)
    monkeypatch.setattr(master_mod, "execute_tool", herramienta)
    monkeypatch.setattr(master_mod, "perform_handoff", handoff)
    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "create_temporal_order", pedido)
    monkeypatch.setattr(master_mod, "_run_specialty", especialista)
    monkeypatch.setattr(master_mod.settings, "pedido_temporal_enabled", True, raising=False)
    return ev


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999000111", conversation_id=cid
    )


# ── El distrito se valida dentro del cierre ───────────────────────────

@pytest.mark.asyncio
async def test_un_distrito_valido_trae_su_tarifa(mundo):
    await save_state(1, _en_paso("district"))
    reply = await _turno(1, "Surco")
    state = await load_state(1)
    assert "S/15.00" in reply
    assert state.district == "Santiago De Surco"
    assert state.id_distrito == 5
    assert state.shipping_fee_sol == 15.0
    assert state.checkout_step == "date"


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", ["Arequipa", "Madrid"])
async def test_lo_que_no_es_un_distrito_de_lima_no_se_guarda(mundo, texto):
    await save_state(2, _en_paso("district"))
    await _turno(2, texto)
    state = await load_state(2)
    assert state.district == ""
    assert state.checkout_step == "district"
    assert state.step_retries == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", ["cuánto es el delivery?", "qué contiene?"])
async def test_una_pregunta_en_el_paso_de_distrito_no_se_guarda(mundo, texto):
    await save_state(6, _en_paso("district"))
    await _turno(6, texto)
    state = await load_state(6)
    assert state.district == ""
    assert state.checkout_step == "district"
    assert state.step_retries == 0


@pytest.mark.asyncio
async def test_tres_lugares_que_no_ubica_ceden_el_chat(mundo):
    await save_state(3, _en_paso("district"))
    for texto in ("Arequipa", "Cusco", "Trujillo"):
        await _turno(3, texto)
    assert len(mundo["handoff"]) == 1
    assert not mundo["ventas"]


@pytest.mark.asyncio
async def test_no_repite_la_misma_pregunta_de_distrito(mundo):
    await save_state(4, _en_paso("district"))
    primero = await _turno(4, "Arequipa")
    segundo = await _turno(4, "Cusco")
    assert primero != segundo
    assert "Lima" in segundo


@pytest.mark.asyncio
async def test_si_la_cobertura_falla_el_cierre_no_se_detiene(mundo, monkeypatch):
    async def caida(_client, _args):
        raise RuntimeError("API caída")

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", caida)
    await save_state(5, _en_paso("district"))
    await _turno(5, "Surco")
    state = await load_state(5)
    assert state.district == "Surco"
    assert state.checkout_step == "date"


def test_sin_cobertura_una_pregunta_tampoco_se_guarda_como_distrito():
    state, _, _ = advance_checkout(_en_paso("district"), "cuánto es el delivery?", today=HOY)
    assert state.district == ""
    assert state.checkout_step == "district"


# ── El resumen dice cuánto se paga ────────────────────────────────────

def _casi_listo(**kw):
    # Fecha relativa: estos turnos pasan por el reloj real.
    base = dict(
        district="Santiago De Surco", id_distrito=5, shipping_fee_sol=15.0,
        date=(orders.lima_today() + timedelta(days=30)).isoformat(),
        time_slot="09:00 AM a 11:00 AM", nombre_destinatario="Ana", apellidos_destinatario="Ruiz",
        telefono_destinatario="999888777", direccion="Jr. Ica 200", tipo=0, dedicatoria="Sin dedicatoria",
    )
    base.update(kw)
    return _en_paso("contact", **base)


@pytest.mark.asyncio
async def test_el_resumen_trae_precio_envio_y_total(mundo):
    await save_state(10, _casi_listo())
    reply = await _turno(10, "Pedro Díaz pedro@gmail.com")
    assert "S/95.00" in reply
    assert "S/15.00" in reply
    assert "Total: S/110.00" in reply
    assert (await load_state(10)).checkout_step == "summary"


@pytest.mark.asyncio
async def test_sin_precio_no_inventa_el_total(mundo):
    mundo["precio"] = None
    await save_state(11, _casi_listo())
    reply = await _turno(11, "Pedro Díaz pedro@gmail.com")
    assert "Total: S/" not in reply
    assert "total" in reply.casefold()


def test_la_venta_anunciada_lleva_el_total():
    state = _casi_listo(chosen_product_price_sol=95.0)
    venta = sale.build_sale(state)
    assert venta["total_sol"] == 110.0
    assert "S/110.00" in sale.summary(venta, 9)


def test_el_aviso_dice_si_el_pedido_temporal_no_se_creo():
    assert "a mano" in sale.summary(sale.build_sale(_casi_listo()), 9)
    con_pedido = sale.summary(sale.build_sale(_casi_listo(pedido_temporal_id=7)), 9)
    assert "a mano" not in con_pedido


# ── No hay formulario sin producto ────────────────────────────────────

@pytest.mark.parametrize("texto", ["Quiero comprar un regalo para mi mamá", "quiero comprar"])
def test_querer_comprar_sin_haber_visto_nada_es_buscar(texto):
    assert classify_rules(texto, ConversationState()).intent == "catalog_search"


@pytest.mark.asyncio
async def test_sin_producto_se_ensena_catalogo_y_no_el_formulario(mundo):
    reply = await _turno(20, "Quiero comprar un regalo para mi mamá")
    state = await load_state(20)
    assert mundo["especialista"] == ["catalog_search"]
    assert state.checkout_step == "idle"
    assert "distrito" not in reply


@pytest.mark.asyncio
async def test_si_el_router_llm_dice_cierre_sin_producto_tambien_se_busca(mundo, monkeypatch):
    from app.harness.router import Classification

    async def llm(*_a, **_k):
        return Classification("checkout", 0.9, "llm")

    monkeypatch.setattr(master_mod, "classify", llm)
    await _turno(21, "lo llevo")
    assert mundo["especialista"] == ["catalog_search"]
    assert (await load_state(21)).checkout_step == "idle"


# ── Datos que el pedido temporal exige ────────────────────────────────

def test_sin_telefono_del_destinatario_se_usa_el_del_comprador():
    state = _casi_listo(
        telefono_destinatario="", nombre_cliente="Pedro", email_cliente="p@x.pe", checkout_step="summary"
    )
    body = orders.build_body(state, "51999000111", id_distrito=5)
    assert body is not None
    assert body["telefono_destinatario"] == "51999000111"


def test_si_solo_da_el_correo_se_pide_el_nombre():
    state = _casi_listo()
    state, reply, _ = advance_checkout(state, "pedro@gmail.com", today=HOY)
    assert state.checkout_step == "contact"
    assert state.email_cliente == "pedro@gmail.com"
    assert "nombre" in reply.casefold()
    state, _, _ = advance_checkout(state, "Pedro Díaz", today=HOY)
    assert state.checkout_step == "summary"
    assert (state.nombre_cliente, state.apellidos_cliente) == ("Pedro", "Díaz")
    assert state.email_cliente == "pedro@gmail.com"


def test_si_solo_da_el_nombre_se_conserva_al_pedir_el_correo():
    state = _casi_listo()
    state, _, _ = advance_checkout(state, "Pedro Díaz", today=HOY)
    assert state.checkout_step == "contact"
    state, _, _ = advance_checkout(state, "pedro@gmail.com", today=HOY)
    assert state.checkout_step == "summary"
    assert state.nombre_cliente == "Pedro"


def test_no_promete_pedir_el_telefono_despues():
    state = _en_paso("recipient")
    for texto in ("???", "12"):
        state, reply, _ = advance_checkout(state, texto, today=HOY)
        assert "después" not in reply


# ── Después de la venta ───────────────────────────────────────────────

def _vendido(**kw):
    base = dict(district="Santiago De Surco", date="2026-10-01", time_slot="09:00 AM a 11:00 AM",
                handoff_reason="cliente listo para pagar / coordinar comprobante",
                recent_products=[{"id_producto": 101, "nombre": "Ramo de Rosas"},
                                 {"id_producto": 102, "nombre": "Peluche Panda"}],
                shown_product_ids=[101, 102])
    base.update(kw)
    return _en_paso("payment", **base)


@pytest.mark.asyncio
async def test_quien_vuelve_otro_dia_empieza_de_cero(mundo):
    await save_state(30, _vendido(checkout_updated_at=time.time() - 40 * 86400,
                                  last_turn_at=time.time() - 40 * 86400))
    await _turno(30, "hola")
    state = await load_state(30)
    assert state.checkout_step == "idle"
    assert state.chosen_product_id is None
    assert state.handoff_reason == ""
    assert state.shown_product_ids == []
    assert not mundo["ventas"]


@pytest.mark.asyncio
async def test_el_mismo_dia_no_se_vuelve_a_anunciar_la_venta(mundo):
    await save_state(31, _vendido(checkout_updated_at=time.time(), last_turn_at=time.time()))
    await _turno(31, "quiero este")
    assert mundo["ventas"] == []
    assert mundo["pedidos"] == []


@pytest.mark.asyncio
async def test_el_mismo_dia_otro_producto_abre_otro_cierre(mundo):
    await save_state(32, _vendido(checkout_updated_at=time.time(), last_turn_at=time.time()))
    await _turno(32, "quiero el peluche panda")
    state = await load_state(32)
    assert state.chosen_product_id == 102
    assert state.checkout_step == "district"
    assert state.district == ""
    assert mundo["ventas"] == []


@pytest.mark.asyncio
async def test_lo_ya_mostrado_no_se_oculta_para_siempre(mundo):
    await save_state(33, ConversationState(shown_product_ids=[1, 2], last_turn_at=time.time() - 3 * 86400))
    await _turno(33, "hola")
    assert (await load_state(33)).shown_product_ids == []


@pytest.mark.asyncio
async def test_en_la_misma_jornada_no_se_repiten_productos(mundo):
    await save_state(34, ConversationState(shown_product_ids=[1, 2], last_turn_at=time.time()))
    await _turno(34, "hola")
    assert (await load_state(34)).shown_product_ids == [1, 2]


# ── Desayunos: un día de anticipación y solo por la mañana ────────────

def _desayuno(step, **kw):
    return _en_paso(step, chosen_product_name="1era Sorpresa del día", chosen_is_breakfast=True, **kw)


def test_un_desayuno_no_se_agenda_para_hoy():
    state, reply, _ = advance_checkout(_desayuno("date"), "hoy", today=HOY)
    assert state.date == ""
    assert state.checkout_step == "date"
    assert "anticipación" in reply


def test_un_desayuno_para_manana_si():
    state, reply, _ = advance_checkout(_desayuno("date"), "mañana", today=HOY)
    assert state.date == "2026-11-05"
    assert state.checkout_step == "schedule"
    assert "07:00 AM a 09:00 AM" in reply
    assert "09:00 AM a 11:00 AM" in reply
    assert "11:00 AM a 02:00 PM" not in reply
    assert "04:00 PM a 07:00 PM" not in reply


def test_el_viernes_un_desayuno_solo_tiene_de_9_a_11():
    assert [w.display for w in windows_for(VIERNES, breakfast=True)] == ["09:00 AM a 11:00 AM"]


def test_un_desayuno_no_acepta_una_franja_de_la_tarde():
    state = _desayuno("schedule", date="2026-11-05")
    state, reply, _ = advance_checkout(state, "de 4 a 7", today=HOY)
    assert state.time_slot == ""
    assert state.checkout_step == "schedule"
    assert "04:00 PM" not in reply
    state, _, _ = advance_checkout(state, "2", today=HOY)
    assert state.time_slot == "09:00 AM a 11:00 AM"


def test_otro_producto_si_se_puede_para_hoy():
    state, _, _ = advance_checkout(_en_paso("date"), "hoy", today=HOY)
    assert state.date == HOY.isoformat()
    assert state.checkout_step == "schedule"


def test_el_nombre_sirve_de_respaldo_si_no_hay_datos_de_la_api():
    state, _, _ = advance_checkout(
        _en_paso("date", chosen_product_name="Desayuno Clásico"), "hoy", today=HOY
    )
    assert state.date == ""


def test_la_categoria_manda_sobre_el_nombre():
    state, _, _ = advance_checkout(
        _en_paso("date", chosen_product_name="Desayuno de flores", chosen_is_breakfast=False),
        "hoy",
        today=HOY,
    )
    assert state.date == HOY.isoformat()


@pytest.mark.asyncio
async def test_la_categoria_de_la_api_identifica_el_desayuno(mundo):
    mundo["cat"] = "Desayunos"
    await save_state(40, _en_paso("date", chosen_product_name="1era Sorpresa del día"))
    await _turno(40, "hoy")
    state = await load_state(40)
    assert state.chosen_is_breakfast is True
    assert state.date == ""


# ── Las franjas de hoy que ya pasaron no se ofrecen ───────────────────

def _a_las(hora, minuto=0):
    return datetime(2026, 11, 4, hora, minuto, tzinfo=LIMA)


def test_hoy_solo_se_ofrecen_las_franjas_que_aun_no_empiezan():
    opciones = schedule_options_for(HOY.isoformat(), now=_a_las(10, 30))
    assert "07:00 AM" not in opciones
    assert "09:00 AM a 11:00 AM" not in opciones
    assert opciones.startswith("1. Mediodía")


def test_otro_dia_se_ofrecen_todas():
    assert len(windows_for("2026-11-05", now=_a_las(18))) == 5


def test_el_numero_se_resuelve_contra_la_lista_que_vio():
    assert parse_schedule("1", HOY.isoformat(), now=_a_las(10, 30)) == "11:00 AM a 02:00 PM"


def test_a_ultima_hora_hoy_ya_no_es_una_fecha_valida():
    state, reply, _ = advance_checkout(_en_paso("date"), "hoy", today=HOY, now=_a_las(18))
    assert state.date == ""
    assert state.checkout_step == "date"
    assert "no quedan" in reply


def test_una_franja_que_ya_paso_no_se_acepta():
    state = _en_paso("schedule", date=HOY.isoformat())
    state, _, _ = advance_checkout(state, "de 7 a 9", today=HOY, now=_a_las(10, 30))
    assert state.time_slot == ""


def test_si_el_dia_se_quedo_sin_franjas_vuelve_a_pedir_la_fecha():
    state = _en_paso("schedule", date=HOY.isoformat())
    state, reply, _ = advance_checkout(state, "3", today=HOY, now=_a_las(18))
    assert state.checkout_step == "date"
    assert state.date == ""
    assert "no quedan" in reply
