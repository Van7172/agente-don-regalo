"""Políticas del harness: funciones puras `(entrada) → Decision`.

Estas son reglas de negocio, no de inferencia. Vivían dentro del loop del LLM en
`services/agent.py`, lo que obligaba a montar un mock de OpenAI para testear algo
tan simple como "no escales una venta corporativa". Aquí se testean en
milisegundos y sin red.
"""
from __future__ import annotations

import re
import unicodedata

from app.harness.contracts import Decision, Product

# ── Percepción del turno ──────────────────────────────────────────────

_GREETING_RE = re.compile(
    r"^(?:"
    r"h+o+l+a+s?|holis|"
    r"buenas?|"
    r"buen[oa]s?\s+d[ií]as?|"
    r"buenas?\s+tardes?|"
    r"buenas?\s+noches?|"
    r"qu[eé]\s+tal|"
    r"c[oó]mo\s+est[aá]s?|"
    r"hey|hi|hello|saludos?"
    r")"
    r"(?:\s+(?:a\s+todos?|amigo|amiga|equipo|don\s*regalo))?$"
)

# Vocabulario de cortesía: "ok gracias", "todo en orden hoy", "jaja", "👍"…
# Un mensaje formado SOLO por estas palabras no pide nada, así que no hay nada
# que escalar. Lista blanca corta a propósito: preferimos dejar pasar charla
# trivial a suprimir una escalación de verdad.
_SMALL_TALK_WORDS = frozenset(
    """
    gracias muchas mil ok oka okay okey vale listo perfecto genial excelente
    buenisimo entiendo entendido claro dale bueno buena ya ah aja si no nada
    todo bien en orden correcto estamos igualmente a ti usted de acuerdo por
    ahora hoy amigo amiga saludos chevere tranquilo tranquila
    """.split()
)

# Si el cliente pide esto, el handoff SÍ procede aunque también diga "corporativo".
_HANDOFF_FORCE_RE = re.compile(
    r"asesor|humano|persona|atenci[oó]n\s+humana|p[aá]same\s+con|"
    r"comprobante|ya\s+pagu|transfer[ií]|"
    # Con raíz: el cliente escribe "Cancelo el pedido", no "cancelar".
    r"descuento|\bcancel\w*|\banul\w*|modificar\s+(el\s+)?pedido|"
    r"mala\s+atenci|no\s+me\s+ayud|quiero\s+hablar\s+con",
    re.IGNORECASE,
)

# Contexto de venta en curso: el bot debe seguir preguntando, no escalar.
_SALES_CONTINUE_RE = re.compile(
    r"corporativ|empresa|b2b|mayorista|colegio|instituci|"
    r"recuerdo|exposici|fiestas?\s+patrias|patrias|"
    r"cantidad|unidades|docena|presupuesto|cotizaci|"
    r"cat[aá]logo|en\s+su\s+p[aá]gina|en\s+la\s+p[aá]gina|"
    r"desayuno|cesta|suculenta|arreglo|girasol|rosa|ramo|floral|"
    r"disponib|stock|horario|distrito|delivery|entrega|"
    r"tarjeta|visa|mastercard|paypal|"
    r"reserv[aoe]|me\s+gusta\s+est|elijo|escoger|"
    r"\d+\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)|"
    r"\b\d+\s*(?:y|,|/|&)\s*\d+\b|\by\s*\d+\b",
    re.IGNORECASE,
)

_MEDIA_ONLY_RE = re.compile(r"\[(?:image|video|audio|document|sticker)\]", re.I)

_PAYMENT_RE = re.compile(r"pago|comprobante|yape|plin|transfer|tarjeta", re.I)


