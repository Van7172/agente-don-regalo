"""El menú de categorías lo arma el código, contra la taxonomía REAL.

El incidente (Yudith, 22-07): "¡Hola! Quiero más información" → el bot ofreció
las 7 categorías padre (bien) → eligió "7" (Plantas) → le ofreció **siete tipos
de planta** cuando existen tres → eligió uno → le ofreció **seis terrarios
inventados**, con descripción y sin precio, ninguno del catálogo → eligió uno →
le ofreció un asesor para enseñarle fotos de un producto que no existe.

Cuatro menús, cero productos, veinte minutos y una derivación. El playbook ya
prohibía las tres cosas (copiar la taxonomía literalmente, no inventar un tercer
nivel, máximo dos menús); el modelo se las saltó igual. Aquí se comprueba que
ahora no depende de él.
"""
import json
import pathlib

import pytest

from app.harness import master
from app.harness.contracts import Turn
from app.harness.state import ConversationState
from app.harness.taxonomy import (
    as_state,
    parse_navegacion,
    render_menu,
    resolve_option,
)
from app.tools import adapters

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def taxonomia() -> list[dict]:
    return parse_navegacion(load("catalogo_navegacion"))


def _find(options: list[dict], nombre: str) -> dict:
    return next(o for o in options if o["nombre"] == nombre)


# ── La taxonomía, tal como la devuelve la API ─────────────────────────

def test_las_categorias_padre_son_las_siete_reales(taxonomia):
    assert [o["nombre"] for o in taxonomia] == [
        "Arreglos Florales",
        "Desayunos",
        "Arreglos Fúnebres",
        "Peluches",
        "Regalos para Bebé",
        "Cestas",
        "Plantas",
    ]


def test_plantas_tiene_tres_hijas_no_siete(taxonomia):
    """El bot ofreció 7 tipos de planta; la API tiene 3."""
    plantas = _find(taxonomia, "Plantas")
    assert [h["nombre"] for h in plantas["hijos"]] == [
        "Orquideas",
        "Suculentas",
        "Terrarios",
    ]
    # Los que se inventó, que no vuelvan por la puerta de atrás.
    nombres = {h["nombre"] for h in plantas["hijos"]}
    for inventado in (
        "Anturios y flores tropicales",
        "Plantas de interior",
        "Plantas grandes",
        "Terrarios y kokedamas",
        "Plantas para oficina",
    ):
        assert inventado not in nombres


def test_el_menu_se_numera_desde_uno_y_en_orden(taxonomia):
    texto = render_menu(taxonomia, "¿Qué categoría?")
    assert "1) Arreglos Florales" in texto
    assert "7) Plantas" in texto


# ── Resolver lo que responde el cliente ───────────────────────────────

def test_el_numero_del_cliente_se_resuelve_contra_nuestro_menu(taxonomia):
    """Yudith respondió “7”. Con la numeración en el código, eso es un slug."""
    assert resolve_option("7", taxonomia)["slug"] == "plantas"
    assert resolve_option("la 2", taxonomia)["nombre"] == "Desayunos"
    assert resolve_option("el tercero", taxonomia)["nombre"] == "Arreglos Fúnebres"
    assert resolve_option("plantas", taxonomia)["slug"] == "plantas"


def test_un_numero_fuera_del_menu_no_elige_nada(taxonomia):
    """Antes que meterlo en una categoría que no pidió, que pregunte el modelo."""
    assert resolve_option("99", taxonomia) is None
    assert resolve_option("no sé, ayúdame", taxonomia) is None
    assert resolve_option("", taxonomia) is None


# ── El turno completo, sin LLM ────────────────────────────────────────

@pytest.fixture
def sin_red(monkeypatch):
    """La API, servida desde los fixtures grabados.

    Pasa por el adapter igual que `execute_tool` de verdad: los productos le
    llegan al harness ya normalizados y en soles, no en la forma cruda.
    """
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


@pytest.mark.asyncio
async def test_elegir_una_categoria_con_hijas_ofrece_las_hijas_reales(
    taxonomia, sin_red
):
    state = ConversationState(recent_options=as_state(taxonomia), menu_depth=1)

    result = await master._answer_menu(_turn("7"), state)

    assert result is not None, "el “7” tiene que resolverse en código"
    assert "Orquideas" in result.user_facing
    assert "Terrarios" in result.user_facing
    assert "kokedamas" not in result.user_facing.lower(), "no existen"
    assert result.state_patch["menu_depth"] == 2


