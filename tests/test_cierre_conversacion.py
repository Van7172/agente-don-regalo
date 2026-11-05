"""El cierre es una conversación: se puede preguntar, corregir y volver.

El formulario solo sabía avanzar. "¿Qué contiene?" recibía "No logré leer una fecha",
ofrecía corregir el distrito sin poder hacerlo y al tercer intento derivaba. Y "el
segundo", la forma más común de elegir, dependía de un clasificador que no veía el
listado.
"""
import json
import time
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import settings
from app.harness import coverage as cov
from app.harness import master as master_mod
from app.harness import router as router_mod
from app.harness.checkout import advance_checkout
from app.harness.contracts import AgentResult, EscalateReason
from app.harness.orders import lima_today
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state

LIMA = ZoneInfo("America/Lima")
HOY = date(2026, 11, 4)  # miércoles
_cobertura_real = master_mod._checkout_coverage
_datos_reales = master_mod._load_product_facts

LISTADO = [
    {"id_producto": 101, "nombre": "Ramo de Rosas"},
    {"id_producto": 102, "nombre": "Peluche Panda"},
]


def _en_paso(step: str, **kw) -> ConversationState:
    base = dict(
        checkout_step=step,
        chosen_product_id=101,
        chosen_product_name="Ramo de Rosas",
        recent_products=list(LISTADO),
        shown_product_ids=[101, 102],
    )
    base.update(kw)
    return ConversationState(**base)


def _resumen(**kw) -> ConversationState:
    # Fecha relativa: algunos de estos turnos pasan por el reloj real.
    base = dict(
        district="Santiago De Surco", id_distrito=5, shipping_fee_sol=15.0,
        chosen_product_price_sol=95.0,
        date=(lima_today() + timedelta(days=30)).isoformat(),
        time_slot="09:00 AM a 11:00 AM",
        dedicatoria="Sin dedicatoria", nombre_destinatario="Ana", apellidos_destinatario="Ruiz",
        telefono_destinatario="999888777", direccion="Jr. Ica 200", tipo=0,
        nombre_cliente="Pedro", apellidos_cliente="Díaz", email_cliente="p@x.pe",
    )
    base.update(kw)
    return _en_paso("summary", **base)


@pytest.fixture
def mundo(monkeypatch):
    ev = {"especialista": [], "handoff": [], "ventas": [], "guard": [], "disponible": True}

    async def distritos(_client, _args):
        return {"data": [
            {"id_distrito": 5, "nombre": "Santiago De Surco", "tarifa_sol": 15.0, "tarifa_usd": 4.41},
            {"id_distrito": 7, "nombre": "Miraflores", "tarifa_sol": 12.0, "tarifa_usd": 3.53},
        ]}

    async def herramienta(name, args=None):
        if name == "detalle_producto":
            return json.dumps({"data": {
                "id_producto": args["id_producto"], "nombre": "Ramo de Rosas", "precio_sol": 95.0,
                "precio_usd": 28.0, "descripcion": "12 rosas rojas", "categoria": "Arreglos Florales",
                "categoria_slug": "arreglos-florales",
            }})
        return json.dumps({"data": []})

    async def handoff(**kw):
        ev["handoff"].append(kw.get("motivo"))
        return EscalateReason(motivo=kw.get("motivo") or "")

    async def anuncia(_cid, state):
        ev["ventas"].append(state.chosen_product_name)

    async def disponible(_pid):
        return ev["disponible"]

    async def especialista(messages, **kw):
        ev["especialista"].append(messages[0]["content"])
        return AgentResult(user_facing="(respuesta del especialista)")

    real_guard = master_mod.guard_reply

    def guard(*a, **kw):
        ev["guard"].append(kw)
        return real_guard(*a, **kw)

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", distritos)
    monkeypatch.setattr(master_mod, "_checkout_coverage", _cobertura_real)
    monkeypatch.setattr(master_mod, "_load_product_facts", _datos_reales)
    monkeypatch.setattr(master_mod, "execute_tool", herramienta)
    monkeypatch.setattr(master_mod, "perform_handoff", handoff)
    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "is_available", disponible)
    monkeypatch.setattr(master_mod, "run_specialist", especialista)
    monkeypatch.setattr(master_mod, "guard_reply", guard)
    return ev


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999000111", conversation_id=cid
    )


# ── Preguntar sin perder el paso ──────────────────────────────────────

