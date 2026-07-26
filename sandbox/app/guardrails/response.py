"""Guardrails de salida: lo que una respuesta del bot NUNCA debe hacer.

Cada regla de aquí nació de un incidente real en producción. Son funciones puras
sobre `(estado, respuesta, artifacts)`, así que sirven para dos cosas a la vez:

- en runtime, el orquestador las evalúa y deja la violación en el log/traza;
- en los evals, el corpus las aplica a conversaciones reales grabadas.

Antes, cada incidente se arreglaba con una regex nueva al lado de la anterior y
nadie sabía si el parche de hoy rompía el de la semana pasada. Esto es lo que
convierte esos parches en una red de regresión.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.harness.contracts import Product
from app.harness.quoting import internal_leak
from app.harness.render import render_product_list
from app.harness.state import ConversationState

# Reconoce una URL de imagen dentro de una línea (igual que el emisor de WhatsApp).
_IMG_URL = re.compile(r"https?://[^\s<>\"']+\.(?:jpe?g|png|webp|gif)", re.I)

# Medios de pago que NO existen en Don Regalo. El bot llegó a preguntar
# "¿prefieres pagar en línea (tarjeta/PSE) o contra entrega?": ninguna de las dos.
_FAKE_PAYMENT = re.compile(
    r"contra\s*entrega|contraentrega|contra\s*reembolso|"
    r"pago\s+en\s+efectivo\s+al\s+recibir|efectivo\s+al\s+recibir|"
    r"pagar\s+al\s+repartidor|\bPSE\b|\bNequi\b|\bDaviplata\b",
    re.I,
)

_PRICE_SOL = re.compile(r"S/\s?(\d+(?:[.,]\d{1,2})?)")


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formato de log
        return f"{self.rule}: {self.detail}"


@dataclass(frozen=True)
class GuardrailResult:
    """Contrato de la barrera de salida antes de enviar al canal."""

    reply: str | None
    violations: tuple[Violation, ...] = ()
    blocked: bool = False


def no_repeated_products(state: ConversationState, artifacts: list[Product]) -> Violation | None:
    """Un producto ya mostrado no se vuelve a mostrar.

    El cliente lo lee como que no le hicimos caso: "otras, no esas".
    """
    ya_vistos = set(state.shown_product_ids or [])
    repetidos = sorted({p.id_producto for p in artifacts if p.id_producto in ya_vistos})
    if repetidos:
        return Violation("no_repeated_products", f"ids ya mostrados: {repetidos}")
    return None


def no_duplicates_within_reply(artifacts: list[Product]) -> Violation | None:
    """Ni siquiera dos veces dentro del mismo paquete."""
    vistos: set[int] = set()
    for p in artifacts:
        if p.id_producto in vistos:
            return Violation("no_duplicates_within_reply", f"id {p.id_producto} duplicado")
        vistos.add(p.id_producto)
    return None


def no_cash_on_delivery(reply: str) -> Violation | None:
    """Don Regalo cobra por adelantado. No hay contraentrega, ni PSE (es colombiano)."""
    m = _FAKE_PAYMENT.search(reply or "")
    if m:
        return Violation("no_cash_on_delivery", f"ofrece un medio inexistente: {m.group(0)!r}")
    return None


def image_urls_on_own_line(reply: str) -> Violation | None:
    """La URL de imagen va sola en su línea, o llega al cliente como link, no como foto."""
    for line in (reply or "").split("\n"):
        m = _IMG_URL.search(line)
        if not m:
            continue
        resto = (line[: m.start()] + line[m.end():]).strip()
        if resto:
            return Violation(
                "image_urls_on_own_line",
                f"URL pegada al texto {resto[:40]!r}: llegará como link",
            )
    return None


def no_internal_context(reply: str) -> Violation | None:
    """Ningún marcador del sistema sale al cliente. Es un fallo NUESTRO.

    Nació de una respuesta real: *No ubico “[El cliente está respondiendo al
    mensaje: «Brunch de Feliz Cumpleaños modificado»” en nuestra lista*. El
    marcador de la cita se coló entero en la burbuja, en el turno en que la
    clienta estaba cerrando la compra. Cubre también el texto saneado por
    seguridad, las etiquetas de PII y los stubs de media (`[image]`).

    Si esto salta, el turno está roto: el orquestador lo deriva a un humano en
    vez de enseñarle al cliente nuestra tubería.
    """
    leak = internal_leak(reply or "")
    if leak:
        return Violation("no_internal_context", f"marcador interno en la respuesta: {leak[:60]!r}")
    return None


def prices_are_sourced(reply: str, artifacts: list[Product]) -> Violation | None:
    """Todo precio en soles debe venir de una tool, no del modelo.

    Es el fallo más caro del negocio: un precio inventado que el cliente da por
    bueno. Si el turno no citó productos, no hay nada contra qué comparar.
    """
    if not reply or not artifacts:
        return None

    respaldados = {
        f"{float(v):.2f}"
        for p in artifacts
        for v in (p.precio_sol,)
        if v is not None
    }
    if not respaldados:
        return None

    for m in _PRICE_SOL.finditer(reply):
        citado = f"{float(m.group(1).replace(',', '.')):.2f}"
        if citado not in respaldados:
            return Violation(
                "prices_are_sourced",
                f"S/{m.group(1)} no salió de ninguna tool de este turno",
            )
    return None


def check_reply(
    reply: str | None,
    *,
    state: ConversationState | None = None,
    artifacts: list[Product] | None = None,
) -> list[Violation]:
    """Todas las invariantes aplicables a una respuesta. Lista vacía = limpia."""
    reply = reply or ""
    artifacts = artifacts or []
    state = state or ConversationState()

    candidatas = [
        no_cash_on_delivery(reply),
        no_internal_context(reply),
        image_urls_on_own_line(reply),
        prices_are_sourced(reply, artifacts),
        no_repeated_products(state, artifacts),
        no_duplicates_within_reply(artifacts),
    ]
    return [v for v in candidatas if v is not None]


_BLOCKING_RULES = frozenset(
    {"prices_are_sourced", "no_cash_on_delivery", "no_internal_context"}
)

SAFE_FALLBACK = (
    "Para no darte un dato equivocado, prefiero confirmártelo bien 🙏 "
    "El pago es siempre por adelantado (Yape/Plin, transferencia bancaria o "
    "tarjeta). ¿Te comparto los precios exactos del regalo que te interesa?"
)

# Fallo técnico: ni se explica ni se promete nada. Lo normal es que el
# orquestador ya esté derivando a un humano (`master`); esto es lo que queda
# cuando no hay conversación a la que ceder el chat.
SAFE_TECHNICAL_FALLBACK = (
    "Perdón, se me cruzó un cable con ese mensaje 😅 "
    "¿Me lo cuentas otra vez en una línea y lo vemos al toque?"
)


def sanitize_reply(
    reply: str | None,
    violations: list[Violation],
    artifacts: list[Product],
) -> str | None:
    """Bloquea prosa insegura y conserva únicamente datos respaldados.

    Las reglas informativas se registran en la traza. Un precio inventado o un
    medio de pago inexistente no puede salir al cliente: con productos se
    reconstruye el listado desde artifacts; sin ellos se usa un respaldo fijo.

    Un marcador interno es distinto: no es la prosa lo que está mal, es que
    perdimos el hilo del turno. Ahí no se conserva nada de lo redactado.
    """
    broken = {violation.rule for violation in violations} & _BLOCKING_RULES
    if not broken:
        return reply
    if "no_internal_context" in broken:
        return SAFE_TECHNICAL_FALLBACK
    if artifacts:
        return render_product_list([_product_dict(product) for product in artifacts])
    return SAFE_FALLBACK


def guard_reply(
    reply: str | None,
    *,
    state: ConversationState | None = None,
    artifacts: list[Product] | None = None,
) -> GuardrailResult:
    """Fachada runtime: evaluar y sanear una respuesta en una sola operación."""
    products = artifacts or []
    violations = check_reply(reply, state=state, artifacts=products)
    safe = sanitize_reply(reply, violations, products)
    return GuardrailResult(
        reply=safe,
        violations=tuple(violations),
        blocked=safe != reply,
    )


def _product_dict(product: Product) -> dict:
    return {
        "id_producto": product.id_producto,
        "nombre": product.nombre,
        "precio_sol": product.precio_sol,
        "precio_usd": product.precio_usd,
        "imagen_url": product.imagen_url,
        "descripcion_corta": product.descripcion,
    }