@pytest.mark.asyncio
async def test_al_segundo_menu_se_muestran_productos_no_un_tercero(
    taxonomia, sin_red
):
    """El corazón del bug: aquí es donde llegaron los seis terrarios inventados."""
    plantas = _find(taxonomia, "Plantas")
    state = ConversationState(recent_options=as_state(plantas["hijos"]), menu_depth=2)

    result = await master._answer_menu(_turn("3"), state)

    assert result is not None
    assert result.artifacts, "con el slug en la mano no se pregunta más"
    # Productos REALES: cada uno con su id y su precio, no prosa del modelo.
    for p in result.artifacts:
        assert p.id_producto and p.nombre
        assert p.precio_sol is not None
    # Y el menú deja de estar vigente: ahora los números son los del listado.
    assert result.state_patch["menu_depth"] == 0
    assert result.state_patch["recent_options"] == []


@pytest.mark.asyncio
async def test_sin_menu_previo_manda_el_especialista(sin_red):
    """`None` = “esto no me toca”: el LLM sigue atendiendo lo que no es un menú."""
    state = ConversationState()
    assert await master._answer_menu(_turn("quiero flores"), state) is None


@pytest.mark.asyncio
async def test_dentro_del_cierre_un_numero_es_un_horario_no_una_categoria(
    taxonomia, sin_red
):
    """El cierre tiene sus propios menús numerados; no se los pisamos."""
    state = ConversationState(
        recent_options=as_state(taxonomia), menu_depth=1, checkout_step="schedule"
    )
    assert await master._answer_menu(_turn("2"), state) is None


# ── Qué cuenta como menú y qué no ─────────────────────────────────────

def test_una_pregunta_de_dos_opciones_no_es_un_menu_de_categorias():
    """El bot preguntó “1) Ver fotos 2) Ver otras opciones”: eso no se suplanta."""
    assert not master._looks_like_menu(
        "¿Qué prefieres?\n1) Ver fotos y tamaños\n2) Ver otras opciones"
    )


def test_una_lista_de_tres_o_mas_si_es_un_menu():
    assert master._looks_like_menu("¿Cuál?\n1) Uno\n2) Dos\n3) Tres")


@pytest.mark.asyncio
async def test_el_menu_del_modelo_se_reescribe_con_la_taxonomia_real(sin_red):
    """El eslabón que lo sostiene todo: sin esto, el “7” no se puede resolver.

    El modelo propone un menú (a veces con nombres suyos); el sistema lo
    sustituye por la taxonomía real y **guarda esa numeración**, que es la que
    verá el cliente y la que resolverá el turno siguiente.
    """
    from app.harness.contracts import AgentResult

    result = AgentResult(
        user_facing=(
            "¡Perfecto! 🎁 ¿Qué categoría te interesa? Responde con el número:\n\n"
            "1) Flores y arreglos\n2) Desayunos gourmet\n3) Peluches y cajas regalo"
        )
    )
    await master._own_the_menu(result, _turn("quiero información"), ConversationState())

    # Los nombres inventados desaparecen; entran los reales, completos.
    assert "Flores y arreglos" not in result.user_facing
    assert "Peluches y cajas regalo" not in result.user_facing
    assert "1) Arreglos Florales" in result.user_facing
    assert "7) Plantas" in result.user_facing, "no omitir lo que sí vendemos"

    # Y queda recordada, en el MISMO orden en que se numeró.
    guardadas = result.state_patch["recent_options"]
    assert [o["nombre"] for o in guardadas][:2] == ["Arreglos Florales", "Desayunos"]
    assert result.state_patch["menu_depth"] == 1
    assert resolve_option("7", guardadas)["slug"] == "plantas"


