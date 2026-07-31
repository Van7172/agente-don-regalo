"""Guardrails de conversación: funciones puras `(entrada) → Decision`.

Protegen la clasificación de cortesía, los handoffs y la deduplicación de
productos. No dependen del LLM, la red ni el canal.
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
#
# Ojo con lo que faltaba aquí. Un cliente escribió "Cuánto está hello Kitty" y
# el modelo llamó a `escalar_a_humano`: esta lista no traía NI UNA palabra de
# precio ni la palabra "peluche" —una de las siete categorías padre—, así que
# `handoff_policy` no lo reconoció como venta, cayó en el `allow=True` por
# defecto y le cedió el chat a un humano que nadie había pedido. Preguntar el
# precio de un producto es el mensaje más comercial que existe.
#
# `test_handoff_no_se_come_una_venta.py` comprueba que sigue cubriendo las
# categorías de la taxonomía REAL, para que no vuelva a desincronizarse.
_SALES_CONTINUE_RE = re.compile(
    r"corporativ|empresa|b2b|mayorista|colegio|instituci|"
    r"recuerdo|exposici|fiestas?\s+patrias|patrias|"
    r"cantidad|unidades|docena|presupuesto|cotizaci|"
    r"cat[aá]logo|en\s+su\s+p[aá]gina|en\s+la\s+p[aá]gina|"
    # Precio: preguntar cuánto cuesta algo es querer comprarlo.
    r"cu[aá]nto|cu[aá]nta|precio|cuesta|valor|"
    r"desayuno|cesta|suculenta|arreglo|girasol|rosa|ramo|floral|"
    # Las siete categorías padre y sus hijas más nombradas.
    r"peluche|planta|orqu[ií]dea|terrario|beb[eé]|canasta|"
    r"corona|cruz|l[aá]grima|manto|f[uú]nebre|"
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

    return is_courtesy_text(raw)


def is_courtesy_text(text: str) -> bool:
    """¿El texto es SOLO cortesía ("gracias", "ok listo", "👍", "jaja")?

    Mismo vocabulario que `is_small_talk`, pero sobre un texto suelto: el cierre
    lo necesita para no tratar un "Gracias" como si fuera la fecha que pidió.
    """
    norm = _normalize(text or "")
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


def customer_asked_for_human(messages: list) -> bool:
    """¿Lo pidió el CLIENTE, o se lo inventó el modelo?

    Mismo vocabulario que usa `handoff_policy` para forzar la derivación: pedir
    un asesor, hablar de un pago o de una cancelación. Sirve para distinguir una
    escalada solicitada de una escalada por rendición.
    """
    raw = latest_user_text(messages) or ""
    return bool(_HANDOFF_FORCE_RE.search(raw))


def empty_search_is_not_a_handoff(
    messages: list, *, tools_used: list | tuple, found_products: bool
) -> Decision:
    """Buscar y no encontrar NO es motivo para ceder el chat.

    Una clienta preguntó "Cuánto está hello Kitty". El `q` de la API es una
    coincidencia literal de la frase entera: no encontró nada (aunque el
    catálogo tiene *Peluche Kitty Sunshine* a $28) y el modelo se rindió y llamó
    a `escalar_a_humano`. Un catálogo que no responde a la primera es lo normal;
    ceder el chat por eso es regalar la venta.

    Se veta SOLO si la búsqueda volvió vacía. Si encontró productos y el modelo
    escala igual, el motivo es otro —personalización, un reclamo— y ahí la
    derivación sí puede ser legítima; decide `handoff_policy`.
    """
    if found_products:
        return Decision(allow=True)
    if not any(t in _SEARCH_TOOLS for t in (tools_used or ())):
        return Decision(allow=True)
    if customer_asked_for_human(messages):
        return Decision(allow=True)
    return Decision(
        allow=False,
        reason=(
            "La búsqueda no devolvió productos, y eso NO es motivo para derivar. "
            "Dile con franqueza que no encontraste ese producto exacto y ofrécele "
            "alternativas reales del catálogo: prueba otra búsqueda más corta, la "
            "categoría que corresponda o `productos_destacados`. Nunca inventes "
            "productos ni prometas un asesor."
        ),
    )


# Tools que consultan catálogo. Si corrió alguna y no salió ni un producto, el
# turno se quedó sin nada que enseñar — que es cuando el modelo se rinde.
_SEARCH_TOOLS = frozenset({
    "buscar_productos",
    "buscar_semantico",
    "catalogo_categoria",
    "productos_similares",
    "productos_destacados",
    "productos_por_ocasion",
    "productos_oferta",
})


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
