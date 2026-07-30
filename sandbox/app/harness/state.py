"""Estado explícito del pedido / cobertura / handoff (no confiar solo en el historial LLM)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.crm import http_client as crm_http

log = logging.getLogger(__name__)

# Pasos de cierre (alineados a system.py CIERRE DE PEDIDO).
# Tras `card` se recogen los datos que exige `POST /pedidos/temporales`
# (destinatario, dirección y datos del comprador) antes del resumen.
CHECKOUT_STEPS = (
    "idle",
    "district",
    "date",
    "schedule",
    "card",
    "card_text",
    "recipient",
    "address",
    "contact",
    "summary",
    "payment",
    "done",
)


@dataclass
class ConversationState:
    intent_last: str = ""
    checkout_step: str = "idle"
    chosen_product_id: Optional[int] = None
    chosen_product_name: str = ""
    district: str = ""
    # id del distrito en la API de donregalo. Lo exige `POST /pedidos/temporales`;
    # `coverage` lo resuelve al confirmar el distrito y aquí lo conservamos.
    id_distrito: Optional[int] = None
    shipping_fee_sol: Optional[float] = None
    shipping_fee_usd: Optional[float] = None
    date: str = ""
    time_slot: str = ""
    # Datos que recoge el cierre para crear el pedido temporal en el panel.
    dedicatoria: str = ""
    nombre_destinatario: str = ""
    apellidos_destinatario: str = ""
    telefono_destinatario: str = ""
    direccion: str = ""
    tipo: Optional[int] = None  # 0 = casa, 1 = oficina
    nombre_cliente: str = ""
    apellidos_cliente: str = ""
    email_cliente: str = ""
    # id del pedido temporal ya creado en la API (para no duplicarlo y para el aviso).
    pedido_temporal_id: Optional[int] = None
    # Fallos SEGUIDOS del paso actual del cierre. La FSM era una función pura de
    # (paso, texto): cuando no entendía devolvía los mismos bytes, y los devolvía
    # para siempre. Una clienta recibió cuatro veces "No pude confirmar esa fecha"
    # y se fue ("ya no deseo el pedido por q no entienden"). Con el contador cada
    # reintento cambia de texto y el tercero cede el chat a un humano.
    step_retries: int = 0
    # El último menú de categorías que se le ofreció al cliente, con el slug de
    # cada opción y en el MISMO orden en que se numeró. Sin esto, el "7" del
    # cliente no significa nada para el código y había que fiarse de que el
    # modelo recordara su propio menú — que fue justo lo que falló.
    recent_options: list[dict] = field(default_factory=list)
    # Cuántos menús seguidos lleva sin ver un solo producto. A los dos, se
    # muestran productos con lo que haya: preguntar una tercera vez es como se
    # pierde a quien ya sabía lo que quería.
    menu_depth: int = 0
    # Turnos SEGUIDOS de descubrimiento en los que el bot no enseñó ni un
    # producto. `menu_depth` solo cuenta los menús que compone el código, así que
    # con Lichi se quedó en 1 mientras el modelo servía nueve submenús suyos: el
    # tope de dos menús nunca llegó a saltar. Esto cuenta lo que le pasa al
    # CLIENTE —seguir sin ver nada—, que es lo que de verdad mata la venta.
    turns_without_products: int = 0
    shown_product_ids: list[int] = field(default_factory=list)
    # Últimos productos mostrados ({"id_producto", "nombre"}), en orden. Sin los
    # nombres no se puede resolver "quiero el segundo" ni "me gusta el panda".
    recent_products: list[dict] = field(default_factory=list)
    # ¿Ya nos presentamos con este cliente? Vive en el estado, no en el historial:
    # la ventana de historial se recorta y el bot volvía a saludar en genérico en
    # vez de presentarse.
    presented: bool = False
    campaign_slug: str = ""
    # ¿El turno anterior el bot OFRECIÓ un asesor ("¿quieres que consulte con un
    # asesor?")? Entonces un "sí" del cliente es aceptar la derivación, no charla.
    # El router solo ve el texto del cliente, no lo que ofreció el bot.
    handoff_offered: bool = False
    handoff_reason: str = ""
    # Cuándo se cedió el chat (epoch). Es el ancla del releaser mientras el asesor
    # todavía no ha escrito: sin ella no hay forma de medir "lleva X sin contestar"
    # y el bot recuperaba el chat al instante de haber prometido un asesor.
    handoff_at: Optional[float] = None
    keep_human: bool = False
    last_human_outbound_at: Optional[float] = None  # epoch seconds
    assignee_user_id: Optional[int] = None
    # Versión del documento, para escribir sin pisar a nadie (ver `save_state`).
    # No es un dato de la conversación: es lo que permite detectar que otro
    # escritor tocó el estado mientras este turno pensaba.
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ConversationState":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in data.items() if k in known}
        if "shown_product_ids" in kwargs and kwargs["shown_product_ids"] is None:
            kwargs["shown_product_ids"] = []
        return cls(**kwargs)

    def patch(self, updates: dict[str, Any]) -> "ConversationState":
        for k, v in updates.items():
            # `version` la lleva la capa de persistencia, no la conversación: un
            # `state_patch` de un especialista no puede tocarla o el control de
            # concurrencia se rompería desde dentro.
            if k == "version" or not hasattr(self, k) or v is None:
                continue
            if k == "shown_product_ids" and isinstance(v, list):
                merged = list(dict.fromkeys([*self.shown_product_ids, *[int(x) for x in v if x is not None]]))
                setattr(self, k, merged)
            elif k == "recent_products" and isinstance(v, list):
                # Los del turno actual van primero: "el segundo" se refiere a lo
                # último que vio el cliente, no a lo de hace cinco mensajes.
                merged_products: list[dict] = []
                seen: set[int] = set()
                for item in [*v, *self.recent_products]:
                    if not isinstance(item, dict):
                        continue
                    pid = item.get("id_producto")
                    if pid is None or pid in seen:
                        continue
                    seen.add(pid)
                    merged_products.append({"id_producto": pid, "nombre": item.get("nombre") or ""})
                setattr(self, k, merged_products[:12])
            else:
                setattr(self, k, v)
        return self

    def is_payment_handoff(self) -> bool:
        reason = (self.handoff_reason or "").casefold()
        if self.checkout_step == "payment":
            return True
        return any(
            token in reason
            for token in ("pago", "comprobante", "yape", "plin", "transfer", "tarjeta")
        )


def _settings_key(conversation_id: int) -> str:
    return f"harness_state_{conversation_id}"


async def load_state(conversation_id: int, *, wa_id: str = "") -> ConversationState:
    """Carga estado desde CRM settings (externo) o memoria en proceso (local)."""
    if crm_http.crm_enabled():
        try:
            raw = await crm_http.get_setting(_settings_key(conversation_id))
            if raw:
                return ConversationState.from_dict(json.loads(raw))
        except Exception as err:
            log.warning("[harness] load_state falló: %s", err)
        # Fallback: leer tag HARNESS en memory.resumen
        if wa_id:
            try:
                mem = await crm_http.get_memory(wa_id) or {}
                resumen = mem.get("resumen_memory") or mem.get("resumen") or ""
                if isinstance(resumen, str) and resumen.startswith("HARNESS:"):
                    return ConversationState.from_dict(json.loads(resumen[8:]))
            except Exception:
                pass
        return ConversationState()

    # Local: dict en memoria del proceso (tests). Se devuelve una COPIA a
    # propósito: devolver el objeto guardado convertía el caché en memoria
    # compartida entre escritores y hacía imposible reproducir en un test la
    # carrera que sí ocurre en producción contra el CRM.
    stored = _local_cache.get(conversation_id)
    return ConversationState.from_dict(stored.to_dict()) if stored else ConversationState()


# Cuántas veces se reintenta un guardado que perdió la carrera. Tres sobran: los
# escritores son pocos (turno, releaser, handoff) y cada reintento parte de una
# lectura fresca, así que la probabilidad de perder tres seguidas es despreciable.
_SAVE_RETRIES = 3


def apply_delta(state: ConversationState, delta: dict[str, Any]) -> ConversationState:
    """Aplica sobre `state` los campos que otro escritor cambió.

    NO usa `patch()` tal cual: `patch` ignora los `None` (correcto para un
    `state_patch` de un especialista, que solo dice lo que aprendió) y aquí eso
    haría imposible vaciar un campo — un turno que limpia `chosen_product_id`
    vería reaparecer el producto viejo al fusionar. Un delta es, por definición,
    "esto lo cambié yo", incluido cambiarlo a nada.

    Las dos listas acumulativas sí van por `patch`: `shown_product_ids` y
    `recent_products` se unen en vez de reemplazarse, que es justo lo que se
    quiere cuando dos escritores mostraron productos distintos.
    """
    acumulativas = {"shown_product_ids", "recent_products"}
    for key, value in delta.items():
        if key == "version" or not hasattr(state, key):
            continue
        if key in acumulativas:
            state.patch({key: value})
        else:
            setattr(state, key, value)
    return state


def state_delta(base: dict[str, Any] | None, state: ConversationState) -> dict[str, Any]:
    """Los campos que ESTE escritor cambió respecto a la foto que leyó.

    Es lo que convierte un guardado en una fusión en vez de un pisotón. Sin base
    (nadie tomó la foto) el delta es el documento entero, que es el
    comportamiento de siempre.
    """
    current = state.to_dict()
    current.pop("version", None)
    if base is None:
        return current
    return {k: v for k, v in current.items() if base.get(k) != v}


async def save_state(
    conversation_id: int,
    state: ConversationState,
    *,
    wa_id: str = "",
    base: dict[str, Any] | None = None,
) -> None:
    """Guarda el estado sin llevarse por delante lo que otro escribió mientras.

    El problema: `load_state` → mutar → `save_state` es leer-modificar-escribir
    de un documento COMPLETO, y hay tres escritores sobre el mismo documento:

    - el turno del cliente, que tarda segundos (el LLM está en medio);
    - el releaser, que corre en segundo plano y FUERA del lock de Redis;
    - el handoff (`perform_handoff`), que escribe a mitad del propio turno.

    El lock por conversación de Redis serializa los turnos ENTRANTES, así que no
    protege de los otros dos. El caso que ya estaba vivo: `perform_handoff`
    guardaba `handoff_at` a mitad del turno y, al terminar, `master` escribía su
    copia —cargada ANTES del handoff, con `handoff_at` a None— y lo borraba. Ese
    campo es el ancla del releaser: sin él, el bot recuperaba el chat al instante
    de haber prometido un asesor, que es justo el incidente que lo creó.

    La solución es doble: `version` + escritura condicional en el CRM (lo único
    que puede ser atómico de verdad, con la fila bloqueada) y, cuando la versión
    ya no es la esperada, aplicar SOLO los campos que este escritor cambió sobre
    el documento fresco. Reintentar el turno entero no es una opción: ya se
    habló con el cliente.
    """
    delta = state_delta(base, state)
    expected = int(base.get("version", 0)) if base is not None else int(state.version)

    if not crm_http.crm_enabled():
        _save_local(conversation_id, state, delta=delta, expected=expected)
        return

    key = _settings_key(conversation_id)
    for intento in range(_SAVE_RETRIES):
        state.version = expected + 1
        payload = json.dumps(state.to_dict(), ensure_ascii=False)
        try:
            stored = await crm_http.put_setting_cas(key, payload, expected)
        except Exception as err:
            log.warning("[harness] save_state setting falló: %s", err)
            if wa_id:
                try:
                    await crm_http.put_memory(wa_id, {"resumen": f"HARNESS:{payload}"})
                except Exception as err2:
                    log.warning("[harness] save_state memory falló: %s", err2)
            return

        if stored is None:
            # CRM antiguo, sin escritura condicional. Se guarda igual: quedarse
            # sin persistir el estado es peor que la carrera que esto evitaba
            # (mismo criterio que el claim del outbox, que envía aunque el CRM
            # todavía no sepa reclamar).
            try:
                await crm_http.put_setting(key, payload)
            except Exception as err:
                log.warning("[harness] save_state setting falló: %s", err)
            return

        if stored:
            return

        # Perdimos la carrera: releemos y reaplicamos SOLO lo nuestro.
        fresh = await load_state(conversation_id, wa_id=wa_id)
        log.info(
            "[harness] save_state conversation=%s versión %s→%s ocupada; "
            "reaplicando %s campo(s) (intento %s)",
            conversation_id,
            expected,
            fresh.version,
            len(delta),
            intento + 1,
        )
        expected = int(fresh.version)
        apply_delta(fresh, delta)
        state = fresh

    log.error(
        "[harness] save_state conversation=%s no pudo escribir tras %s intentos",
        conversation_id,
        _SAVE_RETRIES,
    )


def _save_local(
    conversation_id: int,
    state: ConversationState,
    *,
    delta: dict[str, Any],
    expected: int,
) -> None:
    """Mismo contrato en local, para que los tests vean lo que ve producción.

    El caché local se comportaba como memoria compartida: `load_state` devolvía
    EL MISMO objeto a todos los que lo pidieran, así que dos escritores se veían
    las mutaciones sin guardar y ninguna carrera podía reproducirse en un test.
    Ahora se copia al leer y se fusiona al escribir, igual que contra el CRM.
    """
    stored = _local_cache.get(conversation_id)
    if stored is not None and int(stored.version) != expected:
        merged = apply_delta(ConversationState.from_dict(stored.to_dict()), delta)
        merged.version = int(stored.version) + 1
        _local_cache[conversation_id] = merged
        return
    state.version = expected + 1
    _local_cache[conversation_id] = ConversationState.from_dict(state.to_dict())


_local_cache: dict[int, ConversationState] = {}


def clear_local_cache() -> None:
    _local_cache.clear()
