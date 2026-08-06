"""Un turno con dos intenciones se contesta entero, con una sola voz.

Medido sobre turnos reales antes de arreglarlo:

    "Quiero un desayuno para mañana. ¿Llegan a SMP?"      → coverage (0.95)
    "¿Cuánto cuesta el ramo de rosas y hacen delivery…?"  → coverage (0.97)
    "¿Qué contiene ese desayuno? ¿y aceptan yape?"        → policy_faq (0.85)

Ganaba la señal más inequívoca y **el resto del mensaje se descartaba en
silencio**. Un nombre de distrito es facilísimo de detectar, así que la mitad
logística se comía a la mitad comercial — al revés de lo que conviene: la
cobertura cabe en una frase subordinada y un catálogo no, que necesita fotos,
precios y lista.

Y no es un caso raro: lo fabrica el buffer. `collapse_parts` une la ráfaga de
mensajes de WhatsApp en UN turno, así que el turno multi-intención es la forma
normal de escribir de un cliente.

La solución NO es lanzar dos especialistas (dos voces, dos saludos, dos
listados que se pisan). Es que lo comercial se quede el micrófono y la cobertura
entre como hecho ya resuelto en el system del especialista.
"""
import json
import pathlib

import pytest

from app.harness import coverage as cov
from app.harness import master
from app.harness.contracts import AgentResult, Product, Turn
from app.harness.state import ConversationState
from app.guardrails import check_reply
from app.tools import adapters

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def cobertura_real(monkeypatch):
    """Los distritos de verdad, pasados por el adapter (tarifas en soles)."""
    crudo = load("distritos")

    async def fake_distritos(client, args):
        return adapters.districts_payload(crudo, 3.4)

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", fake_distritos)


@pytest.fixture
def catalogo_real(monkeypatch):
    async def fake(nombre: str, args: dict) -> str:
        if nombre == "explorar_catalogo":
            return json.dumps(load("catalogo_navegacion"))
        if nombre in ("catalogo_categoria", "productos_destacados"):
            return json.dumps(adapters.products_payload(load("categoria_productos"), 3.4))
        raise AssertionError(f"tool inesperada: {nombre}")

    monkeypatch.setattr(master, "execute_tool", fake)


@pytest.fixture
def especialista(monkeypatch):
    """Simula al catálogo y captura el `extra_system` que le llegó."""
    visto: dict = {}

    async def fake(intent, turn, state, *, extra_system="", **kwargs):
        visto["intent"] = intent
        visto["extra_system"] = extra_system
        return AgentResult(
            user_facing="Estos desayunos te van a encantar 🎁",
            artifacts=[Product(id_producto=1, nombre="Desayuno Feliz", precio_sol=89.0)],
        )

    monkeypatch.setattr(master, "_run_specialty", fake)
    return visto


async def _coverage(text: str, state: ConversationState | None = None) -> AgentResult:
    return await master._handle_coverage(
        Turn(text=text, has_media=False, messages=[]),
        state or ConversationState(),
        wa_id="51900000000",
    )


# ── El turno mixto se contesta entero ─────────────────────────────────

@pytest.mark.asyncio
async def test_producto_mas_cobertura_no_pierde_el_producto(
    cobertura_real, catalogo_real, especialista
):
    """La fila 1 de la tabla: el desayuno ya no se descarta."""
    result = await _coverage("Quiero un desayuno para mañana. ¿Llegan a Comas?")

    assert result.artifacts, "el desayuno se perdía entero"
    assert especialista["intent"] == "catalog_search", "la voz es del catálogo"
    assert "Comas" in especialista["extra_system"].title()
    assert "S/" in especialista["extra_system"], "la tarifa entra como hecho"


@pytest.mark.asyncio
async def test_el_precio_pedido_se_detecta_aunque_no_sea_una_categoria(
    cobertura_real, catalogo_real, especialista
):
    """La fila 2. `match_category` no reconoce "ramo de rosas" —devuelve `None`
    ante la duda a propósito—, así que aquí quien lo salva es partir el turno en
    cláusulas y clasificar cada una con las reglas de siempre.
    """
    result = await _coverage("¿Cuánto cuesta el ramo de rosas y hacen delivery a Comas?")

    assert result.artifacts, "la petición de precio se descartaba"


