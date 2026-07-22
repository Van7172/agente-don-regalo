"""Parseo de webhooks inbound de WhatsApp Cloud API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class InboundMessage:
    wa_id: str
    contact_name: str
    wa_message_id: str
    message_type: str  # text|image|audio|document|unknown
    text: str = ""
    media_id: str | None = None
    mime_type: str | None = None
    caption: str = ""
    quoted_wa_id: str | None = None  # context.id del mensaje citado
    # De qué anuncio viene el lead (Click-to-WhatsApp). Meta lo adjunta SOLO al
    # primer mensaje de la conversación: si no se captura aquí, se pierde.
    referral: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


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
