"""Un número contestando a un menú NUESTRO se resuelve en código, sea cual sea
la etiqueta que le ponga el router.

El incidente (Lichi, 29-07): "Podría enviarme los modelos que tiene" → el bot
ofreció las 7 categorías padre (bien) → el cliente respondió "4" (Peluches) y
"1" → y a partir de ahí el bot le sirvió **nueve submenús inventados**: cinco
tipos de peluche, tres combos, cuatro tamaños, tres tipos de rosas (dos veces) y
tres rangos de precio. Peluches no tiene ni una subcategoría en la API. Luego
listó "1) Producto #1221  2) Producto #290", ids internos sin nombre ni precio.
El cliente pidió "los modelos" CUATRO veces (15:57, 16:05, 16:12, 16:15); el
primer producto real con foto salió a las 16:16. Al minuto escribió "No tiene un
catálogo??" y el bot le devolvió el mismo menú de 7 categorías del principio.

`test_menu_taxonomia.py` ya cubría el caso de Yudith y su maquinaria seguía
correcta — sencillamente no se ejecutó ni una vez. Dos agujeros encadenados:

1. **El router no miraba `recent_options`.** Un "4" pelado no casaba con ninguna
   regla, salía `small_talk` con confianza 0.3 y decidía el clasificador LLM.
2. **La disciplina del menú colgaba del intent.** `_answer_menu` solo corría con
   `intent == "catalog_search"` y `_own_the_menu` solo con
   `output_policy == "catalog"`, así que en `concierge` —sin tools de catálogo—
   el modelo escribía lo que quisiera y nadie lo reescribía.

Aquí se comprueban los dos, y que arreglarlos no rompe los turnos donde un
número significa otra cosa (una franja horaria del cierre, el segundo producto
de un listado).
"""
import json
import pathlib

import pytest

from app.harness import master
from app.harness.contracts import Turn
from app.harness.router import classify_rules
from app.harness.state import ConversationState
from app.harness.taxonomy import (
    as_state,
    looks_like_option_pick,
    parse_navegacion,
)
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
        raise AssertionError(f"tool inesperada: {nombre}")

    monkeypatch.setattr(master, "execute_tool", fake)


def _turn(text: str) -> Turn:
    return Turn(text=text, has_media=False, messages=[])


# ── Qué cuenta como "esto va dirigido al menú" ────────────────────────

@pytest.mark.parametrize(
    "texto",
    ["4", "1", "la 3", "el 2", "opción 5", "quiero la 4", "el segundo", "7 ", "4\n1"],
)
def test_una_eleccion_de_menu_se_reconoce(texto):
    assert looks_like_option_pick(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "",
        "San Isidro",
        "gracias",
        "Podría enviarme los modelos que tiene",
        "No tiene un catálogo??",
        "51954713002",      # un teléfono no es una opción de un menú de siete
        "quiero 2 desayunos",
        "125.80",
    ],
)
def test_lo_que_no_es_una_eleccion_de_menu_no_lo_parece(texto):
    assert not looks_like_option_pick(texto)


# ── 1. El router ve el menú vivo ──────────────────────────────────────

def test_un_numero_con_menu_vivo_va_a_catalogo(taxonomia):
    """El agujero exacto: sin esto salía `small_talk` 0.3 → concierge."""
    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)

    resultado = classify_rules("4", state)

    assert resultado.intent == "catalog_search"
    assert resultado.source == "rules"
    # Por encima de CONFIDENCE_FLOOR: no se le pregunta al clasificador LLM algo
    # que ya sabemos, porque el menú lo escribimos nosotros.
    assert resultado.confidence >= 0.9


def test_dos_numeros_seguidos_tambien(taxonomia):
    """El buffer une "4" (16:01) y "1" (16:02) en un turno. Cuál gana lo decide
    `resolve_option`; lo que no puede pasar es que el turno se vaya a concierge."""
    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)
    assert classify_rules("4\n1", state).intent == "catalog_search"


def test_sin_menu_vivo_un_numero_no_dispara_la_regla():
    """Sin `recent_options` no hay nada que resolver: que siga el camino de antes."""
    resultado = classify_rules("4", ConversationState())
    assert resultado.intent != "catalog_search" or resultado.source != "rules"


def test_tras_mostrar_productos_el_numero_ya_no_es_del_menu(taxonomia):
    """`_reduce` vacía `recent_options` al mostrar productos: a partir de ahí un
    "2" es el segundo PRODUCTO, no la segunda categoría."""
    state = ConversationState(
        recent_options=[], shown_product_ids=[290, 1221], intent_last="catalog_search"
    )
    resultado = classify_rules("2", state)
    assert not (resultado.intent == "catalog_search" and resultado.confidence >= 0.9)


def test_durante_el_cierre_un_numero_sigue_siendo_del_cierre(taxonomia):
    """Regresión: "2" en el paso de horario es una franja, no una categoría."""
    state = ConversationState(
        recent_options=as_state(taxonomia), menu_depth=1, checkout_step="schedule"
    )
    assert classify_rules("2", state).intent == "checkout"


# ── 2. La disciplina del menú no depende del intent ───────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["small_talk", "greet", "policy_faq", "catalog_search"])
async def test_el_menu_se_resuelve_venga_el_intent_que_venga(
    intent, taxonomia, sin_red
):
    """El corazón del arreglo.

    Antes esto solo funcionaba con `catalog_search`. Con cualquier otra etiqueta
    el turno se lo quedaba un especialista LLM y ahí nacieron los nueve submenús.
    """
    state = ConversationState(
        recent_options=as_state(taxonomia), menu_depth=1, presented=True
    )

    result = await master._handle(intent, _turn("4"), state)

    assert result.artifacts, f"con intent={intent} el 4 tiene que dar productos"
    for p in result.artifacts:
        # Productos REALES: nunca más un "Producto #1221" sin nombre ni precio.
        assert p.id_producto and p.nombre
        assert p.precio_sol is not None
    assert result.state_patch["recent_options"] == []


@pytest.mark.asyncio
async def test_peluches_no_abre_un_submenu_porque_no_tiene_hijas(taxonomia, sin_red):
    """El "4" del incidente. La API no tiene subcategorías de Peluches: los cinco
    "tipos de peluche" que ofreció el bot se los inventó enteros."""
    peluches = next(o for o in taxonomia if o["nombre"] == "Peluches")
    assert peluches["hijos"] == [], "si esto cambia, el resto del test miente"

    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)
    result = await master._handle("small_talk", _turn("4"), state)

    assert result.artifacts
    for inventado in ("Osito clásico", "Personalizable", "Con combo", "Para bebé"):
        assert inventado not in (result.user_facing or "")


@pytest.mark.asyncio
async def test_una_pregunta_de_verdad_sigue_yendo_al_especialista(taxonomia, sin_red):
    """`_answer_menu` devuelve `None` cuando no es lo suyo: "San Isidro" con un
    menú vivo no puede acabar mostrando peluches."""
    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)
    assert await master._answer_menu(_turn("San Isidro"), state) is None
    assert await master._answer_menu(_turn("gracias"), state) is None
