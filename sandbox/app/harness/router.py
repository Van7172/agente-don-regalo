"""Clasificador de intención (heurístico + barato). El Master LLM puede refinar después."""
from __future__ import annotations

import re

from app.harness.checkout import resolve_chosen_product, wants_checkout
from app.harness.coverage import looks_like_coverage
from app.harness.state import ConversationState

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
    r"hasta\s+que\s+hora\s+puedo\s+pagar|metodo\s+de\s+pago|c[oó]mo\s+pago",
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


def classify_intent(text: str, state: ConversationState | None = None) -> Intent:
    raw = (text or "").strip()
    if not raw:
        return "small_talk"

    state = state or ConversationState()

    if _ESCALATE_RE.search(raw):
        return "escalate"

    if _TRACK_RE.search(raw):
        return "track_order"

    # Cierre en curso: prioridad al FSM
    if state.checkout_step and state.checkout_step not in ("idle", "done", "payment"):
        return "checkout"

    if wants_checkout(raw) or raw.casefold() in ("ese", "esa", "este", "esta"):
        return "checkout"

    # "quiero el panditas" nombra un producto que YA se mostró: es una compra, no
    # una búsqueda nueva. Exigimos referencia explícita (nombre u ordinal): con un
    # solo producto a la vista, "quiero flores" sigue siendo una búsqueda.
    if (
        _BUY_VERB_RE.search(raw)
        and resolve_chosen_product(state, raw, allow_implicit=False) is not None
    ):
        return "checkout"

    if looks_like_coverage(raw) and not _CATALOG_RE.search(raw[:40]):
        return "coverage"

    # Cobertura pura (pregunta de zonas)
    if re.search(r"zonas?\s+cubren|distritos?\s+de\s+lima|llegan\s+a", raw, re.I):
        return "coverage"

    if _DETAIL_RE.search(raw) and state.shown_product_ids:
        return "product_detail"

    if _POLICY_RE.search(raw):
        return "policy_faq"

    if _GREET_RE.match(raw) and len(raw) < 40:
        return "greet"

    if _SMALL_RE.match(raw):
        return "small_talk"

    if _CATALOG_RE.search(raw):
        return "catalog_search"

    # Si hay distrito pendiente y mensaje corto tipo lugar → coverage
    if len(raw) < 60 and looks_like_coverage(raw):
        return "coverage"

    return "catalog_search"
