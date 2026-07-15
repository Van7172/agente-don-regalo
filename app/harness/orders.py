"""Pedido temporal: del estado de la conversación al panel de donregalo.

Cuando el cliente confirma el resumen del cierre, el bot ya tiene todo lo que
`POST /pedidos/temporales` (API.md → Pedidos) necesita: producto, distrito,
fecha, horario, destinatario, dirección y datos del comprador. Este módulo
traduce ese estado al cuerpo que la API espera y crea el pedido, para que ventas
lo encuentre pre-armado en "Pedidos temporales".

Dos decisiones que vienen de la arquitectura:

- **Es best-effort.** Si falta un dato, la API responde 422 o el CRM está caído,
  se registra y se sigue: el cliente que espera para pagar no puede quedar
  bloqueado por un fallo del panel. El asesor completa lo que falte.
- **El dinero no lo calcula el LLM.** `delivery` va en soles tal como lo guardó
  cobertura (`shipping_fee_sol`), que ya salió convertido del adapter. La API lo
  reconvierte a USD como hace el panel.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, timedelta

import httpx

from app.delivery_windows import hora_entrega_api
from app.harness.state import ConversationState
from app.tools import orders as orders_tool

log = logging.getLogger(__name__)

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_DIAS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").casefold())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _fmt(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def normalize_fecha(text: str, today: date | None = None) -> str | None:
    """Texto libre de fecha → `YYYY-MM-DD`, o `None` si no se puede afirmar.

    El paso `date` del FSM guarda lo que el cliente escriba ("mañana", "viernes
    18", "20 de julio", "2026-07-20"). La API exige un ISO, así que aquí se
    interpreta lo común. Ante la duda devuelve `None`: preferimos no crear el
    pedido a mandar una fecha inventada.
    """
    today = today or date.today()
    t = _norm(text)
    if not t:
        return None

    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        return _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    if "pasado manana" in t:
        return (today + timedelta(days=2)).isoformat()
    if "manana" in t:
        return (today + timedelta(days=1)).isoformat()
    if "hoy" in t:
        return today.isoformat()

    m = re.search(
        r"\b(\d{1,2})\s*(?:de\s+)?(" + "|".join(_MESES) + r")(?:\s+(?:de\s+)?(20\d{2}))?\b",
        t,
    )
    if m:
        d, mo = int(m.group(1)), _MESES[m.group(2)]
        y = int(m.group(3)) if m.group(3) else today.year
        res = _fmt(y, mo, d)
        if res and m.group(3) is None and res < today.isoformat():
            res = _fmt(y + 1, mo, d)  # "20 de enero" en diciembre → el próximo
        return res

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            y = y + 2000 if y < 100 else y
        else:
            y = today.year
        res = _fmt(y, mo, d)
        if res and not m.group(3) and res < today.isoformat():
            res = _fmt(y + 1, mo, d)
        return res

    for name, idx in _DIAS.items():
        if name in t:
            delta = (idx - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()

    return None


def build_body(
    state: ConversationState, wa_id: str, *, id_distrito: int | None = None
) -> dict | None:
    """Estado de la conversación → cuerpo de `POST /pedidos/temporales`.

    Devuelve `None` si falta algún campo obligatorio que no podemos completar (el
    orquestador simplemente no crea el pedido y deja constancia en el log).
    """
    id_dist = id_distrito if id_distrito is not None else state.id_distrito
    fecha = normalize_fecha(state.date)
    hora = hora_entrega_api(state.time_slot)
    telefono_cliente = _digits(wa_id) or _digits(state.telefono_destinatario)

    # Un apellido en blanco (el cliente dio solo un nombre) no debería frenar toda
    # la venta: lo dejamos como marcador y el asesor lo corrige en el panel.
    apellidos_cliente = state.apellidos_cliente or ("." if state.nombre_cliente else "")
    apellidos_dest = state.apellidos_destinatario or ("." if state.nombre_destinatario else "")

    required: dict[str, object | None] = {
        "nombre_cliente": state.nombre_cliente,
        "apellidos_cliente": apellidos_cliente,
        "telefono_cliente": telefono_cliente,
        "email_cliente": state.email_cliente,
        "nombre_destinatario": state.nombre_destinatario,
        "apellidos_destinatario": apellidos_dest,
        "telefono_destinatario": state.telefono_destinatario,
        "fecha_entrega": fecha,
        "hora_entrega": hora,
        "dedicatoria": state.dedicatoria or "Sin dedicatoria",
        "id_distrito": id_dist,
        "direccion": state.direccion,
        "tipo": state.tipo,
    }

    faltan = [k for k, v in required.items() if v is None or v == ""]
    if faltan:
        log.info("[pedido-temporal] no se crea; faltan campos: %s", faltan)
        return None

    body: dict[str, object] = dict(required)  # type: ignore[assignment]
    body["tipo"] = int(state.tipo or 0)
    body["id_distrito"] = int(id_dist)  # type: ignore[arg-type]
    if state.chosen_product_id:
        body["id_producto"] = int(state.chosen_product_id)
    body["observaciones"] = _observaciones(state)
    if state.shipping_fee_sol is not None:
        body["delivery"] = round(float(state.shipping_fee_sol), 2)
    return body


def _observaciones(state: ConversationState) -> str:
    partes = ["[Agente IA] Pedido pre-armado desde WhatsApp."]
    if state.chosen_product_name:
        partes.append(f"Producto: {state.chosen_product_name}.")
    if state.time_slot:
        partes.append(f"Franja indicada: {state.time_slot}.")
    return " ".join(partes)


async def _resolve_id_distrito(client: httpx.AsyncClient, nombre: str) -> int | None:
    from app.harness.coverage import match_district
    from app.tools import catalog

    try:
        raw = await catalog.distritos_cobertura(client, {})
    except Exception as err:
        log.warning("[pedido-temporal] no se pudo listar distritos: %s", err)
        return None
    districts = raw.get("data") if isinstance(raw, dict) else raw
    matched = match_district(nombre, list(districts or []))
    if matched and matched.get("id_distrito") is not None:
        try:
            return int(matched["id_distrito"])
        except (TypeError, ValueError):
            return None
    return None


async def create_from_state(
    state: ConversationState, wa_id: str, *, client: httpx.AsyncClient | None = None
) -> dict | None:
    """Crea el pedido temporal a partir del estado. Best-effort: nunca lanza.

    Devuelve el `data` de la respuesta (con `id_pedido_temporal`) o `None` si no
    se pudo crear.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=20.0)
    assert client is not None
    try:
        id_dist = state.id_distrito
        if id_dist is None and state.district:
            id_dist = await _resolve_id_distrito(client, state.district)

        body = build_body(state, wa_id, id_distrito=id_dist)
        if body is None:
            return None

        resp = await orders_tool.crear_pedido_temporal(client, body)
        data = resp.get("data") if isinstance(resp, dict) else None
        if data:
            log.info(
                "[pedido-temporal] creado id=%s distrito=%s",
                data.get("id_pedido_temporal"),
                data.get("nombre_distrito"),
            )
        return data or (resp if isinstance(resp, dict) else None)
    except httpx.HTTPStatusError as err:
        log.warning(
            "[pedido-temporal] la API rechazó el pedido (HTTP %s): %s",
            err.response.status_code,
            err.response.text[:300],
        )
        return None
    except Exception as err:
        log.warning("[pedido-temporal] no se pudo crear: %s", err)
        return None
    finally:
        if own_client:
            await client.aclose()
