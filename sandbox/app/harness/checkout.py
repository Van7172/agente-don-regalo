"""Máquina de estados del cierre de pedido (determinista)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.delivery_windows import SCHEDULE_MAP as _SCHEDULE_MAP
from app.delivery_windows import SCHEDULE_OPTIONS
from app.harness.state import ConversationState

__all__ = [
    "SCHEDULE_OPTIONS",
    "advance_checkout",
    "parse_address",
    "parse_contact",
    "parse_recipient",
    "parse_schedule",
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


def parse_schedule(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw[0] in _SCHEDULE_MAP and (len(raw) == 1 or not raw[1].isdigit()):
        return _SCHEDULE_MAP[raw[0]]
    m = re.search(r"(\d{1,2})\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)", raw, re.I)
    if m:
        # Heurística: "09 AM a 11" → opción 2
        hour = int(m.group(1))
        if 7 <= hour < 9:
            return _SCHEDULE_MAP["1"]
        if 9 <= hour < 11:
            return _SCHEDULE_MAP["2"]
        if 11 <= hour < 14:
            return _SCHEDULE_MAP["3"]
        if 14 <= hour < 17:
            return _SCHEDULE_MAP["4"]
        if 16 <= hour <= 19:
            return _SCHEDULE_MAP["5"]
    low = raw.casefold()
    if "11:00" in low or "09" in low or "9 am" in low:
        return _SCHEDULE_MAP["2"]
    if "mediod" in low:
        return _SCHEDULE_MAP["3"]
    return None


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


def advance_checkout(state: ConversationState, user_text: str) -> tuple[ConversationState, str, dict[str, Any]]:
    """
    Avanza un paso del cierre. Devuelve (state, reply, meta).
    meta puede incluir escalate=True en payment tras confirmación.

    Tras el horario se recogen los datos que exige `POST /pedidos/temporales`:
    dedicatoria (paso `card`/`card_text`), destinatario, dirección y datos del
    comprador. Recién con todo eso se muestra el resumen y, al confirmarlo, el
    orquestador crea el pedido temporal y escala al pago.
    """
    step = state.checkout_step or "idle"
    text = (user_text or "").strip()
    meta: dict[str, Any] = {"specialty": "checkout"}

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
        if wants_checkout(text) or len(text) > 80 or looks_like_product:
            return state, "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?", meta
        if text:
            state.district = text
        if not state.district:
            return state, "¡Perfecto! 🎉 ¿A qué distrito lo enviamos?", meta
        state.checkout_step = "date"
        return state, "¿Para qué fecha lo necesitas? 📅", meta

    if step == "date":
        if text:
            state.date = text
        state.checkout_step = "schedule"
        return (
            state,
            f"¿En qué horario prefieres que llegue? 🕐\n{SCHEDULE_OPTIONS}\n"
            "Responde con el número que prefieras.",
            meta,
        )

    if step == "schedule":
        slot = parse_schedule(text) or text
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
        nombre, apellidos, telefono = parse_recipient(text)
        # Sin al menos un nombre no podemos avanzar con algo útil: repreguntamos.
        if not nombre:
            return state, _ASK_RECIPIENT, meta
        state.nombre_destinatario = nombre
        state.apellidos_destinatario = apellidos
        state.telefono_destinatario = telefono
        state.checkout_step = "address"
        return state, _ASK_ADDRESS, meta

    if step == "address":
        direccion, tipo = parse_address(text)
        if not direccion:
            return state, _ASK_ADDRESS, meta
        state.direccion = direccion
        state.tipo = tipo
        state.checkout_step = "contact"
        return state, _ASK_CONTACT, meta

    if step == "contact":
        nombre, apellidos, email = parse_contact(text)
        if not email:
            return (
                state,
                "Necesito un *correo* válido para enviarte la confirmación. "
                "¿Me lo compartes? ✉️",
                meta,
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
        f"· Fecha: {state.date or '—'}",
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