def latest_user_text(messages: list) -> str | None:
    """Último texto del cliente. `None` si el turno fue solo una imagen."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("image_url", "image"):
                    return None
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
            joined = "\n".join(t for t in texts if t).strip()
            return joined or None
        return None
    return None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[^\w\sáéíóúüñ]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def is_simple_greeting(messages: list) -> bool:
    raw = latest_user_text(messages)
    if not raw or len(raw) > 80:
        return False
    norm = _normalize(raw)
    if not norm or len(norm) > 60:
        return False
    return bool(_GREETING_RE.match(norm))


def is_small_talk(messages: list) -> bool:
    """True si el último mensaje del cliente es cortesía o charla sin pedido."""
    raw = latest_user_text(messages)
    if not raw or len(raw) > 80 or "?" in raw:
        return False

    if is_simple_greeting(messages):
        return True

    norm = _normalize(raw)
    if not norm:
        # Se quedó vacío al normalizar: era solo emojis ("👍", "😊").
        return True

    tokens = norm.split()
    if not tokens or len(tokens) > 6:
        return False

    # "jaja", "jejeje", "jjj"… son risas, no un pedido.
    return all(
        t in _SMALL_TALK_WORDS or re.fullmatch(r"(?:ja|je|ji|ha)+|j+", t)
        for t in tokens
    )


# ── Política de handoff ───────────────────────────────────────────────


def handoff_policy(messages: list) -> Decision:
    """¿Procede escalar a un humano en este turno?

    El modelo escala de más: manda a un asesor ventas sanas ("regalos
    corporativos por Fiestas Patrias", "quiero la 2 y la 3") y hasta cortesía.
    Cada handoff falso deja a un cliente esperando a alguien que no hace falta.
    """
    if is_small_talk(messages):
        return Decision(
            allow=False,
            reason=(
                "El cliente no está pidiendo nada: es cortesía o charla suelta. "
                "No se escala. Responde tú, corto y cálido, y deja la puerta abierta."
            ),
        )

    raw = latest_user_text(messages)
    if not raw:
        return Decision(allow=True)

    # Solo media (sin texto útil): seguir vendiendo, no escalar por "vacío".
    if _MEDIA_ONLY_RE.fullmatch(raw.strip()):
        return Decision(
            allow=False,
            reason=(
                "El cliente envió solo un archivo/media. Identifica el producto "
                "(nombre en la captura si hay visión) o pregunta a qué se refiere; "
                "NO escales."
            ),
        )

    # Pedido explícito de humano o de pago: manda por encima de todo lo demás.
    if _HANDOFF_FORCE_RE.search(raw):
        return Decision(allow=True)

    if _SALES_CONTINUE_RE.search(raw):
        return Decision(
            allow=False,
            reason=(
                "El cliente sigue en un flujo de venta (producto, corporativo, "
                "catálogo, campaña o eligiendo opciones). NO escalas: pregunta "
                "cantidad, presupuesto, distrito o fecha, o muestra productos con "
                "las tools. Solo escala si pide asesor, pago/comprobante, descuento "
                "o cancelación."
            ),
        )

    return Decision(allow=True)


def should_discard_handoff(messages: list) -> bool | str:
    """Compatibilidad con el call site del loop: `False` o el motivo del rechazo."""
    decision = handoff_policy(messages)
    return False if decision.allow else decision.reason


def is_payment_reason(motivo: str) -> bool:
    return bool(_PAYMENT_RE.search(motivo or ""))


# ── Política de deduplicación ─────────────────────────────────────────


def dedupe_artifacts(shown_ids: list[int], artifacts: list[Product]) -> list[Product]:
    """Quita los productos ya mostrados antes y los repetidos dentro del turno."""
    seen = set(shown_ids or [])
    out: list[Product] = []
    for product in artifacts:
        if product.id_producto in seen:
            continue
        seen.add(product.id_producto)
        out.append(product)
    return out


# ── Política de grounding ─────────────────────────────────────────────

_PRICE_RE = re.compile(r"S/\s?(\d+(?:[.,]\d{1,2})?)")


def grounding_violation(reply: str, artifacts: list[Product]) -> str | None:
    """Detecta precios en la respuesta que ninguna tool respaldó.

    Es el fallo más caro del negocio: un precio inventado que el cliente da por
    bueno. Si el turno no citó ningún producto, no hay nada contra qué comparar
    y no opinamos.
    """
    if not reply or not artifacts:
        return None

    sourced: set[str] = set()
    for product in artifacts:
        for value in (product.precio_sol, product.precio_usd):
            if value is not None:
                sourced.add(f"{float(value):.2f}")

    if not sourced:
        return None

    for match in _PRICE_RE.finditer(reply):
        quoted = f"{float(match.group(1).replace(',', '.')):.2f}"
        if quoted not in sourced:
            return (
                f"El precio S/{match.group(1)} no vino de ninguna herramienta en "
                "este turno."
            )
    return None