@pytest.mark.asyncio
async def test_el_distrito_se_guarda_igual(cobertura_real, catalogo_real, especialista):
    """Aunque hable el catálogo, la cobertura deja el cierre medio hecho."""
    result = await _coverage("Quiero un desayuno. ¿Llegan a Comas?")

    assert result.state_patch.get("district")
    assert result.state_patch.get("shipping_fee_sol", 0) > 0
    assert "distritos_cobertura" in result.tools_used


# ── La cobertura pura NO se secuestra ─────────────────────────────────

@pytest.mark.parametrize(
    "texto",
    [
        "¿Llegan a Comas?",
        "¿Cuánto cuesta el envío a Miraflores?",
        "hacen delivery a Comas?",
        "Estoy en San Juan de Lurigancho",
    ],
)
@pytest.mark.asyncio
async def test_una_pregunta_solo_de_cobertura_se_queda_en_cobertura(
    texto, cobertura_real, catalogo_real, especialista
):
    """El falso positivo sería peor que el problema.

    Soltarle un catálogo a quien solo quería una tarifa le cambia el tema y le
    entierra la respuesta bajo cinco fotos.
    """
    result = await _coverage(texto)

    assert not result.artifacts, f"{texto!r} no pedía productos"
    assert "S/" in (result.user_facing or "") or "?" in (result.user_facing or "")


@pytest.mark.asyncio
async def test_durante_el_cierre_manda_el_formulario(
    cobertura_real, catalogo_real, especialista
):
    """Con el cierre en marcha, el distrito ES el paso del formulario.

    Enseñarle productos a quien está confirmando su pedido lo devuelve al
    principio: es la conversación que no avanza, otra vez.
    """
    state = ConversationState(checkout_step="district", chosen_product_id=7)
    result = await _coverage("Quiero un desayuno. ¿Llegan a Comas?", state)

    assert not result.artifacts


@pytest.mark.asyncio
async def test_si_la_cobertura_no_se_resuelve_pregunta_ella(
    cobertura_real, catalogo_real, especialista
):
    """Un hecho subordinado no puede hacer una pregunta.

    Si no sabemos el distrito, la cobertura necesita repreguntar — y eso exige
    quedarse con la voz.
    """
    result = await _coverage("Quiero un desayuno. ¿Llegan a Ciudad Gótica?")

    assert not result.artifacts
    assert "?" in (result.user_facing or "")


@pytest.mark.asyncio
async def test_si_el_especialista_no_habla_queda_la_tarifa(
    cobertura_real, catalogo_real, monkeypatch
):
    """Con OpenAI caído, la tarifa sigue siendo una respuesta útil."""

    async def mudo(*_a, **_kw):
        return AgentResult(user_facing=None)

    monkeypatch.setattr(master, "_run_specialty", mudo)

    result = await _coverage("Quiero un desayuno. ¿Llegan a Comas?")

    assert result.user_facing, "no puede quedarse mudo teniendo la tarifa"
    assert "S/" in result.user_facing


# ── La tarifa no es un precio inventado ───────────────────────────────

def test_el_envio_no_se_marca_como_precio_inventado():
    """`prices_are_sourced` compara contra los precios de los PRODUCTOS.

    La respuesta de cobertura sola se libra por accidente (no lleva artifacts y
    la invariante sale antes de comparar). En un turno mixto sí los hay, así que
    sin declarar la tarifa el envío se marcaba como invento y la barrera —que
    esta regla sí bloquea— tiraba la prosa entera.
    """
    reply = "Te muestro los desayunos 🎁 S/89.00\nY sí, llegamos a Comas: envío S/18.90."
    productos = [Product(id_producto=1, nombre="Desayuno Feliz", precio_sol=89.0)]

    sin_declarar = check_reply(reply, artifacts=productos)
    assert any(v.rule == "prices_are_sourced" for v in sin_declarar)

    declarado = check_reply(reply, artifacts=productos, sourced_prices=[18.90])
    assert not any(v.rule == "prices_are_sourced" for v in declarado)


# ── Políticas × producto ──────────────────────────────────────────────

