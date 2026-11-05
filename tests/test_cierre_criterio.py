"""Criterio del cierre: entender lo que el cliente dice, no solo que lo dijo.

Nació de una auditoría (04/11/2026) que pasó frases cotidianas por el código real.
La suite estaba en verde y aun así una corrección ("no, es San Isidro") contaba como
confirmar la compra, una dedicatoria ("ya no puedo vivir sin ti") como abandonarla y
un "regalo personalizado" cedía el chat porque contiene la palabra "persona".
"""
from datetime import date

import pytest

from app.guardrails import handoff_policy
from app.harness import master as master_mod
from app.harness.checkout import (
    advance_checkout,
    parse_recipient,
    resolve_chosen_product,
)
from app.harness.orders import normalize_fecha
from app.harness.router import classify_rules
from app.harness.state import ConversationState
from app.payments import asks_for_payment_methods, payment_methods_reply

HOY = date(2026, 11, 4)  # miércoles

PRODUCTOS = [
    {"id_producto": 101, "nombre": "Desayuno Clasico"},
    {"id_producto": 102, "nombre": "Desayuno Romantico"},
    {"id_producto": 103, "nombre": "Ramo de Rosas Rojas"},
]


def _en_paso(step: str, **kw) -> ConversationState:
    base = dict(
        checkout_step=step,
        chosen_product_id=101,
        chosen_product_name="Desayuno Clasico",
        district="Santiago De Surco",
        date="2026-11-06",
    )
    base.update(kw)
    return ConversationState(**base)


def _con_listado(**kw) -> ConversationState:
    return ConversationState(
        recent_products=list(PRODUCTOS), shown_product_ids=[101, 102, 103], **kw
    )


# ── Confirmar el resumen es decir que sí, no contener "si" ────────────

@pytest.mark.parametrize(
    "texto",
    ["si", "Sí", "sí, confirmo", "todo ok", "correcto", "perfecto", "dale", "ok",
     "si todo correcto gracias", "está bien", "así es", "de acuerdo", "listo", "👍"],
)
def test_una_afirmacion_confirma_el_resumen(texto):
    state, _, meta = advance_checkout(_en_paso("summary"), texto, today=HOY)
    assert meta.get("create_order") is True, texto
    assert state.checkout_step == "payment"


@pytest.mark.parametrize(
    "texto",
    [
        "no",
        "no, es San Isidro",
        "quisiera cambiar la fecha",
        "el distrito es incorrecto",
        "así no es",
        "casi todo bien pero cambia la dirección",
        "sí, pero cambia la dirección",
        "ok pero falta la tarjeta",
        "espera, una consulta",
    ],
)
def test_una_correccion_no_confirma_el_resumen(texto):
    state, _, meta = advance_checkout(_en_paso("summary"), texto, today=HOY)
    assert not meta.get("create_order"), texto
    assert not meta.get("escalate"), texto
    assert state.checkout_step != "payment"


# ── Los textos libres no son señales de abandono ──────────────────────

@pytest.mark.parametrize(
    "texto",
    [
        "Ya no puedo vivir sin ti",
        "No quiero perderte nunca, te amo",
        "Te dije que siempre lo mismo: te amo",
    ],
)
def test_una_dedicatoria_se_guarda_tal_cual(texto):
    state, _, meta = advance_checkout(_en_paso("card_text"), texto, today=HOY)
    assert not meta.get("handoff")
    assert state.dedicatoria == texto
    assert state.checkout_step == "recipient"


@pytest.mark.parametrize(
    "texto",
    [
        "Av. Arequipa 123, déjalo en portería",
        "Jr. Lima 450, déjala con el vigilante",
        "Ya no vivo en Surco, es Jr. Ica 200",
    ],
)
def test_una_direccion_con_indicaciones_no_es_abandono(texto):
    state, _, meta = advance_checkout(_en_paso("address"), texto, today=HOY)
    assert not meta.get("handoff")
    assert state.direccion == texto


