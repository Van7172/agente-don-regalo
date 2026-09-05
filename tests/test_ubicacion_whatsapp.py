"""Ubicación de WhatsApp: debe verse en el CRM y llegar con coordenadas.

Chat real (Abel, sep 2026): el cliente mandó su pin y en el panel salía una
burbuja vacía. El parser no leía `type=location`, el contenido caía a
`[location]` y el inbox oculta esos marcadores como si fueran `[image]`.
"""
from __future__ import annotations

from app.channels.whatsapp.parser import format_location_text, parse_webhook_payload


def _payload(location: dict) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [
                                {"wa_id": "51916033589", "profile": {"name": "Abel"}}
                            ],
                            "messages": [
                                {
                                    "from": "51916033589",
                                    "id": "wamid.LOC1",
                                    "type": "location",
                                    "location": location,
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


def test_parsea_ubicacion_con_coordenadas_y_enlace():
    msg = parse_webhook_payload(
        _payload({"latitude": -12.0464, "longitude": -77.0428})
    )[0]
    assert msg.message_type == "location"
    assert "-12.0464" in msg.text
    assert "-77.0428" in msg.text
    assert "maps.google.com" in msg.text
    assert msg.text.strip()  # no puede quedar vacío: eso es la burbuja hueca


def test_parsea_ubicacion_con_nombre_y_direccion():
    msg = parse_webhook_payload(
        _payload(
            {
                "latitude": -12.12,
                "longitude": -77.03,
                "name": "Casa",
                "address": "Av. Larco 123, Miraflores",
            }
        )
    )[0]
    assert "Casa" in msg.text
    assert "Miraflores" in msg.text
    assert "maps.google.com" in msg.text


def test_format_location_sin_coords_no_inventa():
    assert format_location_text({}) == ""
    assert format_location_text({"latitude": None}) == ""