@pytest.mark.parametrize(
    "step,texto,intent",
    [
        ("date", "¿qué contiene?", "product_detail"),
        ("schedule", "cómo es el ramo?", "product_detail"),
        ("district", "qué contiene?", "product_detail"),
        ("date", "hola, ¿sigue disponible lo que vi?", "product_detail"),
        ("date", "puedo ver otras opciones?", "catalog_search"),
        ("recipient", "quiero ver otro regalo", "catalog_search"),
        ("date", "aceptan tarjeta?", "policy_faq"),
        ("date", "¿entregan los domingos?", "policy_faq"),
        ("address", "¿a qué hora llega?", "policy_faq"),
        ("summary", "¿hasta qué hora entregan?", "policy_faq"),
        ("date", "hasta que hora entregan", "policy_faq"),
    ],
)
def test_una_duda_sale_del_formulario(step, texto, intent):
    assert classify_rules(texto, _en_paso(step)).intent == intent


@pytest.mark.parametrize(
    "step,texto",
    [
        ("date", "¿puede ser el viernes?"),
        ("schedule", "¿puede ser de 9 a 11?"),
        ("district", "¿llegan a Surco?"),
        ("recipient", "¿Ana Ruiz 999888777?"),
        ("address", "Jr. Ica 200, ¿les sirve?"),
        ("contact", "¿pedro@gmail.com está bien?"),
        ("card_text", "¿Quieres ser mi novia?"),
        ("card_text", "Aceptas tarjeta de amor?"),
        ("address", "Como referencia, frente al parque"),
    ],
)
def test_lo_que_contesta_el_paso_sigue_en_el_formulario(step, texto):
    assert classify_rules(texto, _en_paso(step)).intent == "checkout"


@pytest.mark.asyncio
async def test_contesta_el_contenido_y_retoma_la_pregunta_pendiente(mundo):
    await save_state(1, _en_paso("date", product_facts_for=101))
    reply = await _turno(1, "¿qué contiene?")
    state = await load_state(1)
    assert "12 rosas rojas" in mundo["especialista"][0]
    assert "(respuesta del especialista)" in reply
    assert reply.rstrip().endswith("¿Para qué fecha lo necesitas? 📅")
    assert state.checkout_step == "date"
    assert state.step_retries == 0


@pytest.mark.asyncio
async def test_contesta_el_pago_y_retoma_la_pregunta_pendiente(mundo):
    await save_state(2, _en_paso("date", product_facts_for=101))
    reply = await _turno(2, "aceptan yape?")
    assert "metodos-pago-qr" in reply
    assert reply.rstrip().endswith("¿Para qué fecha lo necesitas? 📅")


@pytest.mark.asyncio
async def test_pedir_otras_opciones_suelta_el_producto_y_conserva_lo_demas(mundo):
    await save_state(3, _en_paso("date", district="Miraflores", id_distrito=7, shipping_fee_sol=12.0))
    reply = await _turno(3, "puedo ver otras opciones?")
    state = await load_state(3)
    assert state.checkout_step == "idle"
    assert state.chosen_product_id is None
    assert state.district == "Miraflores"
    assert state.shipping_fee_sol == 12.0
    assert "fecha" not in reply


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", ["cuánto es en total?", "cuánto es el delivery?", "cuánto sale?"])
async def test_preguntar_el_precio_en_el_cierre_se_contesta_con_datos(mundo, texto):
    await save_state(4, _en_paso("date", district="Santiago De Surco", shipping_fee_sol=15.0))
    reply = await _turno(4, texto)
    state = await load_state(4)
    assert "S/95.00" in reply
    assert "S/15.00" in reply
    assert "S/110.00" in reply
    assert reply.rstrip().endswith("¿Para qué fecha lo necesitas? 📅")
    assert state.step_retries == 0
    assert not mundo["especialista"]


@pytest.mark.asyncio
async def test_sin_distrito_el_precio_avisa_que_el_envio_depende_de_el(mundo):
    await save_state(5, _en_paso("district"))
    reply = await _turno(5, "cuánto es?")
    assert "S/95.00" in reply
    assert "distrito" in reply
    assert "S/110" not in reply


@pytest.mark.asyncio
async def test_los_horarios_del_cierre_no_cuentan_como_menu_inventado(mundo):
    await save_state(6, _en_paso("date", product_facts_for=101))
    await _turno(6, "mañana")
    assert mundo["guard"][-1]["menu_owned"] is True


# ── Corregir cualquier dato desde el resumen ──────────────────────────

