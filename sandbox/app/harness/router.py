"""Router de intención: reglas deterministas + clasificador LLM de respaldo.

Las reglas son rápidas y precisas cuando aciertan. El problema era qué pasaba
cuando NO acertaban: el caso por defecto devolvía `catalog_search`, así que
cualquier mensaje que ninguna regla reconociera acababa buscando productos. Los
dos bugs que cazó el corpus el primer día tenían justo esa forma.

Ahora las reglas devuelven confianza, y por debajo de `CONFIDENCE_FLOOR` decide un
clasificador LLM barato. Si el LLM falla o no hay clave, mandan las reglas: el
router nunca puede tumbar un turno.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass

import httpx

from app.harness.checkout import resolve_chosen_product, wants_checkout
from app.harness.coverage import looks_like_coverage
from app.harness.policies import is_small_talk
from app.harness.state import ConversationState

log = logging.getLogger(__name__)

Intent = str  # greet|small_talk|catalog_search|coverage|product_detail|checkout|policy_faq|track_order|escalate

_GREET_RE = re.compile(
    r"^(hola|holi|buenas|buenos\s+dias|buenas\s+tardes|buenas\s+noches|hey|hi)\b",
    re.I,
)
_TRACK_RE = re.compile(r"rastre|donde\s+esta\s+mi\s+pedido|estado\s+de\s+mi\s+pedido|seguimiento", re.I)
_DETAIL_RE = re.compile(
    r"mas\s+detalle|m[aá]s\s+info|qu[eé]\s+contiene|como\s+es|cuanto\s+mide|foto\s+de|"
    r"de\s+qu[eé]\s+viene|medida|tama[nñ]o",
    re.I,
)
_ESCALATE_RE = re.compile(
    r"asesor|humano|persona|atenci[oó]n\s+humana|p[aá]same\s+con|"
    r"comprobante|ya\s+pagu|mala\s+atenci|quiero\s+hablar\s+con",
    re.I,
)
_POLICY_RE = re.compile(
    r"politica|pol[ií]tica|horario\s+de\s+atenci|garant|devoluci|factura|"
    r"hasta\s+que\s+hora\s+puedo\s+pagar|m[eé]todos?\s+de\s+pago|c[oó]mo\s+pago|"
    # Preguntas de pago tal como las escribe el cliente. Sin esto, "¿puedo pagar
    # contra entrega?" en mitad del cierre lo absorbía el FSM.
    r"puedo\s+pagar|formas?\s+de\s+pago|contra\s*entrega|contraentrega|"
    r"pagar\s+en\s+efectivo|aceptan\s+(yape|plin|tarjeta|transferencia)",
    re.I,
)
_CATALOG_RE = re.compile(
    r"busco|quiero|tienen|peluche|desayuno|ramo|rosa|girasol|flor|regalo|"
    r"cesta|planta|terrario|panda|osito|catalogo|cat[aá]logo|opcion|modelo|muestra|manda|"
    r"dia\s+del\s+padre|fiestas\s+patrias|corp",
    re.I,
)
_BUY_VERB_RE = re.compile(
    r"quiero|me\s+lo\s+llevo|lo\s+pido|me\s+quedo\s+con|elijo|escojo|reserv|comprar|"
    r"me\s+gusta\s+est|me\s+interesa",
    re.I,
)
_SMALL_RE = re.compile(
    r"^(ok|okay|gracias|gale|dale|jaja|jeje|perfecto|listo|👍|😊|🙏)[\s!.]*$",
    re.I,
)
# Confirmación afirmativa "sí, muéstramelos" tal cual la escribe el cliente.
# Anclada a todo el mensaje (^…$): solo dispara con una confirmación pelada, no
# con una frase larga que empiece por "sí" y siga con otra intención. Se compara
# contra el texto SIN tildes (`norm`).
_CONFIRM_SHOW_RE = re.compile(
    r"^\s*(si+|sip+|claro( que si)?|dale|de una|va|vale|bueno|obvio|"
    r"muestrame(los|las)?|muestra(los|las|melos|melas)?|ensena(melos|melas)?|"
    r"a ver|ver(los|las)?|quiero ver(los|las)?|si (quiero|porfa|por favor|dale)"
    r")[\s!.,]*$",
    re.I,
)


def _strip_accents(text: str) -> str:
    """"¿Dónde está mi pedido?" tiene que enrutar igual que "donde esta"."""
    nfd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class Classification:
    intent: Intent
    confidence: float
    source: str  # "rules" | "llm" | "fallback"


# Por debajo de esto, las reglas no saben: entra el clasificador LLM.
CONFIDENCE_FLOOR = 0.5


def classify_intent(text: str, state: ConversationState | None = None) -> Intent:
    """Solo la etiqueta de las reglas. Determinista: no llama a nadie."""
    return classify_rules(text, state).intent


def classify_rules(text: str, state: ConversationState | None = None) -> Classification:
    """Reglas: rápidas y precisas cuando aciertan, mudas cuando no.

    Devuelven confianza para que el orquestador sepa si puede fiarse. El caso por
    defecto (catálogo) sale con confianza baja a propósito: era el agujero por el
    que se colaba todo lo que ninguna regla reconocía.
    """
    raw = (text or "").strip()
    if not raw:
        return Classification("small_talk", 0.9, "rules")

    state = state or ConversationState()

    # Los patrones se escribieron sin tildes. Sin normalizar, "¿Dónde está mi
    # pedido?" no casaba con `_TRACK_RE` y acababa en el catálogo, buscando
    # productos en vez de rastrear el pedido.
    norm = _strip_accents(raw)

    if _ESCALATE_RE.search(norm):
        return Classification("escalate", 0.95, "rules")

    if _TRACK_RE.search(norm):
        return Classification("track_order", 0.95, "rules")

    # Cierre en curso: manda el FSM… salvo que el cliente pregunte otra cosa.
    #
    # Antes esto devolvía `checkout` con confianza 1.0 pasara lo que pasara, así que
    # "¿puedo pagar contra entrega?" en mitad del cierre no recibía respuesta:
    # recibía el siguiente paso del formulario. El cierre no se pierde — el paso
    # sigue en el estado y el FSM lo retoma en el turno siguiente.
    if state.checkout_step and state.checkout_step not in ("idle", "done", "payment"):
        if _POLICY_RE.search(norm):
            return Classification("policy_faq", 0.8, "rules")
        return Classification("checkout", 1.0, "rules")

    if wants_checkout(norm) or norm.casefold() in ("ese", "esa", "este", "esta"):
        return Classification("checkout", 0.9, "rules")

    # Confirmación afirmativa cuando el turno anterior fue de producto: el cliente
    # responde "sí / dale / muéstramelos" a un ofrecimiento de mostrar productos.
    # El router ve el mensaje aislado, así que sin esto un "Si" tras "¿quieres que
    # te muestre?" caía en small_talk → concierge, que no tiene tools de catálogo:
    # el modelo inventaba un menú de productos falso o escalaba una venta sana.
    if state.intent_last in ("catalog_search", "product_detail") and _CONFIRM_SHOW_RE.match(norm):
        return Classification("catalog_search", 0.85, "rules")

    # "quiero el panditas" nombra un producto que YA se mostró: es una compra, no
    # una búsqueda nueva. Exigimos referencia explícita (nombre u ordinal): con un
    # solo producto a la vista, "quiero flores" sigue siendo una búsqueda.
    if (
        _BUY_VERB_RE.search(norm)
        and resolve_chosen_product(state, raw, allow_implicit=False) is not None
    ):
        return Classification("checkout", 0.95, "rules")

    if looks_like_coverage(norm) and not _CATALOG_RE.search(norm[:40]):
        return Classification("coverage", 0.85, "rules")

    # Cobertura pura (pregunta de zonas)
    if re.search(r"zonas?\s+cubren|distritos?\s+de\s+lima|llegan\s+a", norm, re.I):
        return Classification("coverage", 0.95, "rules")

    if _DETAIL_RE.search(norm) and state.shown_product_ids:
        return Classification("product_detail", 0.9, "rules")

    if _POLICY_RE.search(norm):
        return Classification("policy_faq", 0.85, "rules")

    if _GREET_RE.match(norm) and len(raw) < 40:
        return Classification("greet", 0.95, "rules")

    # Misma definición de cortesía que usa la política de handoff. Antes el router
    # tenía la suya, más pobre: "Todo en orden hoy" no le sonaba a charla y acababa
    # en el catálogo, buscando productos para alguien que no pedía nada.
    if is_small_talk([{"role": "user", "content": raw}]):
        return Classification("small_talk", 0.9, "rules")

    if _CATALOG_RE.search(norm):
        return Classification("catalog_search", 0.8, "rules")

    # Si hay distrito pendiente y mensaje corto tipo lugar → coverage
    if len(raw) < 60 and looks_like_coverage(norm):
        return Classification("coverage", 0.7, "rules")

    # Ninguna regla reconoció el mensaje. Antes se devolvía `catalog_search` a
    # secas y el cliente acababa recibiendo productos que no pidió. Ahora sale con
    # confianza baja para que `classify()` se lo pase al clasificador LLM.
    return Classification("catalog_search", 0.3, "fallback")


VALID_INTENTS = frozenset(
    {
        "greet",
        "small_talk",
        "catalog_search",
        "product_detail",
        "coverage",
        "checkout",
        "policy_faq",
        "track_order",
        "escalate",
    }
)


async def classify(text: str, state: ConversationState | None = None) -> Classification:
    """Reglas primero; el LLM solo cuando las reglas no saben.

    Las reglas resuelven la inmensa mayoría de los turnos sin coste ni latencia.
    El LLM entra únicamente en el hueco que antes se tragaba el catálogo.
    """
    rules = classify_rules(text, state)
    if rules.confidence >= CONFIDENCE_FLOOR:
        return rules

    llm = await classify_with_llm(text)
    return llm or rules


async def classify_with_llm(text: str) -> Classification | None:
    """Clasificador barato con salida estructurada. `None` si no se puede."""
    from app.config import settings

    if not settings.openai_api_key:
        return None

    from app.prompts.playbooks import ORCHESTRATOR

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.router_model,
                    "messages": [
                        {"role": "system", "content": ORCHESTRATOR},
                        {"role": "user", "content": (text or "")[:500]},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                },
            )
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as err:
        # El router nunca puede tumbar un turno: si el LLM falla, mandan las reglas.
        log.warning("[router] clasificador LLM no disponible: %s", err)
        return None

    intent = str(data.get("intent") or "").strip()
    if intent not in VALID_INTENTS:
        log.warning("[router] el LLM devolvió una intención desconocida: %r", intent)
        return None

    try:
        confidence = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6

    return Classification(intent, min(max(confidence, 0.0), 1.0), "llm")
