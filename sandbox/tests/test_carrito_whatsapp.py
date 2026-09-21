"""Carrito de catálogo de WhatsApp (mensaje tipo `order`).

Chat real (Josee, 21-09-2026): compartió un carrito con 3 artículos y
preguntó "quisiera saber el precio final de esto, y si hacen envios al
callao?" — el bot solo contestó lo de cobertura ("¡Sí llegamos a Callao! El
envío es S/19.99. ¿Qué regalo quieres enviar?"), como si el carrito nunca
hubiera llegado. El parser no leía `type=order`: el contenido caía al
genérico `[Mensaje tipo order]`, un marcador interno sin ni un dato del
carrito, y el precio final que pidió no se podía calcular de esa nada.
"""
from __future__ import annotations

from app.channels.whatsapp.parser import format_order_text, parse_webhook_payload


def _payload(order: dict) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [
                                {"wa_id": "5491139456584", "profile": {"name": "Josee"}}
                            ],
                            "messages": [
                                {
                                    "from": "5491139456584",
                                    "id": "wamid.ORDER1",
                                    "type": "order",
                                    "order": order,
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


def _carrito() -> dict:
    return {
        "catalog_id": "123456789",
        "product_items": [
            {
                "product_retailer_id": "girasoles-3",
                "quantity": 1,
                "item_price": 47.20,
                "currency": "USD",
            },
            {
                "product_retailer_id": "ferrero-12",
                "quantity": 1,
                "item_price": 8.50,
                "currency": "USD",
            },
        ],
    }


def test_parsea_el_carrito_con_precio_total():
    msg = parse_webhook_payload(_payload(_carrito()))[0]
    assert msg.message_type == "order"
    assert "girasoles-3" in msg.text
    assert "ferrero-12" in msg.text
    # 47.20 + 8.50 — el precio final que el cliente pidió sin que nadie sume a mano.
    assert "55.70" in msg.text
    assert msg.text.strip()  # no puede quedar vacío: es la burbuja hueca de antes


def test_la_nota_del_cliente_en_el_carrito_se_conserva():
    carrito = _carrito()
    carrito["text"] = "para hoy porfa"
    msg = parse_webhook_payload(_payload(carrito))[0]
    assert "para hoy porfa" in msg.text


def test_format_order_sin_items_no_inventa():
    assert format_order_text({}) == ""
    assert format_order_text({"product_items": []}) == ""


def test_un_precio_faltante_no_rompe_el_total():
    carrito = {
        "product_items": [
            {"product_retailer_id": "a", "quantity": 1, "item_price": 10.0, "currency": "USD"},
            {"product_retailer_id": "b", "quantity": 1},  # sin item_price
        ]
    }
    text = format_order_text(carrito)
    assert "id_catalogo=a" in text
    assert "id_catalogo=b" in text
    # Con un ítem sin precio no se puede saber el total real: no se inventa uno.
    assert "Total del carrito" not in text
