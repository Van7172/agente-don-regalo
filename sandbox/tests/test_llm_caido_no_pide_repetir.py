"""Si el modelo se cae, el bot enseña lo que tiene — no pide que le repitan.

El incidente (02-08, tres chats en la misma mañana). OpenAI devolvía **429
sostenido**: los cuatro reintentos de `_chat_completion` se agotaban, la
excepción moría en el `except` del bucle del agente y `run_specialist` devolvía
`user_facing=None`. Ese None llegaba a `buffer`, que soltaba siempre lo mismo:

    Disculpa, se me cruzó un cable 😅 Cuéntame otra vez qué buscas y te ayudo
    al toque.

Los tres clientes ya habían sido todo lo específicos que podían:

    "Buen día. Me gustaría ver su catalogo"   → una frase, un cable cruzado
    "QUIERO DESAYUNO"                          → una frase, un cable cruzado
    "Que tiempo demora un pedido"              → una frase, un cable cruzado

Pedirles que repitan no añade información: es un rodeo que devuelve la
conversación al punto de partida. Y lo peor es que **el dato para contestarles
no necesitaba modelo**: el router los clasificó bien con reglas (`catalog_search`
los dos primeros) y `match_category("QUIERO DESAYUNO")` resuelve a `desayunos`.
El listado lo compone el código desde la API, con precios y fotos reales.

Los tres acabaron en «Necesita ayuda humana», que es donde habrían acabado igual
—pero después de gastarle al cliente un turno inútil y las ganas.
"""
import json
import pathlib

import pytest

from app.harness import master
from app.harness.contracts import AgentResult, EscalateReason
from app.harness.router import Classification
from app.tools import adapters

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"

# Lo que el cliente NO puede volver a leer: le pide repetir lo que acaba de decir.
RODEO = "cuéntame otra vez"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def catalogo_vivo(monkeypatch):
    """La API responde con normalidad. El que está caído es el modelo."""

    async def fake(nombre: str, args: dict) -> str:
        if nombre == "explorar_catalogo":
            return json.dumps(load("catalogo_navegacion"))
        if nombre in ("catalogo_categoria", "productos_destacados"):
            return json.dumps(
                adapters.products_payload(load("categoria_productos"), 3.4)
            )
        raise AssertionError(f"tool inesperada: {nombre}")

    monkeypatch.setattr(master, "execute_tool", fake)


@pytest.fixture
def especialista_mudo(monkeypatch):
    """Lo que devuelve `run_specialist` cuando OpenAI no contesta."""

    async def mudo(*_args, **_kwargs):
        return AgentResult(user_facing=None)

    monkeypatch.setattr(master, "_run_specialty", mudo)


@pytest.fixture
def correr(monkeypatch):
    """Un turno completo, devolviendo el `AgentResult` final.

    Va por `run_master` y no por `_handle` a propósito: el rescate vive un nivel
    por encima porque `_handle_detail` devuelve su especialista directamente, y
    cubriendo solo la rama del final una pregunta por el contenido de un
    desayuno se quedaba con la disculpa.
    """
    visto: dict = {}
    reduce_real = master._reduce

    def espia(state, result, *, intent=""):
        visto["result"] = result
        return reduce_real(state, result, intent=intent)

    monkeypatch.setattr(master, "_reduce", espia)

    async def _correr(intent: str, text: str, conversation_id: int | None = None):
        async def fake_classify(*_a, **_kw):
            return Classification(intent=intent, confidence=0.9, source="rules")

        monkeypatch.setattr(master, "classify", fake_classify)
        await master.run_master(
            [{"role": "user", "content": text}],
            wa_id="51900000000",
            conversation_id=conversation_id,
        )
        return visto["result"]

    return _correr


@pytest.fixture
def handoff_espia(monkeypatch):
    """Captura la cesión sin tocar el CRM."""
    cedido: dict = {}

    async def fake_handoff(**kwargs):
        cedido.update(kwargs)
        return EscalateReason(ceded=True, motivo=kwargs.get("motivo", ""))

    monkeypatch.setattr(master, "perform_handoff", fake_handoff)
    return cedido


# ── Lo que sí se puede contestar sin modelo ───────────────────────────

@pytest.mark.asyncio
async def test_quiero_desayuno_muestra_desayunos_no_una_disculpa(
    catalogo_vivo, especialista_mudo, correr
):
    """El caso de Tavo. "QUIERO DESAYUNO" en mayúsculas resuelve igual."""
    result = await correr("catalog_search", "QUIERO DESAYUNO")

    assert result.artifacts, "la categoría se puede resolver sin el modelo"
    assert RODEO not in (result.user_facing or "").lower()
    assert "Desayunos" in result.user_facing


