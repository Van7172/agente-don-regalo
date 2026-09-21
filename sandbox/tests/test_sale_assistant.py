from app.services.sale_assistant import FIELD_NAMES, _validate_suggestion


def test_sale_suggestion_accepts_only_values_with_literal_evidence():
    messages = [
        {
            "id": 10,
            "speaker": "cliente",
            "content": "Quiero el Box Dulce. Ya pagué S/ 89 por Yape.",
            "quoted_text": "",
        },
        {
            "id": 11,
            "speaker": "cliente",
            "content": "Para mañana en Miraflores, de 9 a 12.",
            "quoted_text": "",
        },
    ]
    raw = {
        "fields": {
            "producto": "Box Dulce",
            "monto_sol": 89,
            "distrito": "Miraflores",
            "fecha": "2026-09-21",
            "horario": "9:00 a 12:00",
            "motivo": "Pagó por Yape.",
        },
        "evidence": {
            "producto": [{"message_id": 10, "quote": "Box Dulce"}],
            "monto_sol": [{"message_id": 10, "quote": "S/ 89"}],
            "distrito": [{"message_id": 11, "quote": "Miraflores"}],
            "fecha": [{"message_id": 11, "quote": "mañana"}],
            "horario": [{"message_id": 11, "quote": "de 9 a 12"}],
            "motivo": [{"message_id": 10, "quote": "pagué S/ 89 por Yape"}],
        },
    }

    result = _validate_suggestion(raw, messages)

    assert result["fields"]["producto"] == "Box Dulce"
    assert result["fields"]["monto_sol"] == 89.0
    assert result["fields"]["fecha"] == "2026-09-21"
    assert result["fields"]["envio_sol"] is None
    assert "envio_sol" in result["missing"]


def test_sale_suggestion_discards_hallucinated_or_malformed_values():
    messages = [
        {"id": 20, "content": "La dirección es Calle Uno 123", "quoted_text": ""},
    ]
    raw = {
        "fields": {
            "producto": "Ramo premium",
            "monto_sol": "gratis",
            "pedido_temporal_id": "999-abc",
            "fecha": "mañana",
            "motivo": "x" * 400,
        },
        "evidence": {
            # El id existe, pero la cita no: no respalda el producto inventado.
            "producto": [{"message_id": 20, "quote": "Ramo premium"}],
            "motivo": [{"message_id": 999, "quote": "Calle Uno 123"}],
        },
    }

    result = _validate_suggestion(raw, messages)

    assert tuple(result["fields"]) == FIELD_NAMES
    assert all(value is None for value in result["fields"].values())
    assert result["missing"] == list(FIELD_NAMES)


def test_sale_suggestion_can_cite_quoted_text():
    messages = [
        {
            "id": 30,
            "content": "Sí, ese mismo",
            "quoted_text": "Desayuno Brunch Feliz Cumpleaños",
        }
    ]
    result = _validate_suggestion(
        {
            "fields": {"producto": "Desayuno Brunch Feliz Cumpleaños"},
            "evidence": {
                "producto": [
                    {"message_id": 30, "quote": "Desayuno Brunch Feliz Cumpleaños"}
                ]
            },
        },
        messages,
    )

    assert result["fields"]["producto"] == "Desayuno Brunch Feliz Cumpleaños"

