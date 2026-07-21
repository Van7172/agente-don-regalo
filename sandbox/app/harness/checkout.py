"""Máquina de estados del cierre de pedido (determinista)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any

from app.delivery_windows import SCHEDULE_OPTIONS
from app.delivery_windows import schedule_map_for, schedule_options_for, windows_for
from app.harness.orders import display_fecha, lima_today, normalize_fecha
from app.harness.policies import is_courtesy_text
from app.harness.state import ConversationState

__all__ = [
    "SCHEDULE_OPTIONS",
    "advance_checkout",
    "parse_address",
    "parse_contact",
    "parse_recipient",
    "parse_schedule",
    "recognized_window",
    "resolve_chosen_product",
    "split_name",
    "start_checkout",
    "wants_checkout",
]

_BUY_RE = re.compile(
    r"lo\s+quiero|me\s+lo\s+llevo|quiero\s+(ese|esa|este|esta|comprar)|"
    r"como\s+lo\s+(pido|reservo)|reserv[aoe]|me\s+gusta\s+est|"
    r"si\s+perfecto|dale\s+con\s+ese|ese\s+lo\s+pido",
    re.I,
)


def wants_checkout(text: str) -> bool:
    return bool(_BUY_RE.search(text or ""))


def _label_for_hour(hour: int) -> str | None:
    # En delivery "de 2 a 5" es la tarde, no la madrugada: sin am/pm, las horas
    # de 1 a 6 son PM.
    if 1 <= hour <= 6:
        hour += 12
    if 7 <= hour < 9:
        return "Mañana temprano"
    if 9 <= hour < 11:
        return "Mañana"
    if 11 <= hour < 14:
        return "Mediodía"
    if 14 <= hour < 16:
        return "Tarde"
    if 16 <= hour <= 19:
        return "Tarde-noche"
    return None


# El orden manda: "tarde-noche" antes que "tarde", "mañana temprano" antes que
# "mañana". En este paso la fecha ya está fija, así que "mañana" es la franja
# horaria, no el día.
_LABEL_PATTERNS = (
    ("Mañana temprano", r"temprano|primera\s+hora"),
    ("Mediodía", r"mediod[ií]a|medio\s+dia|al\s+almuerzo"),
    ("Tarde-noche", r"tarde\s*[-/ ]?\s*noche|\bnoche\b|fin\s+de\s+la\s+tarde"),
    ("Tarde", r"\btarde\b"),
    ("Mañana", r"\bmanana\b|\bam\b|por\s+la\s+manana"),
)

# "De 7 a 9", "entre 9 y 11", "7-9": manda la hora de inicio. El separador va con
# `\b` para que la "a" de "9 am" no se lea como el "a" de un rango.
_RANGE_RE = re.compile(
    r"\b(\d{1,2})(?::\d{2})?\s*(?:-|\ba\b|\bal\b|\bhasta\b|\by\b)\s*(\d{1,2})"
)
_AMPM_RE = re.compile(r"\b(\d{1,2})\s*(?::\d{2})?\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)")
_BARE_HOUR_RE = re.compile(r"\b(?:a\s+las\s+|las\s+)?(\d{1,2})\b")


def recognized_window(text: str) -> str | None:
    """Franja que nombra el texto, exista o no ese día; `None` si no se reconoce.

    Separado de `parse_schedule` para distinguir dos fracasos que no son el
    mismo: "no te entendí" y "esa franja no sale ese día". Repetir el menú
    entero valía para los dos, y por eso no servía para ninguno.
    """
    raw = _norm(text)
    if not raw:
        return None

    m = _RANGE_RE.search(raw)
    if m:
        return _label_for_hour(int(m.group(1)))

    m = _AMPM_RE.search(raw)
    if m:
        hour = int(m.group(1))
        if re.search(r"p\.?\s*m", raw) and hour < 12:
            hour += 12
        return _label_for_hour(hour)

    for label, pattern in _LABEL_PATTERNS:
        if re.search(pattern, raw):
            return label

    m = _BARE_HOUR_RE.search(raw)
    if m:
        return _label_for_hour(int(m.group(1)))

    return None


def parse_schedule(
    text: str, delivery_date: date | str | None = None
) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    schedule_map = schedule_map_for(delivery_date)
    if raw[0] in schedule_map and (len(raw) == 1 or not raw[1].isdigit()):
        return schedule_map[raw[0]]

    label = recognized_window(raw)
    if label is None:
        return None
    available = {window.label: window.display for window in windows_for(delivery_date)}
    return available.get(label)


_ASK_RECIPIENT = (
    "¿Para quién es el regalo? 🎁 Escríbeme el *nombre y apellido* del "
    "destinatario y un *teléfono* de contacto por si el motorizado lo necesita."
)
_ASK_ADDRESS = (
    "¿A qué *dirección* lo llevamos? 🏠 Incluye referencias y dime si es una "
    "*casa* o una *oficina*."
)
_ASK_CONTACT = (
    "¡Casi listo! ✨ ¿A nombre de quién va el pedido y a qué *correo* te "
    "enviamos la confirmación? (el teléfono lo tomo de este WhatsApp)"
)

# ── No entender no puede ser un bucle ─────────────────────────────────
#
# La FSM era una función pura de (paso, texto): cuando no entendía devolvía los
# mismos bytes, y los devolvía indefinidamente. Una clienta recibió cuatro veces
# "No pude confirmar esa fecha" y otras cuatro el menú de horarios completo —
# incluso al escribir "gracias" y "ya no deseo el pedido por q no entienden".
# Se fue. Tres reglas salieron de ahí:
#
#   1. Nunca el mismo texto dos veces: cada reintento reformula y cita lo que el
#      cliente escribió, para que se note que alguien lo está leyendo.
#   2. Al tercer fallo seguido el bot suelta y cede el chat. Si no entendió dos
#      veces, no va a entender a la tercera.
#   3. Lo que no es una respuesta al formulario tampoco se trata como tal: la
#      cortesía se acusa recibo y el abandono se deriva.
_MAX_RETRIES = 3

_GIVE_UP = (
    "Perdona, no estoy logrando entenderte y no quiero hacerte perder más "
    "tiempo 🙏 Te paso con un asesor del equipo para que lo cierre contigo."
)
_ABANDON_REPLY = (
    "Entendido, sin problema 🙏 Te dejo con un asesor del equipo por si "
    "quieres retomarlo o resolver cualquier duda."
)
_STUCK_REPLY = (
    "Tienes toda la razón y te pido disculpas 🙏 Te paso con un asesor del "
    "equipo para cerrarlo sin más vueltas."
)

# "Ya no gracias", "ya no deseo el pedido", "mejor otro día": el cliente se está
# yendo. No se comprueba en el paso `card`, donde "mejor no" es la respuesta
# legítima a "¿quieres tarjeta?".
_ABANDON_RE = re.compile(
    r"\bya\s+no\b|no\s+(?:lo\s+)?(?:deseo|quiero)\b|"
    r"olv[ií]dal[oa]|d[eé]jal[oa]\b|d[eé]jemoslo|lo\s+dejamos|"
    r"mejor\s+no\b|otro\s+d[ií]a|en\s+otro\s+momento",
    re.I,
)
# El cliente nos dice que estamos fallando. Es la señal más clara de que seguir
# preguntando no va a funcionar. "co[nm]firm" porque la gente escribe "comfirme".
_STUCK_RE = re.compile(
    r"no\s+(?:me\s+)?entiend(?:en|es|e)\b|no\s+me\s+est[aá]s?\s+entend|"
    r"ya\s+(?:te\s+)?(?:lo\s+)?(?:dije|co[nm]firm\w+|respond\w+|indiqu\w+|"
    r"puse|escrib\w+|mand\w+)|te\s+lo\s+dije|siempre\s+lo\s+mismo|"
    r"otra\s+vez\s+lo\s+mismo",
    re.I,
)


def _echo(text: str, limit: int = 40) -> str:
    """Lo que escribió el cliente, recortado para citarlo de vuelta."""
    clean = " ".join((text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _again(
    state: ConversationState, meta: dict[str, Any], variants: tuple[str, ...]
) -> tuple[ConversationState, str, dict[str, Any]]:
    """Repregunta con otras palabras; al tercer fallo seguido cede el chat."""
    state.step_retries += 1
    if state.step_retries >= _MAX_RETRIES:
        state.handoff_reason = (
            f"el bot no logró entender al cliente en el paso «{state.checkout_step}» "
            f"del cierre ({state.step_retries} intentos)"
        )
        meta["handoff"] = True
        return state, _GIVE_UP, meta
    return state, variants[min(state.step_retries, len(variants)) - 1], meta


def _courtesy(
    state: ConversationState, meta: dict[str, Any], question: str
) -> tuple[ConversationState, str, dict[str, Any]]:
    """"Gracias" no es la fecha que pedimos: se acusa y no gasta un reintento."""
    return state, f"¡A ti! 😊 Solo me falta esto para cerrarlo:\n{question}", meta


def advance_checkout(
    state: ConversationState,
    user_text: str,
    *,
    today: date | None = None,
) -> tuple[ConversationState, str, dict[str, Any]]:
    """
    Avanza un paso del cierre. Devuelve (state, reply, meta).
    meta puede incluir escalate=True en payment tras confirmación, o handoff=True
    cuando el cierre se atasca y hay que ceder el chat sin anunciar venta.

    Tras el horario se recogen los datos que exige `POST /pedidos/temporales`:
    dedicatoria (paso `card`/`card_text`), destinatario, dirección y datos del
    comprador. Recién con todo eso se muestra el resumen y, al confirmarlo, el
    orquestador crea el pedido temporal y escala al pago.
    """
    before = state.checkout_step or "idle"
    state, reply, meta = _advance(state, user_text, today=today)
    # Un paso que avanza es un paso que entendimos: la escalera vuelve a cero.
    # Centralizado aquí y no en cada rama para que un `return` nuevo no se olvide
    # de resetearlo y arrastre los reintentos de un paso al siguiente.
    if (state.checkout_step or "idle") != before:
        state.step_retries = 0
    return state, reply, meta


def _advance(
    state: ConversationState,
    user_text: str,
    *,
    today: date | None = None,
) -> tuple[ConversationState, str, dict[str, Any]]:
    step = state.checkout_step or "idle"
    text = (user_text or "").strip()
    meta: dict[str, Any] = {"specialty": "checkout"}

    # Antes de tratar el texto como una respuesta al formulario: ¿lo es? Un
    # cliente que se despide o que nos dice que no lo entendemos no está
    # contestando la pregunta, y contestarle con la pregunta otra vez es lo que
    # rompió la conversación que originó esto.
    if step not in ("idle", "", "payment", "done"):
        if step != "card" and _ABANDON_RE.search(text):
            state.handoff_reason = "el cliente abandonó el cierre a medias"
            meta["handoff"] = True
            return state, _ABANDON_REPLY, meta
        if _STUCK_RE.search(text):
            state.handoff_reason = (
                "el cliente avisó que el bot no lo estaba entendiendo durante el cierre"
            )
            meta["handoff"] = True
            return state, _STUCK_REPLY, meta

    if step == "idle" or (wants_checkout(text) and step == "idle"):
        if not state.district:
            state.checkout_step = "district"
            return state, "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?", meta
        state.checkout_step = "date"
        return state, "¿Para qué fecha lo necesitas? 📅", meta

    if step == "district":
        # No guardar como distrito lo que claramente no lo es: una intención de
        # compra ("lo quiero") o el nombre de un producto que le mostramos.
        looks_like_product = (
            resolve_chosen_product(state, text, allow_implicit=False) is not None
        )
        ask_district = (
            "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?",
            "Solo necesito el *distrito* de Lima donde lo entregamos 🏠 "
            "(por ejemplo: Miraflores, Surco, San Isidro).",
        )
        if wants_checkout(text) or len(text) > 80 or looks_like_product:
            return _again(state, meta, ask_district)
        if text:
            state.district = text
        if not state.district:
            return _again(state, meta, ask_district)
        state.checkout_step = "date"
        return state, "¿Para qué fecha lo necesitas? 📅", meta

    if step == "date":
        effective_today = today or lima_today()
        # El ejemplo se calcula, no se escribe: la plantilla fija proponía "20/07"
        # y el 21 de julio le estábamos pidiendo a una clienta una fecha futura
        # con un ejemplo del día anterior.
        ejemplo = (effective_today + timedelta(days=1)).strftime("%d/%m")
        if is_courtesy_text(text):
            return _courtesy(state, meta, "¿Para qué fecha lo necesitas? 📅")
        normalized = normalize_fecha(text, today=today)
        if normalized is not None and normalized < effective_today.isoformat():
            return _again(
                state,
                meta,
                (
                    f"Esa fecha ({display_fecha(normalized)}) ya pasó 😅 "
                    f"¿Para qué día lo necesitas? Puede ser hoy mismo o *{ejemplo}*.",
                    f"¿Me la escribes como día/mes? Por ejemplo *{ejemplo}* 📅",
                ),
            )
        if normalized is None:
            return _again(
                state,
                meta,
                (
                    f"No logré leer una fecha en «{_echo(text)}» 😅 ¿Me la pones "
                    f"en números, día/mes? Por ejemplo *{ejemplo}*, o dime *hoy* "
                    f"o *mañana*.",
                    f"Vamos con lo más simple: escríbeme solo el día y el mes, "
                    f"así → *{ejemplo}* 📅",
                ),
            )
        state.date = normalized
        state.checkout_step = "schedule"
        return (
            state,
            f"¿En qué horario prefieres que llegue? 🕐\n"
            f"{schedule_options_for(state.date)}\n"
            "Responde con el número que prefieras.",
            meta,
        )

    if step == "schedule":
        opciones = schedule_options_for(state.date)
        if is_courtesy_text(text):
            return _courtesy(state, meta, f"¿En qué horario te llega mejor? 🕐\n{opciones}")
        slot = parse_schedule(text, state.date)
        if slot is None:
            # Entender la franja y que no salga ese día NO es lo mismo que no
            # entender nada. Repetir el menú entero servía para ambos casos —
            # y por eso no servía para ninguno.
            label = recognized_window(text)
            if label:
                return _again(
                    state,
                    meta,
                    (
                        f"Te entendí *{label}*, pero justo esa franja no sale "
                        f"para el {display_fecha(state.date)} 😕 Estas sí:\n{opciones}",
                        f"De las que quedan ese día, ¿cuál te sirve? Respóndeme "
                        f"con el número 🕐\n{opciones}",
                    ),
                )
            return _again(
                state,
                meta,
                (
                    f"No logré cuadrar «{_echo(text)}» con nuestras franjas 😅 "
                    f"Respóndeme con el *número* de la que te sirva:\n{opciones}",
                    f"Solo necesito un número del 1 al "
                    f"{len(windows_for(state.date))} 🕐\n{opciones}",
                ),
            )
        state.time_slot = slot
        state.checkout_step = "card"
        return state, "¿Quieres incluir una tarjeta con mensaje? 💌", meta

    if step == "card":
        low = text.casefold()
        if low.startswith("s") and "no" not in low[:4]:
            state.checkout_step = "card_text"
            return state, "¡Genial! ✍️ ¿Qué texto quieres en la tarjeta?", meta
        # Sin tarjeta: la API exige dedicatoria, mandamos un valor explícito.
        state.dedicatoria = "Sin dedicatoria"
        state.checkout_step = "recipient"
        return state, _ASK_RECIPIENT, meta

    if step == "card_text":
        state.dedicatoria = text or "Sin dedicatoria"
        state.checkout_step = "recipient"
        return state, _ASK_RECIPIENT, meta

    if step == "recipient":
        if is_courtesy_text(text):
            return _courtesy(state, meta, _ASK_RECIPIENT)
        nombre, apellidos, telefono = parse_recipient(text)
        # Sin al menos un nombre no podemos avanzar con algo útil: repreguntamos.
        if not nombre:
            return _again(
                state,
                meta,
                (
                    f"De «{_echo(text)}» no logré sacar el nombre 😅 "
                    f"{_ASK_RECIPIENT}",
                    "Con el *nombre y apellido* del destinatario me basta para "
                    "seguir 🎁 (el teléfono me lo puedes dar después).",
                ),
            )
        state.nombre_destinatario = nombre
        state.apellidos_destinatario = apellidos
        state.telefono_destinatario = telefono
        state.checkout_step = "address"
        return state, _ASK_ADDRESS, meta

    if step == "address":
        if is_courtesy_text(text):
            return _courtesy(state, meta, _ASK_ADDRESS)
        direccion, tipo = parse_address(text)
        if not direccion:
            return _again(
                state,
                meta,
                (
                    f"No logré leer una dirección en «{_echo(text)}» 😅 "
                    f"{_ASK_ADDRESS}",
                    "Con la *calle y el número* me basta 🏠 (las referencias me "
                    "las puedes dar después).",
                ),
            )
        state.direccion = direccion
        state.tipo = tipo
        state.checkout_step = "contact"
        return state, _ASK_CONTACT, meta

    if step == "contact":
        if is_courtesy_text(text):
            return _courtesy(state, meta, _ASK_CONTACT)
        nombre, apellidos, email = parse_contact(text)
        if not email:
            return _again(
                state,
                meta,
                (
                    f"En «{_echo(text)}» no encontré un correo ✉️ ¿Me lo "
                    f"escribes? Es donde te llega la confirmación.",
                    "Solo me falta tu *correo* para mandarte la confirmación "
                    "(algo como nombre@gmail.com) ✉️",
                ),
            )
        state.nombre_cliente = nombre
        state.apellidos_cliente = apellidos
        state.email_cliente = email
        state.checkout_step = "summary"
        return state, _summary_message(state) + "\n¿Todo correcto? 😊", meta

    if step == "summary":
        low = text.casefold()
        if any(x in low for x in ("si", "sí", "ok", "dale", "correcto", "perfecto", "yes")):
            state.checkout_step = "payment"
            state.handoff_reason = "cliente listo para pagar / coordinar comprobante"
            meta["escalate"] = True
            meta["create_order"] = True
            return (
                state,
                "Perfecto 🙌 Un asesor te comparte el link o las cuentas para pagar.",
                meta,
            )
        # Corrección → volver a fecha/distrito genérico
        state.checkout_step = "date"
        return state, "Sin problema. ¿Qué dato quieres corregir: distrito, fecha u horario?", meta

    if step == "payment":
        meta["escalate"] = True
        return state, "Un asesor de nuestro equipo continua contigo para el pago 🙏", meta

    return state, "¿En qué más te ayudo con tu pedido?", meta


def _summary_message(state: ConversationState) -> str:
    product = state.chosen_product_name or "Producto elegido"
    distrito = state.district or "—"
    fee = ""
    if state.shipping_fee_sol is not None:
        fee = f" — envío S/{state.shipping_fee_sol:.2f}"
    destinatario = " ".join(
        p for p in (state.nombre_destinatario, state.apellidos_destinatario) if p
    ) or "—"
    tipo_txt = ""
    if state.tipo is not None:
        tipo_txt = " (oficina)" if state.tipo == 1 else " (casa)"
    lines = [
        "📋 *Resumen del pedido:*",
        f"· Producto: {product}",
        f"· Distrito: {distrito}{fee}",
        f"· Fecha: {display_fecha(state.date) if state.date else '—'}",
        f"· Horario: {state.time_slot or '—'}",
        f"· Para: {destinatario}",
        f"· Dirección: {state.direccion or '—'}{tipo_txt}",
    ]
    if state.dedicatoria and state.dedicatoria != "Sin dedicatoria":
        lines.append(f"· Tarjeta: {state.dedicatoria}")
    return "\n".join(lines)


_PHONE_RE = re.compile(r"(\+?\d[\d\s().\-]{5,}\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_OFFICE_RE = re.compile(r"\boficina\b|\btrabajo\b|\bempresa\b|\bof\.\b", re.I)
# Muletillas que la gente antepone al dar nombre/teléfono; se limpian antes de
# separar nombre y apellidos, para no acabar con nombre="telefono".
_NAME_NOISE_RE = re.compile(
    r"\b(tel[eé]fono|telf|cel(?:ular)?|n[uú]mero|numero|contacto|"
    r"nombre|se\s+llama|es|para|mi|el|la|su|de)\b",
    re.I,
)


def split_name(full: str) -> tuple[str, str]:
    """Parte un nombre completo en (nombre, apellidos). Heurística simple."""
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def parse_phone(text: str) -> str:
    """Extrae un teléfono del texto (solo dígitos y un posible '+')."""
    m = _PHONE_RE.search(text or "")
    if not m:
        return ""
    return re.sub(r"[^\d+]", "", m.group(1))


def _clean_name(text: str) -> str:
    text = _NAME_NOISE_RE.sub(" ", text or "")
    text = re.sub(r"[,;:_/\\\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_recipient(text: str) -> tuple[str, str, str]:
    """(nombre, apellidos, teléfono) del destinatario, a partir de texto libre."""
    telefono = parse_phone(text)
    resto = _PHONE_RE.sub(" ", text or "") if telefono else (text or "")
    nombre, apellidos = split_name(_clean_name(resto))
    return nombre, apellidos, telefono


def parse_address(text: str) -> tuple[str, int]:
    """(dirección, tipo) donde tipo = 1 si menciona oficina, si no 0 (casa)."""
    direccion = (text or "").strip()
    tipo = 1 if _OFFICE_RE.search(direccion) else 0
    return direccion, tipo


def parse_contact(text: str) -> tuple[str, str, str]:
    """(nombre, apellidos, email) del comprador, a partir de texto libre."""
    email = ""
    m = _EMAIL_RE.search(text or "")
    if m:
        email = m.group(0)
    resto = _EMAIL_RE.sub(" ", text or "") if email else (text or "")
    nombre, apellidos = split_name(_clean_name(resto))
    return nombre, apellidos, email


def start_checkout(state: ConversationState, product_name: str = "", product_id: int | None = None) -> ConversationState:
    if product_name:
        state.chosen_product_name = product_name
    if product_id is not None:
        state.chosen_product_id = product_id
    state.checkout_step = "district" if not state.district else "date"
    return state


_ORDINALS: dict[str, int] = {
    "primero": 0, "primera": 0, "1": 0, "uno": 0,
    "segundo": 1, "segunda": 1, "2": 1, "dos": 1,
    "tercero": 2, "tercera": 2, "3": 2, "tres": 2,
    "cuarto": 3, "cuarta": 3, "4": 3, "cuatro": 3,
    "quinto": 4, "quinta": 4, "5": 4, "cinco": 4,
}


def resolve_chosen_product(
    state: ConversationState, user_text: str, *, allow_implicit: bool = True
) -> tuple[int, str] | None:
    """¿A cuál de los productos mostrados se refiere el cliente?

    Sin esto el resumen del pedido decía literalmente "Producto elegido": el
    cierre arrancaba sin saber qué se estaba vendiendo. Devuelve `None` si es
    ambiguo, y entonces preguntar es mejor que adivinar.

    `allow_implicit=False` exige una referencia explícita (nombre u ordinal). Lo
    usa el router: con un solo producto a la vista, "quiero flores" es una
    búsqueda nueva, no la compra de ese producto.
    """
    recent = [p for p in (state.recent_products or []) if p.get("id_producto")]
    if not recent:
        return None

    text = _norm(user_text)

    # 1. Por nombre: "me gusta el panda", "quiero el Ramo de Girasoles".
    matches = [
        p for p in recent
        if (name := _norm(p.get("nombre") or "")) and _name_hit(name, text)
    ]
    # `_name_hit` acierta con UNA palabra larga, así que entre varios desayunos
    # casan todos y quedaba ambiguo. Si el nombre COMPLETO de uno está en el texto,
    # ese gana: es justo lo que pasa cuando el cliente RESPONDE al mensaje del
    # producto (la cita trae el nombre entero) o lo escribe tal cual.
    if len(matches) > 1:
        completos = [
            p for p in matches
            if (name := _norm(p.get("nombre") or "")) and name in text
        ]
        if len(completos) == 1:
            matches = completos
    if len(matches) == 1:
        return int(matches[0]["id_producto"]), str(matches[0].get("nombre") or "")

    # 2. Por ordinal: "el segundo", "la 3".
    for word, index in _ORDINALS.items():
        if re.search(rf"\b(?:el|la|opci[oó]n|numero|n[uú]mero)?\s*{word}\b", text):
            if index < len(recent):
                chosen = recent[index]
                return int(chosen["id_producto"]), str(chosen.get("nombre") or "")
            break

    # 3. "ese" / "lo quiero" sin más: solo es unívoco si mostramos uno solo.
    if allow_implicit and len(recent) == 1:
        return int(recent[0]["id_producto"]), str(recent[0].get("nombre") or "")

    return None


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").casefold())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _name_hit(name: str, text: str) -> bool:
    """Un nombre de producto acierta si alguna palabra suya larga sale en el texto."""
    if name and name in text:
        return True
    words = [w for w in name.split() if len(w) >= 5]
    return any(w in text for w in words)