@pytest.mark.parametrize("step", ["card_text", "recipient", "address"])
@pytest.mark.parametrize(
    "texto", ["ya no", "olvídalo", "Ya no quiero el pedido", "ya no deseo el pedido gracias"]
)
def test_el_abandono_explicito_se_reconoce_tambien_en_texto_libre(step, texto):
    _, _, meta = advance_checkout(_en_paso(step), texto, today=HOY)
    assert meta.get("handoff") is True


# ── "Persona" no es pedir una persona ─────────────────────────────────

@pytest.mark.parametrize(
    "texto",
    [
        "quiero un regalo personalizado",
        "es para una persona muy especial",
        "tienen algo para personas mayores?",
    ],
)
def test_mencionar_a_una_persona_no_deriva(texto):
    assert classify_rules(texto, ConversationState()).intent != "escalate"


@pytest.mark.parametrize(
    "texto",
    ["quiero hablar con una persona", "pásame con una persona", "una persona por favor",
     "necesito una persona real", "quiero un asesor", "atención humana"],
)
def test_pedir_una_persona_si_deriva(texto):
    assert classify_rules(texto, ConversationState()).intent == "escalate"
    assert handoff_policy([{"role": "user", "content": texto}]).allow


@pytest.mark.parametrize(
    "texto", ["Eres la persona más importante de mi vida", "A la persona que más quiero"]
)
def test_una_dedicatoria_con_persona_sigue_en_el_cierre(texto):
    assert classify_rules(texto, _en_paso("card_text")).intent == "checkout"


# ── En Perú "cancelar" también es pagar ───────────────────────────────

@pytest.mark.parametrize(
    "texto", ["como cancelo con yape?", "¿dónde puedo cancelar?", "quiero cancelar con tarjeta"]
)
def test_cancelar_un_pago_no_deriva(texto):
    assert classify_rules(texto, _con_listado()).intent == "policy_faq"
    assert asks_for_payment_methods(texto)


@pytest.mark.parametrize(
    "texto", ["Cancelo el pedido gracias", "quiero cancelar mi pedido", "como cancelo mi pedido?"]
)
def test_cancelar_un_pedido_si_deriva(texto):
    assert classify_rules(texto, _con_listado()).intent == "escalate"


# ── ¿Aceptan tarjeta? se contesta ─────────────────────────────────────

def test_la_pregunta_por_tarjeta_recibe_respuesta_sobre_tarjeta():
    reply = payment_methods_reply("¿Aceptan tarjeta?")
    assert "tarjeta" in reply.casefold()
    assert "Visa" in reply
    assert "PayPal" in reply


def test_la_pregunta_por_yape_no_habla_de_tarjeta():
    assert "PayPal" not in payment_methods_reply("¿aceptan yape?")


# ── Fechas del año que viene ──────────────────────────────────────────

@pytest.mark.parametrize(
    "texto,esperada",
    [
        ("2 de enero", "2027-01-02"),
        ("05/01", "2027-01-05"),
        ("25/12", "2026-12-25"),
        ("20 de noviembre", "2026-11-20"),
    ],
)
def test_una_fecha_sin_anio_es_la_proxima(texto, esperada):
    assert normalize_fecha(texto, today=HOY) == esperada


def test_una_fecha_de_hace_poco_sigue_siendo_pasada():
    """Un error de tipeo no debe mandar el pedido a dentro de un año."""
    assert normalize_fecha("30/10", today=HOY) == "2026-10-30"


def test_enero_no_se_rechaza_como_fecha_pasada():
    state, _, _ = advance_checkout(_en_paso("date", date=""), "2 de enero", today=HOY)
    assert state.date == "2027-01-02"
    assert state.checkout_step == "schedule"


# ── El paso de la tarjeta ─────────────────────────────────────────────

@pytest.mark.parametrize("texto", ["si", "claro", "ok", "dale", "bueno", "por supuesto", "sí por favor"])
def test_un_si_coloquial_pide_el_texto_de_la_tarjeta(texto):
    state, _, _ = advance_checkout(_en_paso("card"), texto, today=HOY)
    assert state.checkout_step == "card_text", texto


