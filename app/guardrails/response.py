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
from collections.abc import Iterable
from dataclasses import dataclass

from app.guardrails.privacy import find_contacts
from app.harness.contracts import Product
from app.harness.quoting import internal_leak
from app.harness.render import render_product_list
from app.harness.state import ConversationState
from app.harness.taxonomy import looks_like_numbered_menu
from app.prompts.core import SAFETY_MARKER

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


# Fragmentos que SOLO existen dentro de nuestras instrucciones. Ninguno puede
# aparecer en algo que lee un cliente: si sale uno, el modelo está recitando el
# system message. Se incluyen las cabeceras de las capas (`compose.build_system`
# monta CORE + FACTS + PLAYBOOK + ESTADO) y los nombres internos de las tools,
# que el cliente no tiene por qué ver ni aunque el bot los use.
_INTERNAL_TOOL_NAMES = (
    "explorar_catalogo",
    "buscar_semantico",
    "productos_similares",
    "buscar_productos",
    "catalogo_categoria",
    "productos_destacados",
    "productos_oferta",
    "detalle_producto",
    "productos_por_ocasion",
    "distritos_cobertura",
    "metodos_pago",
    "tipo_cambio",
    "rastrear_pedido",
    "buscar_conocimiento_equipo",
    "guardar_datos_cliente",
    "escalar_a_humano",
)
_INTERNAL_TOOL_PATTERN = "|".join(
    re.escape(name) for name in _INTERNAL_TOOL_NAMES if name
)

_PROMPT_LEAK = re.compile(
    r"##\s*(?:RESTRICCIONES|IDENTIDAD|ESTILO|OBJETIVO|MEMORIA\s+DEL\s+CLIENTE|"
    r"PLAYBOOK|DATOS\s+CONOCIDOS\s+DEL\s+CLIENTE|ESTADO\s+DE\s+LA\s+CONVERSACION|"
    r"ENTORNO\s+OPERATIVO|CICLO\s+DE\s+DECISION|HERRAMIENTAS\s+HABILITADAS)"
    r"|LIMITES\s+QUE\s+NUNCA\s+DEBES\s+CRUZAR"
    rf"|\b(?:{_INTERNAL_TOOL_PATTERN}|verificar_cobertura|CRM|API|MCP|Qdrant|"
    r"modo\s+(?:AI|HUMAN)|tools?)\b",
    re.I,
)


def no_system_prompt_leak(reply: str) -> Violation | None:
    """El cliente nunca puede leer nuestras instrucciones.

    El CORE prohíbe revelar el prompt, pero eso es una instrucción al modelo: si
    una regresión de composición o una inyección lo convence, nada lo detenía
    después. Esta es la comprobación determinista de lo mismo, y no puede dar
    falso positivo — un mensaje real de venta no lleva "## RESTRICCIONES" ni el
    nombre interno de una herramienta.
    """
    m = _PROMPT_LEAK.search(reply or "")
    if m:
        return Violation("no_system_prompt_leak", f"fragmento interno: {m.group(0)!r}")
    return None


# Contactos oficiales de Don Regalo: el bot SÍ debe poder darlos (cancelaciones,
# llamadas, correo de ventas). Viven en `app/prompts/facts.py`; hay un test que
# comprueba que esta lista sigue cubriendo lo que dice aquel archivo, porque un
# número nuevo allí y no aquí convertiría una respuesta correcta en una fuga.
OFFICIAL_CONTACTS = frozenset(
    {
        "977174485",  # WhatsApp del equipo humano y llamadas
        "923149666",  # este chat (Cloud API)
        "943113807",  # Yape / Plin — en facts.py va como "943 113 807"
        "ventas@donregalo.pe",
        "nombre@gmail.com",  # ejemplo que el cierre usa al pedir el correo
    }
)