@pytest.mark.asyncio
async def test_ver_el_catalogo_muestra_lo_mas_pedido(
    catalogo_vivo, especialista_mudo, correr
):
    """El caso de Jhoel. No nombró categoría, pero "enséñame" se responde enseñando."""
    result = await correr("catalog_search", "Buen día. Me gustaría ver su catalogo")

    assert result.artifacts
    assert RODEO not in (result.user_facing or "").lower()


@pytest.mark.asyncio
async def test_la_categoria_manda_aunque_el_intent_no_sea_catalogo(
    catalogo_vivo, especialista_mudo, correr
):
    """Que el modelo esté caído no suspende la regla de la categoría.

    Es la señal más fuerte que da un cliente: si nombró una, esa es la respuesta
    venga el intent que venga.
    """
    result = await correr("small_talk", "hola, quiero un desayuno para mañana")

    assert result.artifacts
    assert "Desayunos" in result.user_facing


# ── Lo que NO se puede contestar sin modelo ───────────────────────────

@pytest.mark.asyncio
async def test_una_pregunta_que_no_es_de_catalogo_se_deriva(
    catalogo_vivo, especialista_mudo, handoff_espia, correr
):
    """El caso de "activo": preguntó por los tiempos de entrega.

    Soltarle un listado de productos sería otro rodeo, y uno peor: le cambia el
    tema. Sin el modelo no sabemos si preguntó por el envío, por el Yape o por
    si se puede quitar el croissant, así que lo coge un humano.
    """
    result = await correr(
        "small_talk", "Que tiempo demora un pedido", conversation_id=99
    )

    assert result.escalate is not None, "sin catálogo que enseñar, se cede"
    assert not result.artifacts, "no se le cambia el tema con productos"
    assert handoff_espia["conversation_id"] == 99


@pytest.mark.asyncio
async def test_si_la_api_tambien_esta_caida_se_deriva(
    monkeypatch, especialista_mudo, handoff_espia, correr
):
    """Modelo caído Y API caída: no hay respuesta honesta posible."""

    async def api_muerta(_nombre: str, _args: dict) -> str:
        raise RuntimeError("API caída")

    monkeypatch.setattr(master, "execute_tool", api_muerta)

    result = await correr("catalog_search", "quiero un peluche", conversation_id=7)

    assert result.escalate is not None


@pytest.mark.asyncio
async def test_el_detalle_de_un_producto_tampoco_se_queda_en_la_disculpa(
    catalogo_vivo, especialista_mudo, handoff_espia, correr
):
    """`_handle_detail` devuelve su especialista directo, saltándose `_handle`.

    Por eso el rescate vive en `_run_master`: la primera versión colgaba de la
    última rama de `_handle` y esta pregunta se quedaba fuera.
    """
    result = await correr(
        "product_detail", "¿qué trae ese desayuno?", conversation_id=5
    )

    assert result.artifacts or result.escalate is not None
    assert RODEO not in (result.user_facing or "").lower()


# ── Lo que el rescate NO puede pisar ──────────────────────────────────

@pytest.mark.asyncio
async def test_un_handoff_legitimo_del_especialista_no_se_rescata(
    catalogo_vivo, correr, monkeypatch
):
    """`run_specialist` también devuelve `user_facing=None` al ceder el chat.

    Ese None significa "ya cedí", no "no supe": distinguirlos es lo único que
    impide que el rescate le vuelva a enseñar productos a alguien que acaba de
    ser derivado a un asesor.
    """

    async def cedio(*_args, **_kwargs):
        return AgentResult(
            user_facing=None,
            escalate=EscalateReason(ceded=True, motivo="lo pidió el cliente"),
        )

    monkeypatch.setattr(master, "_run_specialty", cedio)

    result = await correr("catalog_search", "quiero hablar con alguien")

    assert result.escalate is not None
    assert not result.artifacts, "no se le enseña un catálogo a quien ya fue derivado"


@pytest.mark.asyncio
async def test_una_respuesta_normal_no_se_toca(catalogo_vivo, correr, monkeypatch):
    """El rescate solo entra cuando el turno se queda sin voz."""

    async def contesta(*_args, **_kwargs):
        return AgentResult(user_facing="El envío a Miraflores cuesta S/15.00 😊")

    monkeypatch.setattr(master, "_run_specialty", contesta)

    result = await correr("small_talk", "cuánto es el envío a Miraflores")

    assert result.user_facing == "El envío a Miraflores cuesta S/15.00 😊"
    assert not result.artifacts
