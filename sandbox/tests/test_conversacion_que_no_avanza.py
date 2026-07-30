"""Tres frenos para la conversación que pregunta y nunca enseña nada.

Los tres salen del mismo incidente (Lichi, 29-07) y cubren lo que el arreglo del
router no alcanzaba:

- **Varios números en un turno.** El buffer une los mensajes seguidos, así que
  "4" (16:01) y "1" (16:02) llegan juntos. `resolve_option` exige un número y
  solo uno: devolvía `None` y el turno se lo quedaba el modelo. Ahora se
  pregunta cuál era **con las opciones que compuso el código**.
- **Turnos sin producto.** `menu_depth` solo cuenta los menús del código, así que
  se quedó en 1 mientras el modelo servía nueve suyos: el tope de dos menús
  nunca saltó. `turns_without_products` cuenta lo que le pasa al CLIENTE.
- **La red.** Un "Producto #1221" (id interno, sin nombre ni precio) no sale, y
  un menú que no compuso el código queda anotado en la traza.
"""
import json
import pathlib

import pytest

from app.guardrails import (
    SAFE_CATALOG_FALLBACK,
    check_reply,
    guard_reply,
    no_raw_product_ids,
    no_uncomposed_menu,
)
from app.harness import master
from app.harness.contracts import AgentResult, Product, Turn
from app.harness.state import ConversationState
from app.harness.taxonomy import as_state, parse_navegacion, resolve_options
from app.tools import adapters

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def taxonomia() -> list[dict]:
    return parse_navegacion(load("catalogo_navegacion"))


@pytest.fixture
def sin_red(monkeypatch):
    async def fake(nombre: str, args: dict) -> str:
        if nombre == "explorar_catalogo":
            return json.dumps(load("catalogo_navegacion"))
        if nombre == "catalogo_categoria":
            return json.dumps(
                adapters.products_payload(load("categoria_productos"), 3.4)
            )
        if nombre == "productos_destacados":
            return json.dumps(
                adapters.products_payload(load("productos_destacados"), 3.4)
            )
        raise AssertionError(f"tool inesperada: {nombre}")

    monkeypatch.setattr(master, "execute_tool", fake)


def _turn(text: str) -> Turn:
    return Turn(text=text, has_media=False, messages=[])


# ── 3. Varios números: se pregunta, no se adivina ni se suelta ────────

def test_dos_numeros_dan_dos_candidatos(taxonomia):
    elegidas = resolve_options("4\n1", taxonomia)
    assert [o["nombre"] for o in elegidas] == ["Peluches", "Arreglos Florales"]


def test_los_numeros_fuera_de_rango_se_ignoran(taxonomia):
    assert resolve_options("99 y 4", taxonomia) == [taxonomia[3]]
    assert resolve_options("sin números", taxonomia) == []


@pytest.mark.asyncio
async def test_dos_numeros_preguntan_con_las_opciones_del_codigo(taxonomia, sin_red):
    """Antes esto devolvía `None` y contestaba el modelo. Ahí nació todo."""
    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)

    result = await master._answer_menu(_turn("4\n1"), state)

    assert result is not None, "el turno no se le suelta al modelo"
    assert "Peluches" in result.user_facing
    assert "Arreglos Florales" in result.user_facing
    # Renumeradas: la respuesta siguiente se resuelve sola.
    assert result.state_patch["recent_options"] == [
        {"nombre": "Peluches", "slug": "peluches", "hijos": []},
        {
            "nombre": "Arreglos Florales",
            "slug": "arreglos-florales",
            "hijos": as_state(taxonomia)[0]["hijos"],
        },
    ]
    # No es un nivel más hondo: no se gasta un menú del presupuesto de dos.
    assert "menu_depth" not in result.state_patch


@pytest.mark.asyncio
async def test_tras_desambiguar_el_siguiente_numero_resuelve(taxonomia, sin_red):
    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)
    primero = await master._answer_menu(_turn("4\n1"), state)
    state.patch(primero.state_patch)

    segundo = await master._answer_menu(_turn("1"), state)

    assert segundo is not None
    assert segundo.artifacts, "Peluches no tiene hijas: productos directos"


# ── 4. El contador de turnos sin producto ─────────────────────────────

def _sin_productos() -> AgentResult:
    return AgentResult(user_facing="¿Qué tipo de peluche prefieres?")


def test_cada_turno_de_descubrimiento_sin_productos_suma():
    state = ConversationState()
    for esperado in (1, 2, 3):
        master._reduce(state, _sin_productos(), intent="catalog_search")
        assert state.turns_without_products == esperado