@pytest.mark.parametrize(
    "texto", ["no", "no gracias", "sin tarjeta", "sin dedicatoria", "mejor no", "ninguna", "no quiero tarjeta"]
)
def test_un_no_sigue_sin_tarjeta(texto):
    state, _, _ = advance_checkout(_en_paso("card"), texto, today=HOY)
    assert state.checkout_step == "recipient", texto
    assert state.dedicatoria == "Sin dedicatoria"


def test_si_escribe_directamente_el_mensaje_ese_es_el_mensaje():
    state, _, _ = advance_checkout(_en_paso("card"), "Feliz cumple mamá", today=HOY)
    assert state.dedicatoria == "Feliz cumple mamá"
    assert state.checkout_step == "recipient"


def test_si_dicta_el_mensaje_con_que_diga_se_extrae():
    state, _, _ = advance_checkout(_en_paso("card"), "sí, que diga: te amo", today=HOY)
    assert state.dedicatoria == "te amo"
    assert state.checkout_step == "recipient"


def test_una_pregunta_en_la_tarjeta_no_se_toma_como_dedicatoria():
    state, _, _ = advance_checkout(_en_paso("card"), "cuánto cuesta la tarjeta?", today=HOY)
    assert state.checkout_step == "card"
    assert state.dedicatoria == ""


def test_un_gracias_en_la_tarjeta_no_decide_por_el_cliente():
    state, _, _ = advance_checkout(_en_paso("card"), "gracias", today=HOY)
    assert state.checkout_step == "card"


# ── Los apellidos no se tocan ─────────────────────────────────────────

@pytest.mark.parametrize(
    "texto,nombre,apellidos",
    [
        ("Ana de la Cruz 999888777", "Ana", "de la Cruz"),
        ("Maria del Carmen Salas 999888777", "Maria", "del Carmen Salas"),
        ("Se llama Ana de la Cruz, su teléfono es 999888777", "Ana", "de la Cruz"),
        ("Para mi mamá Rosa Díaz, cel 999888777", "mamá", "Rosa Díaz"),
    ],
)
def test_los_apellidos_compuestos_se_conservan(texto, nombre, apellidos):
    assert parse_recipient(texto) == (nombre, apellidos, "999888777")


# ── Una cantidad no es una posición ───────────────────────────────────

@pytest.mark.parametrize(
    "texto",
    [
        "quiero dos desayunos",
        "quiero uno para mi mamá y otro para mi suegra",
        "primero quiero saber el precio",
        "para 2 personas",
        "quiero el 2 de agosto",
    ],
)
def test_una_cantidad_no_elige_producto(texto):
    assert resolve_chosen_product(_con_listado(), texto, allow_implicit=False) is None


@pytest.mark.parametrize(
    "texto,pid",
    [("el segundo", 102), ("quiero el segundo", 102), ("la 3", 103), ("2", 102),
     ("opción 1", 101), ("me quedo con el primero", 101), ("el tercero por favor", 103)],
)
def test_una_posicion_si_elige_producto(texto, pid):
    assert resolve_chosen_product(_con_listado(), texto, allow_implicit=False)[0] == pid


@pytest.mark.parametrize(
    "texto", ["quiero ver más rosas", "quiero ver otros desayunos", "quiero más opciones de rosas"]
)
def test_querer_ver_mas_no_abre_el_cierre(texto):
    assert classify_rules(texto, _con_listado()).intent == "catalog_search"


def test_querer_un_producto_por_su_nombre_si_abre_el_cierre():
    assert classify_rules("quiero el ramo de rosas rojas", _con_listado()).intent == "checkout"


# ── La campaña de Flores Amarillas terminó ────────────────────────────

@pytest.mark.asyncio
async def test_flores_amarillas_se_busca_en_el_catalogo(monkeypatch):
    llamado = {}

    async def especialista(intent, turn, state, **_):
        from app.harness.contracts import AgentResult

        llamado["intent"] = intent
        return AgentResult(user_facing="Te muestro lo que tenemos 🌻")

    monkeypatch.setattr(master_mod, "_run_specialty", especialista)
    reply = await master_mod.run_master(
        [{"role": "user", "content": "quiero flores amarillas para el 20 de noviembre"}],
        wa_id="51999",
        conversation_id=992,
    )
    assert llamado["intent"] == "catalog_search"
    assert "ya no estamos tomando" not in reply
