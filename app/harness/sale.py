"""Venta cerrada: la señal que el agente le deja al vendedor.

Cuando el cliente confirma el resumen del pedido, el bot ya tiene TODO: qué
producto, a qué distrito, qué día, en qué horario y cuánto cuesta el envío. Hasta
ahora eso se disolvía en el hilo del chat y el asesor tenía que reconstruirlo
leyendo veinte mensajes.

Ahora se guarda como un objeto, el CRM pinta ese chat en verde y el asesor entra
sabiendo exactamente qué se vendió.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.crm import http_client as crm_http
from app.harness.checkout import order_total
from app.harness.state import ConversationState
from app.harness.orders import display_fecha

log = logging.getLogger(__name__)


def sale_key(conversation_id: int) -> str:
    return f"sale_{conversation_id}"


def build_sale(state: ConversationState) -> dict[str, Any]:
    """El pedido tal como quedó confirmado."""
    return {
        "producto": state.chosen_product_name or "",
        "id_producto": state.chosen_product_id,
        "distrito": state.district or "",
        "precio_sol": state.chosen_product_price_sol,
        "envio_sol": state.shipping_fee_sol,
        "total_sol": order_total(state),
        "fecha": display_fecha(state.date),
        "horario": state.time_slot or "",
        # id del pedido ya creado en el panel (si se pudo): el asesor lo abre y
        # convierte, sin recapturar nada.
        "pedido_temporal_id": state.pedido_temporal_id,
        "cerrada_en": int(time.time()),
        "motivo": state.handoff_reason or "cliente listo para pagar",
    }


def is_complete(sale: dict[str, Any]) -> bool:
    """Una venta solo se anuncia si de verdad tiene los datos del pedido.

    Pintar un chat en verde sin producto ni distrito sería peor que no pintarlo:
    el asesor entra, no encuentra nada y deja de fiarse del color.
    """
    return bool(sale.get("producto") and sale.get("distrito") and sale.get("fecha"))


async def announce(conversation_id: int, state: ConversationState) -> dict[str, Any] | None:
    """Deja la venta en el CRM. Devuelve el pedido, o `None` si no estaba completo."""
    sale = build_sale(state)
    if not is_complete(sale):
        log.info(
            "[venta] conversation=%s cierre sin datos completos; no se anuncia (%s)",
            conversation_id,
            {k: v for k, v in sale.items() if k in ("producto", "distrito", "fecha")},
        )
        return None

    if crm_http.crm_enabled():
        try:
            await crm_http.put_setting(
                sale_key(conversation_id), json.dumps(sale, ensure_ascii=False)
            )
        except Exception as err:
            # Que falle el verde no puede tumbar el handoff ni dejar al equipo sin
            # enterarse: el cliente está esperando para pagar, y el aviso va igual.
            log.warning("[venta] no se pudo anunciar conversation=%s: %s", conversation_id, err)

    log.info(
        "[venta] CERRADA conversation=%s producto=%r distrito=%r fecha=%r",
        conversation_id,
        sale["producto"],
        sale["distrito"],
        sale["fecha"],
    )

    # Aviso inmediato al equipo: el cliente está listo para pagar AHORA.
    try:
        from app.services.messenger import notify_team

        await notify_team(summary(sale, conversation_id))
    except Exception as err:
        log.warning("[venta] no se pudo avisar al equipo: %s", err)

    return sale


def summary(sale: dict[str, Any], conversation_id: int) -> str:
    """El pedido, listo para leer de un vistazo en el aviso."""
    envio = (
        f"S/{float(sale['envio_sol']):.2f}" if sale.get("envio_sol") is not None else "—"
    )
    total = (
        f"· Total: S/{float(sale['total_sol']):.2f}\n"
        if sale.get("total_sol") is not None
        else "· Total: sin confirmar, revisa precio y envío\n"
    )
    # Sin esto, un chat en verde parecía tener el pedido armado aunque la API lo
    # hubiera rechazado: el asesor lo buscaba en el panel y no estaba.
    pedido = (
        f"· Pedido temporal #{sale['pedido_temporal_id']} ya en el panel "
        "(solo conviértelo)\n"
        if sale.get("pedido_temporal_id")
        else "· ⚠️ No se creó el pedido temporal: hay que armarlo a mano en el panel\n"
    )
    return (
        "💚 *VENTA CERRADA POR DON REGALO* — solo falta cobrar\n"
        f"Conversación #{conversation_id}\n"
        f"· Producto: {sale.get('producto') or '—'}\n"
        f"· Distrito: {sale.get('distrito') or '—'} (envío {envio})\n"
        f"· Fecha: {sale.get('fecha') or '—'}\n"
        f"· Horario: {sale.get('horario') or '—'}\n"
        f"{total}"
        f"{pedido}\n"
        "Entra al chat en el CRM (está en verde) y coordina el pago."
    )
