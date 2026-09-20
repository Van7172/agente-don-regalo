"""Las consultas de pago entregan la imagen oficial con QR."""
import hashlib
from pathlib import Path

import pytest

from app.guardrails import guard_reply
from app.harness import master
from app.harness.contracts import Turn
from app.harness.state import ConversationState
from app.payments import (
    PAYMENT_METHODS_IMAGE_URL,
    PAYMENT_RECEIPT_WHATSAPP,
    append_payment_methods_image,
    asks_for_payment_methods,
    payment_methods_reply,
)
from app.services import buffer as buffer_mod
from app.services.messenger import split_reply


@pytest.mark.parametrize(
    "text",
    [
        "¿Cuáles son sus métodos de pago?",
        "¿Cómo puedo pagar?",
        "¿Aceptan Yape?",
        "Yape?",
        "¿Yape o Plin?",
        "Pásame el QR",
        "¿Cuál es su número de Yape?",
        "Quiero pagar con Plin",
        "Pásame sus cuentas bancarias",
        "¿Tienen QR de pago?",
        "¿Puedo pagar contra entrega?",
    ],
)
def test_reconoce_consultas_reales_de_pago(text):
    assert asks_for_payment_methods(text)


@pytest.mark.parametrize("text", ["Ya pagué", "Aquí está mi comprobante", "pago aprobado"])
def test_un_pago_ya_realizado_no_vuelve_a_enviar_los_qr(text):
    assert not asks_for_payment_methods(text)


def test_la_imagen_oficial_esta_incluida_en_el_despliegue():
    image = Path("crm/public/assets/metodos-pago-qr.png")

    assert image.is_file()
    content = image.read_bytes()
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(content).hexdigest() == (
        "a5ad4c5de4b6af71a2a7fae6172e016840b14ac08d1a436522aa2b6036188e91"
    ), "no recomprimir: se deben conservar intactos los QR aprobados"


def test_la_respuesta_se_convierte_en_imagen_de_whatsapp():
    reply = payment_methods_reply("¿Cómo puedo pagar?")
    segments = split_reply(reply)

    assert [segment["type"] for segment in segments] == ["text", "image"]
    assert segments[1]["url"] == PAYMENT_METHODS_IMAGE_URL
    assert PAYMENT_RECEIPT_WHATSAPP in segments[1]["caption"]
    assert guard_reply(reply, state=ConversationState()).blocked is False


def test_contraentrega_se_corrige_sin_ofrecerla():
    reply = payment_methods_reply("¿Puedo pagar contra entrega?")

    assert "únicamente con pago anticipado" in reply
    assert "contra entrega" not in reply.casefold()


def test_turno_mixto_conserva_texto_comercial_y_anade_qr_una_vez():
    reply = append_payment_methods_image("El ramo cuesta lo indicado en su ficha.")
    repeated = append_payment_methods_image(reply)

    assert reply.startswith("El ramo")
    assert repeated.count(PAYMENT_METHODS_IMAGE_URL) == 1


@pytest.mark.asyncio
async def test_consulta_pura_no_depende_del_especialista(monkeypatch):
    async def no_commercial(*_args, **_kwargs):
        return None

    async def no_llm(*_args, **_kwargs):
        raise AssertionError("La respuesta de pago no debe depender del LLM")

    monkeypatch.setattr(master, "_commercial_intent", no_commercial)
    monkeypatch.setattr(master, "_run_specialty", no_llm)
    text = "¿Me comparte sus métodos de pago?"
    result = await master._handle(
        "policy_faq",
        Turn(text=text, messages=[{"role": "user", "content": text}]),
        ConversationState(),
    )

    assert PAYMENT_METHODS_IMAGE_URL in result.user_facing


@pytest.mark.asyncio
async def test_buffer_envia_y_persiste_la_imagen(monkeypatch):
    sent = []
    persisted = []

    async def fake_message(_wa_id, text):
        sent.append(("text", text))
        return "wamid.text"

    async def fake_image(_wa_id, url, caption=""):
        sent.append(("image", url, caption))
        return "wamid.image"

    async def persist(*, content, wa_message_id=None, media_url=None):
        persisted.append((content, wa_message_id, media_url))

    monkeypatch.setattr(buffer_mod, "send_message", fake_message)
    monkeypatch.setattr(buffer_mod, "send_image", fake_image)
    monkeypatch.setattr(buffer_mod, "human_delay", lambda _text: 0)

    await buffer_mod._send_reply_segments(
        "51999",
        991,
        payment_methods_reply("¿Cómo puedo pagar?"),
        persist,
    )

    assert sent[0][0] == "text"
    assert sent[1][0:2] == ("image", PAYMENT_METHODS_IMAGE_URL)
    assert persisted[1][2] == PAYMENT_METHODS_IMAGE_URL