def test_mostrar_productos_pone_el_contador_a_cero():
    state = ConversationState(turns_without_products=2)
    result = AgentResult(artifacts=[Product(id_producto=290, nombre="Osita", precio_sol=125.8)])
    master._reduce(state, result, intent="catalog_search")
    assert state.turns_without_products == 0


@pytest.mark.parametrize("intent", ["coverage", "checkout", "policy_faq", "escalate"])
def test_preguntar_el_distrito_o_el_pago_no_cuenta(intent):
    """Ahí preguntar no es dar largas: soltarle un listado a quien pregunta por
    el Yape sería peor que el problema que esto arregla."""
    state = ConversationState()
    master._reduce(state, _sin_productos(), intent=intent)
    assert state.turns_without_products == 0


@pytest.mark.asyncio
async def test_al_tercer_turno_sin_productos_se_muestran(sin_red):
    """El freno. Con Lichi habría saltado en la tercera pregunta, no en la décima."""
    state = ConversationState(
        turns_without_products=master.MAX_TURNS_WITHOUT_PRODUCTS, presented=True
    )

    result = await master._handle("small_talk", _turn("mmm"), state)

    assert result.artifacts, "se deja de preguntar y se enseña algo"
    for p in result.artifacts:
        assert p.id_producto and p.nombre
        assert p.precio_sol is not None


@pytest.mark.asyncio
async def test_por_debajo_del_tope_sigue_mandando_el_especialista(sin_red, monkeypatch):
    llamado = {}

    async def fake_specialty(intent, turn, state, **ctx):
        llamado["si"] = True
        return AgentResult(user_facing="…")

    monkeypatch.setattr(master, "_run_specialty", fake_specialty)
    state = ConversationState(turns_without_products=1, presented=True)

    await master._handle("catalog_search", _turn("hola"), state)

    assert llamado.get("si"), "con un turno no se atropella al especialista"


# ── 5. La red: lo que no puede salir y lo que queda anotado ───────────

def test_un_id_interno_no_es_un_producto():
    reply = "Aquí 4 modelos:\n\n1) Producto #1221\n2) Producto #290\n3) Producto #1119"
    v = no_raw_product_ids(reply)
    assert v is not None and v.rule == "no_raw_product_ids"


@pytest.mark.parametrize(
    "reply",
    [
        "• 🎁 *Osita encantadora* — S/125.80 ($37.00)",
        "Tenemos 4 modelos disponibles para mañana.",
        "El pedido #1221 ya salió",   # un PEDIDO sí tiene número de cara al cliente
    ],
)
def test_lo_que_si_puede_salir(reply):
    assert no_raw_product_ids(reply) is None


def test_el_listado_con_ids_no_llega_al_cliente():
    """Con artifacts se reconstruye el listado real; sin ellos, un texto honesto."""
    reply = "1) Producto #1221\n2) Producto #290\n3) Producto #1119"

    con_productos = guard_reply(
        reply,
        artifacts=[Product(id_producto=290, nombre="Osita encantadora", precio_sol=125.8)],
    )
    assert con_productos.blocked
    assert "Producto #290" not in con_productos.reply
    assert "Osita encantadora" in con_productos.reply

    sin_productos = guard_reply(reply)
    assert sin_productos.blocked
    assert sin_productos.reply == SAFE_CATALOG_FALLBACK


def test_un_menu_que_no_compuso_el_codigo_queda_anotado():
    menu = "¿Qué tipo de peluche?\n1) Osito clásico\n2) Grande\n3) Personalizable"
    assert no_uncomposed_menu(menu, menu_owned=False) is not None
    assert no_uncomposed_menu(menu, menu_owned=True) is None


def test_el_menu_inventado_se_anota_pero_no_se_bloquea():
    """Observacional a propósito: "1) Yape 2) Transferencia 3) Tarjeta" también
    es una lista de tres, y descartarla rompería el mensaje que cierra la venta."""
    menu = "¿Qué tipo de peluche?\n1) Osito clásico\n2) Grande\n3) Personalizable"

    guarded = guard_reply(menu, menu_owned=False)

    assert not guarded.blocked, "no se descarta"
    assert guarded.reply == menu
    assert "no_uncomposed_menu" in {v.rule for v in guarded.violations}


def test_una_respuesta_normal_no_dispara_nada():
    limpia = check_reply(
        "¡Sí llegamos a San Isidro! El envío es S/13.60 ($4.00). ¿Qué regalo quieres enviar? 🎁",
        menu_owned=False,
    )
    assert limpia == []
