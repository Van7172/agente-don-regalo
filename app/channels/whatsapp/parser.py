"""Parseo de webhooks inbound de WhatsApp Cloud API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class InboundMessage:
    wa_id: str
    contact_name: str
    wa_message_id: str
    message_type: str  # text|image|audio|document|location|order|reaction|button|interactive|unknown
    text: str = ""
    media_id: str | None = None
    mime_type: str | None = None
    caption: str = ""
    quoted_wa_id: str | None = None  # context.id del mensaje citado
    # De qué anuncio viene el lead (Click-to-WhatsApp). Meta lo adjunta SOLO al
    # primer mensaje de la conversación: si no se captura aquí, se pierde.
    referral: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def format_order_text(order: dict[str, Any]) -> str:
    """Texto legible a partir del carrito que el cliente comparte desde el
    catálogo de WhatsApp (mensaje tipo `order`).

    Sin esto el mensaje caía al genérico `[Mensaje tipo order]` — un marcador
    interno, no el pedido — y el bot contestaba solo a lo que el cliente
    escribió aparte ("¿hacen envíos al Callao?") ignorando el carrito entero
    que acababa de adjuntar. Meta NO manda el nombre del producto en este
    webhook, solo `product_retailer_id` (el SKU que el negocio cargó al
    catálogo): se muestra tal cual, sin inventar un nombre que no vino.
    """
    items = order.get("product_items") or []
    if not isinstance(items, list) or not items:
        return ""

    lines = ["🛒 El cliente compartió un carrito del catálogo:"]
    total = 0.0
    currency = ""
    total_conocido = True
    for item in items:
        if not isinstance(item, dict):
            continue
        qty_raw = item.get("quantity")
        try:
            qty = int(qty_raw) if qty_raw is not None else 1
        except (TypeError, ValueError):
            qty = 1
        retailer_id = str(item.get("product_retailer_id") or "").strip() or "sin id"
        price_raw = item.get("item_price")
        currency = str(item.get("currency") or currency or "").strip()
        try:
            price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None
        if price is not None:
            total += price * qty
            lines.append(f"- {qty}x id_catalogo={retailer_id} ({currency} {price:.2f} c/u)")
        else:
            total_conocido = False
            lines.append(f"- {qty}x id_catalogo={retailer_id}")

    if total_conocido and total:
        lines.append(f"Total del carrito: {currency} {total:.2f}")

    nota = str(order.get("text") or "").strip()
    if nota:
        lines.append(f'Nota del cliente en el carrito: "{nota}"')

    return "\n".join(lines)


def format_location_text(location: dict[str, Any]) -> str:
    """Texto legible + enlace a Maps a partir del payload `location` de Meta.

    Sin esto el mensaje caía a `[location]`, el CRM lo trataba como marcador
    interno y el asesor veía una burbuja vacía (Abel, sep 2026).
    """
    try:
        lat = float(location.get("latitude"))
        lng = float(location.get("longitude"))
    except (TypeError, ValueError):
        return ""

    lines = ["📍 Ubicación compartida"]
    name = str(location.get("name") or "").strip()
    address = str(location.get("address") or "").strip()
    if name:
        lines.append(name)
    if address and address.casefold() != name.casefold():
        lines.append(address)
    # `q=lat,lng` abre el pin exacto en Google Maps (móvil y escritorio).
    lines.append(f"https://maps.google.com/?q={lat},{lng}")
    return "\n".join(lines)


def parse_webhook_payload(payload: dict[str, Any]) -> list[InboundMessage]:
    """Extrae mensajes entrantes de un payload Cloud API."""
    out: list[InboundMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if change.get("field") != "messages":
                continue
            contacts = {c.get("wa_id"): c for c in (value.get("contacts") or [])}
            for msg in value.get("messages") or []:
                wa_id = msg.get("from", "")
                contact = contacts.get(wa_id) or {}
                profile = contact.get("profile") or {}
                name = profile.get("name", "")
                mtype = msg.get("type", "unknown")
                text = ""
                media_id = None
                mime = None
                caption = ""
                if mtype == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif mtype == "image":
                    image = msg.get("image") or {}
                    media_id = image.get("id")
                    mime = image.get("mime_type")
                    caption = image.get("caption") or ""
                    text = caption
                elif mtype == "audio":
                    audio = msg.get("audio") or {}
                    media_id = audio.get("id")
                    mime = audio.get("mime_type")
                elif mtype == "document":
                    doc = msg.get("document") or {}
                    media_id = doc.get("id")
                    mime = doc.get("mime_type")
                    caption = doc.get("caption") or doc.get("filename") or ""
                    text = caption
                elif mtype == "location":
                    text = format_location_text(msg.get("location") or {})
                elif mtype == "reaction":
                    # Reacción emoji a un mensaje. El emoji es el contenido; si
                    # viene vacío, es que el cliente RETIRÓ la reacción.
                    text = ((msg.get("reaction") or {}).get("emoji") or "").strip()
                elif mtype == "button":
                    text = (msg.get("button") or {}).get("text", "")
                elif mtype == "interactive":
                    interactive = msg.get("interactive") or {}
                    if interactive.get("type") == "button_reply":
                        text = (interactive.get("button_reply") or {}).get("title", "")
                    elif interactive.get("type") == "list_reply":
                        text = (interactive.get("list_reply") or {}).get("title", "")
                elif mtype == "order":
                    text = format_order_text(msg.get("order") or {})

                context = msg.get("context") or {}
                quoted_wa_id = context.get("id")

                # Anuncio de origen. Puede venir suelto o dentro de `context`
                # según el tipo de mensaje, así que se miran los dos sitios.
                referral = msg.get("referral") or context.get("referral")
                if not isinstance(referral, dict) or not referral:
                    referral = None

                out.append(
                    InboundMessage(
                        wa_id=wa_id,
                        contact_name=name,
                        wa_message_id=msg.get("id", ""),
                        message_type=mtype,
                        text=text or "",
                        media_id=media_id,
                        mime_type=mime,
                        caption=caption or "",
                        quoted_wa_id=quoted_wa_id,
                        referral=referral,
                        raw=msg,
                    )
                )
    return out


def extract_quoted_stub(msg: InboundMessage) -> Optional[str]:
    """Placeholder: el texto citado se resuelve luego desde CRM por wa_message_id."""
    return msg.quoted_wa_id
