"""El RUC y los datos fiscales no dependen de creatividad del modelo."""
import pytest

from app.business import (
    COMPANY_RUC,
    PICKUP_ADDRESS,
    asks_for_business_data,
    business_data_reply,
)
from app.harness import master
from app.harness.contracts import Turn
from app.harness.state import ConversationState


def _turn(text: str) -> Turn:
    return Turn(text=text, messages=[{"role": "user", "content": text}])


@pytest.mark.parametrize(
    "text",
    [
        "¿Cuál es su RUC?",
        "Necesito el ruc para hacer el pago",
        "¿Emiten factura?",
        "¿Cuál es el lugar de recojo?",
    ],
)
def test_reconoce_consultas_de_datos_oficiales(text):
    assert asks_for_business_data(text)


def test_ruc_no_coincide_con_palabras_parecidas():
    assert not asks_for_business_data("¿Tienen rúcula?")


@pytest.mark.asyncio
async def test_el_ruc_se_responde_sin_llm(monkeypatch):
    async def no_debe_ejecutarse(*_args, **_kwargs):
        raise AssertionError("Los datos oficiales no deben llegar a un LLM o tool")

    monkeypatch.setattr(master, "_run_specialty", no_debe_ejecutarse)
    result = await master._handle(
        "policy_faq",
        _turn("Hola, ¿me brinda su RUC?"),
        ConversationState(),
    )

    assert COMPANY_RUC in result.user_facing
    assert "empresa formal" in result.user_facing


def test_cada_consulta_recibe_solo_el_dato_que_pidio():
    invoice = business_data_reply("¿Emiten factura?")
    pickup = business_data_reply("¿Dónde puedo recoger?")

    assert invoice == "Sí, emitimos factura."
    assert pickup == f"Nuestro punto de recojo es *{PICKUP_ADDRESS}*."
    assert COMPANY_RUC not in invoice