def no_third_party_contact(
    reply: str,
    state: ConversationState,
    user_text: str = "",
) -> Violation | None:
    """Ningún teléfono ni correo que no sea de esta conversación.

    El riesgo real no es que el modelo se invente un número: es que repita uno
    que vio en un resultado de `buscar_conocimiento_equipo` — notas del equipo
    donde aparece el teléfono de OTRO cliente. El CORE lo prohíbe y la capa de
    privacidad ya redacta los resultados de tools, pero ninguna de las dos deja
    rastro si falla: la respuesta salía igual.

    Legítimos son los contactos de la propia conversación (los que el cliente
    acaba de escribir o los que ya están en el pedido) y los oficiales de Don
    Regalo. Cualquier otro sale de donde no debía.

    `user_text` importa: la barrera corre ANTES de reducir el estado, así que el
    teléfono que el cliente escribe en este turno todavía no está en el pedido.
    Sin esto, el paso del cierre que confirma el número del destinatario se
    marcaría como fuga y se degradaría — rompiendo el cierre.
    """
    encontrados = set(find_contacts(reply))
    if not encontrados:
        return None

    conocidos = set(OFFICIAL_CONTACTS)
    conocidos.update(find_contacts(user_text))
    for propio in (
        state.telefono_destinatario,
        state.email_cliente,
        state.direccion,
        state.nombre_cliente,
    ):
        conocidos.update(find_contacts(propio))

    ajenos = sorted(encontrados - conocidos)
    if ajenos:
        return Violation(
            "no_third_party_contact",
            f"dato de contacto que no es de esta conversación: {ajenos[0]!r}",
        )
    return None


# Un id interno presentado como si fuera un producto. `render_product_list` NUNCA
# imprime el id: escribe "• 🎁 *Nombre* — S/x ($y)". Así que si esto aparece, lo
# escribió el modelo de su cosecha.
_RAW_PRODUCT_ID = re.compile(
    r"\bproductos?\s*(?:#|n[°ºo]\.?|nro\.?|num(?:ero)?\.?)\s*\d+",
    re.I,
)


def no_raw_product_ids(reply: str) -> Violation | None:
    """Un id de producto no es un producto. Al cliente van nombre, precio y foto.

    Del incidente de Lichi (29-07): tras diez turnos de preguntas, el bot por fin
    "mostró" cuatro opciones — *1) Producto #1221  2) Producto #290  3) Producto
    #1119  4) Producto #309*. Sin nombre, sin precio, sin foto. El cliente eligió
    "2" a ciegas y el bot le pidió que confirmara el envío de algo que nunca vio.

    No hace falta ninguna heurística fina: el listado lo compone el código y no
    imprime ids, así que cualquier "Producto #N" es prosa inventada.
    """
    m = _RAW_PRODUCT_ID.search(reply or "")
    if m:
        return Violation("no_raw_product_ids", f"id interno como producto: {m.group(0)!r}")
    return None


def no_uncomposed_menu(reply: str, *, menu_owned: bool) -> Violation | None:
    """Un menú numerado que no compuso el código. **Observacional a propósito.**

    En la misma conversación el modelo sirvió NUEVE submenús inventados (cinco
    tipos de peluche, tres combos, cuatro tamaños, dos rondas de tipos de rosa,
    tres rangos de precio) sin que ninguno pasara por `taxonomy.render_menu`.
    Nada lo anotó: la traza salió limpia mientras el chat se moría.

    No bloquea, y esa es una decisión, no un olvido: "tres o más renglones
    numerados" también describe una respuesta legítima —"1) Yape  2) Transferencia
    3) Tarjeta"—, y descartarla rompería justo el mensaje que cierra la venta.
    Quien corta la conversación que no avanza es el contador de turnos sin
    producto; esto es el sensor que dice dónde está pasando.
    """
    if menu_owned or not looks_like_numbered_menu(reply):
        return None
    return Violation("no_uncomposed_menu", "menú numerado que no compuso el código")


