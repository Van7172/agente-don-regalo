"""Router híbrido: reglas primero, LLM solo en el hueco.

El clasificador LLM cuesta dinero y latencia en un canal donde el cliente está
mirando el "escribiendo…". Estas dos garantías son las que lo hacen aceptable:
no se llama cuando las reglas ya saben, y si falla no tumba el turno.
"""
import pytest

from app.harness import router as router_mod
from app.harness.router import CONFIDENCE_FLOOR, Classification, classify, classify_rules
from app.harness.state import ConversationState


@pytest.fixture
def espia_llm(monkeypatch):
    """Cuenta las llamadas al clasificador LLM."""
    llamadas: list[str] = []

    async def fake(text: str):
        llamadas.append(text)
        return Classification("policy_faq", 0.8, "llm")

    monkeypatch.setattr(router_mod, "classify_with_llm", fake)
    return llamadas


@pytest.mark.parametrize(
    "text,intent",
    [
        ("Hola buenas tardes", "greet"),
        ("busco peluches", "catalog_search"),
        ("¿Llegan a Miraflores?", "coverage"),
        ("¿Dónde está mi pedido?", "track_order"),
        ("Quiero hablar con un asesor", "escalate"),
        ("Todo en orden hoy", "small_talk"),
    ],
)
@pytest.mark.asyncio
async def test_si_las_reglas_saben_no_se_llama_al_llm(espia_llm, text, intent):
    got = await classify(text, ConversationState())

    assert got.intent == intent
    assert got.source == "rules"
    assert got.confidence >= CONFIDENCE_FLOOR
    assert espia_llm == [], "se pagó una llamada al LLM que no hacía falta"


@pytest.mark.asyncio
async def test_lo_que_las_reglas_no_saben_va_al_llm(espia_llm):
    """Antes esto devolvía catalog_search en silencio."""
    rules = classify_rules("¿A qué hora abren?", ConversationState())
    assert rules.source == "fallback" and rules.confidence < CONFIDENCE_FLOOR

    got = await classify("¿A qué hora abren?", ConversationState())

    assert espia_llm == ["¿A qué hora abren?"]
    assert got.intent == "policy_faq"
    assert got.source == "llm"


@pytest.mark.asyncio
async def test_si_el_llm_falla_mandan_las_reglas(monkeypatch):
    """Un router caído no puede dejar al cliente sin respuesta."""

    async def cae(text: str):
        return None  # timeout, 429, clave ausente…

    monkeypatch.setattr(router_mod, "classify_with_llm", cae)

    got = await classify("Mi esposa cumple años mañana", ConversationState())

    assert got.intent == "catalog_search"  # el default de siempre
    assert got.source == "fallback"


@pytest.mark.asyncio
async def test_una_intencion_inventada_por_el_llm_se_descarta(monkeypatch):
    """Si el modelo devuelve basura, no la propagamos al registro de agentes."""

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": '{"intent": "comprar_bitcoin", "confidence": 0.9}'}}
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr(router_mod.httpx, "AsyncClient", lambda **kw: FakeClient())

    assert await router_mod.classify_with_llm("lo que sea") is None


def test_el_catch_all_sale_con_confianza_baja():
    """Es lo que obliga a que el LLM entre. Si sube, el agujero vuelve."""
    got = classify_rules("Me llegó dañado", ConversationState())

    assert got.source == "fallback"
    assert got.confidence < CONFIDENCE_FLOOR


def _con_intent(last: str) -> ConversationState:
    s = ConversationState()
    s.intent_last = last
    return s


@pytest.mark.parametrize("text", ["Si", "Sí", "dale", "muéstramelos", "claro", "sí quiero", "a ver"])
def test_confirmacion_tras_oferta_de_producto_continua_en_catalogo(text):
    """Regresión (Stepha, 15-07): el cliente respondió "Si" a "¿quieres que te
    muestre los arreglos?" y el turno cayó en small_talk → concierge (sin tools
    de catálogo), que inventó un menú de productos y terminó escalando una venta
    sana. Una confirmación afirmativa cuando el turno previo fue de producto debe
    seguir en catálogo, que busca y muestra productos reales con foto.
    """
    got = classify_rules(text, _con_intent("catalog_search"))
    assert got.intent == "catalog_search"
    assert got.source == "rules"


@pytest.mark.parametrize(
    "text,last",
    [
        ("no", "catalog_search"),          # negativa: no es "muéstrame"
        ("gracias", "catalog_search"),     # cierre de cortesía
        ("Si", "small_talk"),              # sin oferta de producto detrás
        ("Si", ""),                        # primer turno, sin contexto
    ],
)
def test_confirmacion_no_secuestra_charla_sin_contexto_de_producto(text, last):
    got = classify_rules(text, _con_intent(last))
    assert got.intent != "catalog_search"


@pytest.mark.parametrize("text", ["si", "Sí", "dale", "ok", "va", "muéstrame"])
def test_confirmacion_en_derivacion_en_curso_sigue_en_escalate(text):
    """Regresión (Sonia, 15-07): el "sí / dale" a "¿te paso con un asesor ahora?"
    caía en small_talk → concierge (sin la tool de handoff), así que el bot decía
    "te paso, un momento" y nunca cedía el control. Debe seguir en escalate.
    """
    got = classify_rules(text, _con_intent("escalate"))
    assert got.intent == "escalate"
    assert got.source == "rules"
