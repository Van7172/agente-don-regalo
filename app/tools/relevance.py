"""Pedido comercial y relevancia, independientes de la herramienta que elija el LLM."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.harness.taxonomy import RECIPIENT_WORDS, _RECIPIENT_WORDS

CATEGORY_HINTS = (
    (("desayuno", "desayunos", "brunch", "media manana"), "desayunos"),
    (("terrario", "terrarios"), "terrarios"),
    (("suculenta", "suculentas", "macetero", "maceteros"), "plantas"),
    (("peluche", "peluches"), "peluches"),
    (("planta", "plantas"), "plantas"),
    (("cesta", "cestas", "canasta", "canastas"), "cestas"),
    (("bebe", "nacimiento", "baby"), "regalo-para-bebe"),
    (("flor", "flores", "ramo", "ramos", "rosas", "girasol", "tulipan", "arreglo floral"), "arreglos-florales"),
)


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def stem(word: str) -> str:
    word = word[:-1] if len(word) > 4 and word.endswith("s") else word
    return word[:-1] if len(word) > 5 and word.endswith(("o", "a")) else word


def infer_category(text: str) -> str:
    tokens = set(norm(text).split())
    for words, slug in CATEGORY_HINTS:
        if any(set(word.split()) <= tokens for word in words):
            return slug
    return ""


_FILLER = frozenset("de del la el lo los las un una unos unas para con sin y o mi mis su sus que en al por yo me es se a quiero quisiera busco necesito tienen tienes hay hola buen buenos dia dias favor porfavor porfa informacion info ver muestra muestrame ensename manda mandame puedes puede cual cuales cuanto cuesta cuestan esta estan vale valen precio precios tiene sale seria regalo regalos detalle detalles producto productos opcion opciones algo cosa cosas bonito bonita lindo linda hermoso hermosa especial romantico romantica elegante economico economica sorpresa sorpresas cumpleanos aniversario felicitacion agradecimiento negocios mujer hombre esposa esposo novia novio mama papa ella el cumple anos manana hoy entrega envio distrito mas otras otros otra otro mismo misma esas esos no si dale disponible disponibles soles sol pen usd dolares presupuesto hasta maximo aprox".split()) | RECIPIENT_WORDS
_CATEGORY_WORDS = frozenset(word for words, _ in CATEGORY_HINTS for phrase in words for word in phrase.split())
_CORRECTION_FILLER = frozenset("queria referia dije pedi ese esa esos esas era sino realmente".split())
_MORE = re.compile(r"\b(mas|otras?|otros?)\b|\b(no esas|no esos|no lo mismo)\b")


def wants_more(text: str) -> bool:
    return bool(_MORE.search(norm(text)))


def recipient(text: str) -> str:
    tokens = set(norm(text).split())
    found = {key for words, key in _RECIPIENT_WORDS if key in tokens or tokens.intersection(words)}
    if re.search(r"\bpara el$", norm(text)) or re.search(r"\bpara\s+él\b", text.casefold()):
        found.add("hombre")
    return next(iter(found)) if len(found) == 1 else ""


def recipient_conflict(product: dict, target: str) -> bool:
    # Solo evidencias explícitas del nombre; una descripción puede mencionar
    # múltiples destinatarios y un regalo neutro sigue siendo válido.
    name = norm(product.get("nombre") or product.get("nombre_producto"))
    male = bool(re.search(r"\b(para hombre|papa|padre|abuelo)\b|\bpara el$", name))
    female = bool(re.search(r"\b(para (mujer|ella)|mama|madre|abuela)\b", name))
    return (target == "mujer" and male and not female) or (target == "hombre" and female and not male)


@dataclass(frozen=True)
class CatalogRequest:
    text: str
    category: str
    terms: tuple[str, ...]
    recipient: str
    literal_query: str = ""

    @property
    def specific(self) -> bool:
        return bool(self.terms and (self.category or len(self.terms) >= 2))

    @property
    def query(self) -> str:
        return self.literal_query or " ".join(self.terms)


def parse_request(text: str, category: str = "") -> CatalogRequest:
    clean = norm(text)
    # Separar logística/precio evita buscar teléfonos, direcciones o importes.
    core = re.split(r"\b(?:para (?:hoy|manana|el (?:lunes|martes|miercoles|jueves|viernes|sabado|domingo))|delivery|entrega|envio|presupuesto)\b", clean)[0]
    terms = tuple(dict.fromkeys(w for w in core.split() if len(w) >= 3 and w.isalpha() and w not in _FILLER and w not in _CATEGORY_WORDS and w not in _CORRECTION_FILLER))
    literal = " ".join(w for w in core.split() if w in terms or w in _CATEGORY_WORDS)
    return CatalogRequest(clean[:300], infer_category(clean) or category, terms[:10], recipient(clean), literal[:200])


def continue_query(text: str, previous: str) -> str:
    current = parse_request(text)
    if previous and not current.category and current.terms and re.search(r"\b(no|queria|referia|dije|pedi)\b", norm(text)):
        return f"{parse_request(previous).category} {text}"[:300]
    if previous and not current.category and not current.terms and (
        wants_more(text) or recipient(text) or any(w in norm(text) for w in ("cumpleanos", "aniversario"))
    ):
        if current.recipient:
            previous = " ".join(w for w in norm(previous).split() if w not in RECIPIENT_WORDS and w not in {"mujer", "hombre", "ella"})
        return f"{previous} {text}"[:300]
    return text[:300]


def product_text(product: dict) -> str:
    return norm(" ".join(str(product.get(k) or "") for k in (
        "nombre", "nombre_producto", "categoria", "categoria_slug", "descripcion_corta", "descripcion", "url"
    )))


def _preference_score(product: dict, request: CatalogRequest) -> int:
    name = norm(product.get("nombre") or product.get("nombre_producto"))
    score = 0
    if request.recipient == "mujer" and re.search(r"\bpara (mujer|ella)\b", name):
        score += 4
    if request.recipient == "hombre" and re.search(r"\bpara hombre\b|\bpara el$", name):
        score += 4
    for occasion in ("cumpleanos", "aniversario", "agradecimiento", "nacimiento"):
        if occasion in request.text:
            score += 3 if occasion in name else 1 if occasion in product_text(product) else 0
    return score


def select_products(products: list[dict], request: CatalogRequest, *, limit: int | None = None) -> tuple[list[dict], bool]:
    safe = [p for p in products if isinstance(p, dict) and not recipient_conflict(p, request.recipient)]
    if not request.terms:
        safe.sort(key=lambda p: _preference_score(p, request), reverse=True)
        return safe[:limit] if limit else safe, False
    wanted = {stem(t) for t in request.terms}
    ranked = []
    for pos, product in enumerate(safe):
        words = {stem(t) for t in product_text(product).split()}
        hits = len(wanted & words)
        name_words = {stem(t) for t in norm(product.get("nombre") or product.get("nombre_producto")).split()}
        ranked.append((hits == len(wanted), hits, len(wanted & name_words), _preference_score(product, request), -pos, product))
    ranked.sort(key=lambda row: row[:5], reverse=True)
    exact = [row[-1] for row in ranked if row[0]]
    # Si faltan características, conservar alternativas tipadas para que la
    # salida las anuncie como tales; nunca afirmar que son coincidencias.
    chosen = exact or [row[-1] for row in ranked]
    return chosen[:limit] if limit else chosen, bool(chosen and not exact)


def rank_candidates(products: list[dict], text: str) -> list[dict]:
    request = parse_request(text)
    selected, _ = select_products(products, request)
    return selected