@pytest.mark.asyncio
async def test_si_el_cliente_ya_dijo_flores_no_se_le_ofrecen_desayunos(sin_red):
    """Regresión propia (Miguel, 22-07), causada por la primera versión de esto.

    Preguntó "¿Cuáles son las opciones de flores disponibles?" y el sistema le
    reescribió el menú con las SIETE categorías padre: desayunos, peluches,
    cestas, plantas… Le borró de la respuesta lo único que había pedido, y le
    hizo elegir otra vez algo que ya había elegido.
    """
    from app.harness.contracts import AgentResult

    result = AgentResult(user_facing="¿Qué tipo de flores buscas?\n1) A\n2) B\n3) C")
    await master._own_the_menu(
        result, _turn("¿Cuáles son las opciones de flores disponibles?"),
        ConversationState(),
    )

    # Nada que no sean flores. Se compara la ENTRADA del menú (") Peluches") y
    # no la palabra suelta: "Arreglos Florales con Peluches" sí es una hija
    # legítima de flores y no debe hacer fallar el test.
    for ajeno in ("Desayunos", "Peluches", "Cestas", "Plantas", "Regalos para Bebé"):
        assert f") {ajeno}" not in result.user_facing, f"{ajeno} no es una flor"
    # Y sí las hijas reales, incluidas las ocasiones.
    assert "Ramos" in result.user_facing
    assert "Arreglos Florales de Cumpleaños" in result.user_facing
    assert result.state_patch["menu_depth"] == 2, "ya se gastó un paso: el cliente eligió"


@pytest.mark.asyncio
async def test_una_categoria_sin_hijas_va_directa_a_productos(sin_red):
    """Cestas no tiene subcategorías: preguntar otra vez es hacer perder el tiempo."""
    from app.harness.contracts import AgentResult

    result = AgentResult(user_facing="¿Qué te enseño?\n1) A\n2) B\n3) C")
    await master._own_the_menu(
        result, _turn("quiero unas cestas"), ConversationState()
    )

    assert result.artifacts, "sin hijas que ofrecer, tocan productos"
    assert result.state_patch["menu_depth"] == 0


@pytest.mark.parametrize(
    "texto",
    [
        "¡Hola! Quiero más información",   # no concreta nada
        "quiero un regalo",                # "regalo" no es una categoría
        "busco arreglos",                  # ambiguo: Florales y Fúnebres
    ],
)
@pytest.mark.asyncio
async def test_si_no_nombro_una_categoria_clara_se_ofrecen_las_padre(texto, sin_red):
    from app.harness.contracts import AgentResult

    result = AgentResult(user_facing="¿Qué te interesa?\n1) A\n2) B\n3) C")
    await master._own_the_menu(result, _turn(texto), ConversationState())

    assert "1) Arreglos Florales" in result.user_facing
    assert "7) Plantas" in result.user_facing
    assert result.state_patch["menu_depth"] == 1


def test_match_category_no_confunde_un_regalo_generico_con_regalos_para_bebe(taxonomia):
    """"quiero un regalo" casaba con "Regalos para Bebé" por la palabra suelta."""
    from app.harness.taxonomy import match_category

    assert match_category("quiero un regalo", taxonomia) is None
    assert match_category("busco arreglos", taxonomia) is None, "Florales y Fúnebres"
    assert match_category("flores", taxonomia)["slug"] == "arreglos-florales"
    assert match_category("algo para un bebé", taxonomia)["slug"] == "regalo-para-bebe"
    assert match_category("un desayuno sorpresa", taxonomia)["slug"] == "desayunos"


def test_la_coletilla_del_numero_no_se_duplica():
    """Salió al cliente: "…buscas? Elige el número Responde con el número:"."""
    assert master._intro_of(
        "¿Qué tipo de flores buscas? Elige el número\n\n1) A\n2) B\n3) C"
    ) == "¿Qué tipo de flores buscas?"
    assert master._intro_of(
        "¿Cuál prefieres? Responde con el número:\n1) A\n2) B\n3) C"
    ) == "¿Cuál prefieres?"


@pytest.mark.asyncio
async def test_una_respuesta_sin_menu_no_se_toca(sin_red):
    """Solo se suplantan menús. Una frase normal sale tal cual."""
    from app.harness.contracts import AgentResult

    result = AgentResult(user_facing="¿Para qué ocasión es el regalo? 😊")
    await master._own_the_menu(result, _turn("quiero información"), ConversationState())

    assert result.user_facing == "¿Para qué ocasión es el regalo? 😊"
    assert not result.state_patch


def test_la_intro_del_modelo_se_conserva_sin_la_coletilla():
    intro = master._intro_of(
        "¡Genial! 🌿 ¿Qué tipo de planta te interesa? Responde con el número:\n\n1) A\n2) B\n3) C"
    )
    assert intro == "¡Genial! 🌿 ¿Qué tipo de planta te interesa?"