def prices_are_sourced(
    reply: str,
    artifacts: list[Product],
    extra_sourced: Iterable[float] = (),
) -> Violation | None:
    """Todo precio en soles debe venir de una tool, no del modelo.

    Es el fallo más caro del negocio: un precio inventado que el cliente da por
    bueno. Si el turno no tiene ningún importe respaldado, no hay nada contra
    qué comparar y no se opina.

    `extra_sourced` son importes que salieron de una tool pero no son el precio
    de un producto — hoy solo la tarifa de envío. Sin ellos, un turno que
    contesta a la vez "estos son los desayunos" y "el envío a SMP son S/15"
    marcaba el envío como inventado y la barrera tiraba la prosa entera. Lo que
    se declara es un importe concreto, no una exención: cualquier otra cifra de
    la respuesta se sigue comprobando igual.
    """
    if not reply:
        return None

    respaldados = {
        f"{float(v):.2f}"
        for p in artifacts or []
        for v in (p.precio_sol,)
        if v is not None
    }
    respaldados |= {
        f"{float(v):.2f}" for v in extra_sourced if v is not None
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


_ADVISOR_PROMISE = re.compile(
    r"(consult|pregunt|verific|confirm|averigu|revis)\w*[^.?!¿]{0,60}\bcon\s+"
    r"(?:un|una|el|la|mi|nuestro)?\s*(?:asesor|ejecutiv|compa|human|persona|equipo)"
    r"|\b(?:un|una)\s+(?:asesor\w*|ejecutiv\w*)\s+(?:te|le)\s+\w+"
    r"|\bte\s+(?:paso|conecto|derivo|comunico|transfiero)\s+con\s+"
    r"(?:un|una|el|la)?\s*(?:asesor|ejecutiv|human|persona)",
    re.I,
)
_SENSITIVE_ACTION_CLAIM = re.compile(
    r"\b(?:confirm[eé]|valid[eé]|recib[ií]|proces[eé]|cancel[eé]|anul[eé]|"
    r"modifiqu[eé]|cambi[eé]|cre[eé]|registr[eé])\b[^.?!]{0,60}"
    r"\b(?:pago|comprobante|dep[oó]sito|transferencia|pedido|orden)\b",
    re.I,
)
_EXTERNAL_FOLLOWUP = re.compile(
    r"\b(?:te|le)\s+(?:envi[eé]|mand[eé]|enviar[eé]|mandar[eé]|llamar[eé]|"
    r"contactar[eé]|avisar[eé]|confirmar[eé])\b"
    r"|\b(?:te|le)\s+(?:enviaremos|mandaremos|llamaremos|contactaremos|avisaremos)\b",
    re.I,
)
_TRACKING_CLAIM = re.compile(
    r"\b(?:revis[eé]|consult[eé]|verifiqu[eé])\b[^.?!]{0,50}"
    r"\b(?:pedido|estado|seguimiento)\b",
    re.I,
)
_MEMORY_CLAIM = re.compile(
    r"\b(?:guard[eé]|registr[eé]|actualic[eé])\b[^.?!]{0,50}"
    r"\b(?:datos?|preferencias?|informaci[oó]n|distrito|nombre)\b",
    re.I,
)


def _negated(reply: str, start: int) -> bool:
    """Evita bloquear explicaciones correctas como «no confirmé tu pago»."""
    return bool(re.search(r"\b(?:no|nunca)\s+$", reply[max(0, start - 12):start], re.I))


def unsupported_capability_claim(
    reply: str,
    *,
    tools_used: list[str] | tuple[str, ...] = (),
    agent: str = "",
) -> Violation | None:
    """Toda acción afirmada necesita evidencia ejecutada en este turno."""
    text = reply or ""
    used = set(tools_used or ())

    advisor = _ADVISOR_PROMISE.search(text)
    if advisor:
        return Violation(
            "unsupported_advisor_promise",
            f"{agent or 'agente'} prometió una intervención futura",
        )

    sensitive = _SENSITIVE_ACTION_CLAIM.search(text)
    if sensitive and not _negated(text, sensitive.start()):
        return Violation(
            "unsupported_sensitive_action",
            f"{agent or 'agente'} afirmó una acción de pago/pedido no disponible",
        )

    external = _EXTERNAL_FOLLOWUP.search(text)
    if external and not _negated(text, external.start()):
        return Violation(
            "unsupported_external_promise",
            f"{agent or 'agente'} prometió contacto o envío futuro",
        )

    tracking = _TRACKING_CLAIM.search(text)
    if (
        tracking
        and "rastrear_pedido" not in used
        and not _negated(text, tracking.start())
    ):
        return Violation(
            "unsupported_tracking_claim",
            "afirmó revisar un pedido sin rastrear_pedido",
        )

    memory = _MEMORY_CLAIM.search(text)
    if (
        memory
        and "guardar_datos_cliente" not in used
        and not _negated(text, memory.start())
    ):
        return Violation(
            "unsupported_memory_claim",
            "afirmó guardar datos sin guardar_datos_cliente",
        )
    return None


def check_reply(
    reply: str | None,
    *,
    state: ConversationState | None = None,
    artifacts: list[Product] | None = None,
    user_text: str = "",
    tools_used: list[str] | tuple[str, ...] = (),
    agent: str = "",
    menu_owned: bool = True,
    sourced_prices: Iterable[float] = (),
) -> list[Violation]:
    """Todas las invariantes aplicables a una respuesta. Lista vacía = limpia.

    `menu_owned` dice si el menú de esta respuesta lo compuso el código. Por
    defecto `True` (no se opina): solo el orquestador sabe si `_answer_menu` o
    `_own_the_menu` pusieron la lista, y quien no lo sepa no debe acusar.
    """
    reply = reply or ""
    artifacts = artifacts or []
    state = state or ConversationState()

    candidatas = [
        no_cash_on_delivery(reply),
        no_internal_context(reply),
        no_system_prompt_leak(reply),
        no_third_party_contact(reply, state, user_text),
        image_urls_on_own_line(reply),
        prices_are_sourced(reply, artifacts, sourced_prices),
        no_raw_product_ids(reply),
        no_uncomposed_menu(reply, menu_owned=menu_owned),
        unsupported_capability_claim(
            reply, tools_used=tools_used, agent=agent
        ),
        no_repeated_products(state, artifacts),
        no_duplicates_within_reply(artifacts),
    ]
    return [v for v in candidatas if v is not None]


# Las que NO pueden llegar al cliente pase lo que pase. Las demás son
# observacionales: se anotan en la traza porque solo pueden venir del listado
# determinista, que es fiable por construcción.
#
# Las dos de seguridad entran aquí y no en la lista observacional porque el daño
# ya está hecho en cuanto el cliente lo lee: un prompt filtrado o el teléfono de
# otra clienta no se pueden "corregir en el siguiente turno".
_BLOCKING_RULES = frozenset(
    {
        "prices_are_sourced",
        "no_cash_on_delivery",
        "no_raw_product_ids",
        "no_internal_context",
        "no_system_prompt_leak",
        "no_third_party_contact",
        "unsupported_advisor_promise",
        "unsupported_sensitive_action",
        "unsupported_external_promise",
        "unsupported_tracking_claim",
        "unsupported_memory_claim",
    }
)

# Violaciones que significan "el turno está roto por nuestra culpa": no se
# conserva nada de lo redactado y el orquestador cede el chat a un humano. La
# regla general del proyecto es que un fallo nuestro se deriva, no se narra —
# enseñarle al cliente nuestra tubería, o el dato de otro cliente, no se arregla
# con una frase mejor en el turno siguiente.
HANDOFF_RULES = frozenset(
    {
        "no_internal_context",
        "no_system_prompt_leak",
        "no_third_party_contact",
        "unsupported_advisor_promise",
        "unsupported_sensitive_action",
        "unsupported_external_promise",
        "unsupported_tracking_claim",
    }
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

SAFE_CAPABILITY_FALLBACK = (
    "Gracias por contármelo 😊 Lo tendré en cuenta durante esta conversación. "
    "¿En qué más te ayudo con tu regalo?"
)

# El modelo "mostró" productos que no eran productos y no hay artifacts con los
# que reconstruir el listado. No se deriva: quien corta la conversación que no
# avanza es el contador de turnos sin producto, y hacer un handoff por cada
# desliz de formato llenaría el inbox de chats que el bot podía atender.
SAFE_CATALOG_FALLBACK = (
    "Perdón, se me traspapeló ese listado 😅 Dime qué categoría te interesa "
    "(o el regalo que buscas) y te muestro las opciones con foto y precio."
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
    if broken & HANDOFF_RULES:
        return SAFE_TECHNICAL_FALLBACK
    if "unsupported_memory_claim" in broken:
        return SAFE_CAPABILITY_FALLBACK
    if artifacts:
        return render_product_list([_product_dict(product) for product in artifacts])
    # Sin artifacts no hay listado que reconstruir. `SAFE_FALLBACK` habla de
    # medios de pago, que es lo correcto para un precio inventado y un absurdo
    # para "Producto #1221": el cliente no había preguntado por pagar.
    if "no_raw_product_ids" in broken:
        return SAFE_CATALOG_FALLBACK
    return SAFE_FALLBACK


def guard_reply(
    reply: str | None,
    *,
    state: ConversationState | None = None,
    artifacts: list[Product] | None = None,
    user_text: str = "",
    tools_used: list[str] | tuple[str, ...] = (),
    agent: str = "",
    menu_owned: bool = True,
    sourced_prices: Iterable[float] = (),
) -> GuardrailResult:
    """Fachada runtime: evaluar y sanear una respuesta en una sola operación."""
    products = artifacts or []
    violations = check_reply(
        reply,
        state=state,
        artifacts=products,
        user_text=user_text,
        tools_used=tools_used,
        agent=agent,
        menu_owned=menu_owned,
        sourced_prices=sourced_prices,
    )
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