@pytest.fixture
def especialista_generico(monkeypatch):
    """Captura el intent y el `extra_system` de cualquier especialista."""
    visto: dict = {}

    async def fake(intent, turn, state, *, extra_system="", **kwargs):
        visto["intent"] = intent
        visto["extra_system"] = extra_system
        return AgentResult(user_facing="Ahí va 🎁")

    monkeypatch.setattr(master, "_run_specialty", fake)
    return visto


async def _handle(intent: str, text: str, state: ConversationState):
    return await master._handle(
        intent, Turn(text=text, has_media=False, messages=[]), state, wa_id="51900000000"
    )


@pytest.mark.asyncio
async def test_el_pago_llega_al_agente_de_detalle(
    catalogo_real, especialista_generico, monkeypatch
):
    """El caso: "¿Qué contiene ese desayuno? ¿y aceptan yape?".

    Con un listado ya enseñado, el router acierta el primario (`product_detail`)
    y lo que se perdía era el yape — porque los FACTS se componen POR AGENTE y
    `detail` solo lleva `pricing`. No era un fallo de enrutado: el system del
    que hablaba no tenía el dato.
    """

    async def sin_detalle(_turn, _state):
        return None

    monkeypatch.setattr(master, "_prefetch_detalle", sin_detalle)

    state = ConversationState(shown_product_ids=[1], presented=True)
    await _handle("product_detail", "¿Qué contiene ese desayuno? ¿y aceptan yape?", state)

    assert especialista_generico["intent"] == "product_detail"
    assert "Yape" in especialista_generico["extra_system"]


@pytest.mark.asyncio
async def test_sin_listado_previo_manda_lo_comercial_igual(
    catalogo_real, especialista_generico
):
    """Sin productos enseñados el router elige `policy_faq` y se comía el ramo.

    La misma regla lo resuelve por el otro lado: la voz se la queda lo
    comercial y la política entra como apostilla.
    """
    state = ConversationState(presented=True)
    await _handle("policy_faq", "¿Cuánto cuesta el ramo de rosas y aceptan tarjeta?", state)

    assert especialista_generico["intent"] == "catalog_search"
    assert "Yape" in especialista_generico["extra_system"]


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("¿Qué contiene? ¿aceptan yape?", "Yape"),
        ("¿Qué contiene? ¿hacen devoluciones?", "DEVOLUCIONES"),
    ],
)
@pytest.mark.asyncio
async def test_se_inyecta_solo_la_politica_preguntada(
    texto, esperado, catalogo_real, especialista_generico, monkeypatch
):
    """Volcarle las cuatro políticas para contestar una es prompt que se paga
    en cada turno, y ruido del que el modelo extrapola."""

    async def sin_detalle(_turn, _state):
        return None

    monkeypatch.setattr(master, "_prefetch_detalle", sin_detalle)

    state = ConversationState(shown_product_ids=[1], presented=True)
    await _handle("product_detail", texto, state)

    bloque = especialista_generico["extra_system"]
    assert esperado in bloque
    assert bloque.count("##") == 1, "solo un bloque de FACTS"


@pytest.mark.asyncio
async def test_una_pregunta_solo_de_politicas_no_se_secuestra(
    catalogo_real, especialista_generico
):
    """"¿Aceptan yape?" a secas la contesta el agente de políticas, que ya
    lleva esos FACTS. Meterle un catálogo sería cambiarle el tema."""
    state = ConversationState(presented=True)
    await _handle("policy_faq", "¿aceptan yape?", state)

    assert especialista_generico["intent"] == "policy_faq"
    assert especialista_generico["extra_system"] == "", "policy ya tiene sus FACTS"


def test_declarar_la_tarifa_no_abre_la_mano_con_las_demas():
    """Se declara un importe concreto, no una exención del turno."""
    reply = "El desayuno cuesta S/89.00, el envío S/18.90 y el peluche S/45.00."
    productos = [Product(id_producto=1, nombre="Desayuno Feliz", precio_sol=89.0)]

    violaciones = check_reply(reply, artifacts=productos, sourced_prices=[18.90])

    assert any(v.rule == "prices_are_sourced" for v in violaciones), (
        "S/45.00 no salió de ninguna tool y sigue siendo un invento"
    )
