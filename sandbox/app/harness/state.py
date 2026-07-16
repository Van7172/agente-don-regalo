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
            if not hasattr(self, k) or v is None:
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

    # Local: atributo en dict en memoria de proceso (tests) o contact attributes vía caller.
    return _local_cache.get(conversation_id, ConversationState())


async def save_state(conversation_id: int, state: ConversationState, *, wa_id: str = "") -> None:
    payload = json.dumps(state.to_dict(), ensure_ascii=False)
    if crm_http.crm_enabled():
        try:
            await crm_http.put_setting(_settings_key(conversation_id), payload)
            return
        except Exception as err:
            log.warning("[harness] save_state setting falló: %s", err)
            if wa_id:
                try:
                    await crm_http.put_memory(wa_id, {"resumen": f"HARNESS:{payload}"})
                except Exception as err2:
                    log.warning("[harness] save_state memory falló: %s", err2)
            return
    _local_cache[conversation_id] = state


_local_cache: dict[int, ConversationState] = {}


def clear_local_cache() -> None:
    _local_cache.clear()