@pytest.mark.parametrize(
    "texto,paso",
    [
        ("la dirección está mal", "address"),
        ("quiero cambiar el distrito", "district"),
        ("cambia la fecha", "date"),
        ("el horario no", "schedule"),
        ("cambia la dedicatoria", "card"),
        ("cambia el nombre", "recipient"),
        ("el teléfono está mal", "recipient"),
        ("mi correo está mal", "contact"),
    ],
)
def test_el_resumen_lleva_al_dato_que_se_quiere_corregir(texto, paso):
    state, _, meta = advance_checkout(_resumen(), texto, today=HOY)
    assert state.checkout_step == paso
    assert state.checkout_return_step == "summary"
    assert not meta.get("create_order")


def test_corregir_la_direccion_vuelve_al_resumen_con_el_dato_nuevo():
    state, _, _ = advance_checkout(_resumen(), "la dirección está mal", today=HOY)
    state, reply, _ = advance_checkout(state, "Av. Lima 500", today=HOY)
    assert state.checkout_step == "summary"
    assert state.direccion == "Av. Lima 500"
    assert "Av. Lima 500" in reply
    assert "Total: S/110.00" in reply
    assert state.nombre_cliente == "Pedro", "no se vuelven a pedir los demás datos"


def test_el_dato_nuevo_puede_venir_en_el_mismo_mensaje():
    state, reply, _ = advance_checkout(_resumen(), "cambia la dirección a Av. Lima 500", today=HOY)
    assert state.checkout_step == "summary"
    assert state.direccion == "Av. Lima 500"
    assert "Av. Lima 500" in reply


def test_una_fecha_nueva_conserva_el_horario_si_sigue_disponible():
    state, reply, _ = advance_checkout(
        _resumen(date="2026-11-11"), "cambia la fecha", today=HOY
    )
    state, reply, _ = advance_checkout(state, "12/11", today=HOY)
    assert state.checkout_step == "summary"
    assert state.date == "2026-11-12"
    assert state.time_slot == "09:00 AM a 11:00 AM"


def test_una_fecha_nueva_pide_horario_si_el_anterior_no_sale_ese_dia():
    # El 13/11/2026 es viernes: no hay franja de 7 a 9.
    state = _resumen(time_slot="07:00 AM a 09:00 AM")
    state, _, _ = advance_checkout(state, "cambia la fecha", today=HOY)
    state, _, _ = advance_checkout(state, "13/11", today=HOY)
    assert state.checkout_step == "schedule"
    state, _, _ = advance_checkout(state, "1", today=HOY)
    assert state.checkout_step == "summary"
    assert state.time_slot == "09:00 AM a 11:00 AM"


def test_sin_decir_que_corregir_lo_pregunta_nombrando_todo():
    state, reply, meta = advance_checkout(_resumen(), "no", today=HOY)
    assert state.checkout_step == "summary"
    assert "dirección" in reply
    assert not meta.get("handoff")


def test_tras_preguntar_que_corregir_aun_se_puede_confirmar():
    state, _, _ = advance_checkout(_resumen(), "espera", today=HOY)
    state, _, meta = advance_checkout(state, "ok, sí", today=HOY)
    assert meta.get("create_order") is True


def test_cambiar_el_producto_vuelve_al_catalogo():
    state, reply, _ = advance_checkout(_resumen(), "quiero cambiar el producto", today=HOY)
    assert state.checkout_step == "idle"
    assert state.chosen_product_id is None
    assert state.district == "Santiago De Surco"


@pytest.mark.asyncio
async def test_corregir_el_distrito_recalcula_tarifa_y_total(mundo):
    await save_state(10, _resumen())
    await _turno(10, "cambia el distrito")
    reply = await _turno(10, "Miraflores")
    state = await load_state(10)
    assert state.checkout_step == "summary"
    assert state.shipping_fee_sol == 12.0
    assert state.id_distrito == 7
    assert "Total: S/107.00" in reply


@pytest.mark.asyncio
async def test_el_distrito_nuevo_puede_venir_en_el_mismo_mensaje(mundo):
    await save_state(11, _resumen())
    reply = await _turno(11, "el distrito es Miraflores")
    state = await load_state(11)
    assert state.checkout_step == "summary"
    assert state.id_distrito == 7
    assert "Total: S/107.00" in reply


@pytest.mark.asyncio
async def test_una_correccion_no_anuncia_ninguna_venta(mundo):
    await save_state(12, _resumen())
    await _turno(12, "no, es San Isidro")
    assert mundo["ventas"] == []


# ── Elegir de la lista ────────────────────────────────────────────────

@pytest.mark.parametrize("texto", ["el segundo", "2", "la 2", "opción 2", "el 2 por favor"])
def test_una_posicion_se_resuelve_sin_el_clasificador(texto):
    c = classify_rules(texto, ConversationState(recent_products=list(LISTADO), shown_product_ids=[101, 102]))
    assert (c.intent, c.source) == ("product_detail", "rules")


def test_con_un_menu_vivo_el_numero_sigue_siendo_del_menu():
    state = ConversationState(
        recent_products=list(LISTADO),
        recent_options=[{"nombre": "Desayunos", "slug": "desayunos"}],
    )
    assert classify_rules("2", state).intent == "catalog_search"


def test_sin_listado_un_numero_suelto_no_elige_nada():
    assert classify_rules("2", ConversationState()).source == "fallback"


@pytest.mark.asyncio
async def test_elegir_de_la_lista_fija_el_producto_y_propone_llevarlo(mundo):
    await save_state(20, ConversationState(recent_products=list(LISTADO), shown_product_ids=[101, 102]))
    reply = await _turno(20, "el segundo")
    state = await load_state(20)
    assert state.chosen_product_id == 102
    assert "¿Te lo llevas?" in reply


@pytest.mark.parametrize("texto", ["sí", "dale", "ok", "si quiero", "claro"])
def test_un_si_tras_ver_el_detalle_abre_el_cierre(texto):
    state = ConversationState(
        intent_last="product_detail", chosen_product_id=101, chosen_product_name="Ramo de Rosas",
        recent_products=list(LISTADO),
    )
    assert classify_rules(texto, state).intent == "checkout"


def test_un_si_tras_ver_el_detalle_sin_producto_sigue_siendo_ver_mas():
    state = ConversationState(intent_last="product_detail", recent_products=list(LISTADO))
    assert classify_rules("sí", state).intent == "catalog_search"


# ── Volver otro día a un pedido a medias ──────────────────────────────

AYER = time.time() - 2 * 86400


@pytest.mark.asyncio
async def test_reanudar_comprueba_que_el_producto_siga_disponible(mundo):
    mundo["disponible"] = False
    await save_state(30, _en_paso("date", checkout_updated_at=AYER, last_turn_at=AYER))
    await _turno(30, "hola")
    reply = await _turno(30, "sí")
    state = await load_state(30)
    assert "ya no está disponible" in reply
    assert state.chosen_product_id is None
    assert state.checkout_step == "idle"


@pytest.mark.asyncio
async def test_reanudar_vuelve_a_pedir_una_fecha_que_ya_paso(mundo):
    await save_state(
        31,
        _en_paso("address", date="2020-01-15", time_slot="09:00 AM a 11:00 AM", district="Miraflores",
                 checkout_updated_at=AYER, last_turn_at=AYER),
    )
    await _turno(31, "hola")
    reply = await _turno(31, "sí")
    state = await load_state(31)
    assert state.checkout_step == "date"
    assert state.date == ""
    assert "fecha" in reply
    await _turno(31, (lima_today() + timedelta(days=10)).strftime("%d/%m"))
    await _turno(31, "2")
    state = await load_state(31)
    assert state.checkout_step == "address", "retoma donde estaba, sin repetir la tarjeta"


@pytest.mark.asyncio
async def test_reanudar_con_todo_vigente_sigue_donde_estaba(mundo):
    await save_state(
        32,
        _en_paso("address", date="2099-01-15", time_slot="09:00 AM a 11:00 AM", district="Miraflores",
                 checkout_updated_at=AYER, last_turn_at=AYER),
    )
    await _turno(32, "hola")
    reply = await _turno(32, "sí")
    assert (await load_state(32)).checkout_step == "address"
    assert "irección" in reply


# ── El clasificador LLM ve dónde está la conversación ─────────────────

@pytest.mark.asyncio
async def test_el_clasificador_recibe_el_contexto_del_turno(monkeypatch):
    enviado = {}

    class Respuesta:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"intent": "checkout", "confidence": 0.9}'}}]}

    async def post(self, url, *, headers=None, json=None):
        enviado.update(json)
        return Respuesta()

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(settings, "openai_api_key", "x", raising=False)
    state = ConversationState(recent_products=list(LISTADO), shown_product_ids=[101, 102])

    await router_mod.classify("me interesa eso", state)

    sistema = " ".join(m["content"] for m in enviado["messages"] if m["role"] == "system")
    assert "2 productos" in sistema
    assert enviado["messages"][-1] == {"role": "user", "content": "me interesa eso"}
