"""De qué anuncio viene el lead (Click-to-WhatsApp).

Varios clientes abren con "¡Hola! Quiero más información." y el asesor lo lee
como un mensaje raro y seco. No lo escribieron ellos: es el *mensaje
predefinido* de un anuncio. En Ads Manager hay una campaña (DESAYUNOS | VENTAS)
con siete anuncios —PORTADA FAMILIA, PORTADA ELLA, PORTADA EL…— y **todos
comparten ese mismo texto**, así que por el mensaje no hay forma de saber cuál
fue: el asesor abre el chat a ciegas.

Meta sí lo dice, en un `referral` adjunto al PRIMER mensaje. Llegaba al webhook
y se tiraba entero. Aquí se comprueba que ahora se captura y se propaga.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_sandbox.db")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
os.environ.setdefault("DEFAULT_TENANT_SLUG", "test-tenant")
os.environ.setdefault("CRM_MODE", "local")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

from app.channels.whatsapp.parser import parse_webhook_payload  # noqa: E402
from app.crm import http_client as crm_http  # noqa: E402

# El anuncio real de Don Regalo, tal como se ve en el screenshot de Ads Manager.
REFERRAL = {
    "source_url": "https://fb.me/2abcXYZ",
    "source_id": "120214000000000000",
    "source_type": "ad",
    "headline": "DESAYUNOS SORPRESA 🎁",
    "body": "Delivery a todo Lima y Callao | Cumpleaños, aniversarios y más",
    "media_type": "image",
    "ctwa_clid": "ARBcdEfGhIjKlMnOpQ",
}


def _payload(msg_extra: dict) -> dict:
    mensaje = {
        "from": "51945692477",
        "id": "wamid.LEAD1",
        "type": "text",
        "text": {"body": "¡Hola! Quiero más información."},
    }
    mensaje.update(msg_extra)
    return {
        "entry": [{"changes": [{"field": "messages", "value": {
            "contacts": [{"wa_id": "51945692477", "profile": {"name": "MariaAngelica"}}],
            "messages": [mensaje],
        }}]}]
    }


# ── El parser ─────────────────────────────────────────────────────────

def test_el_anuncio_de_origen_se_captura_del_webhook():
    msg = parse_webhook_payload(_payload({"referral": REFERRAL}))[0]

    assert msg.text == "¡Hola! Quiero más información."
    assert msg.referral is not None, "el referral llegaba y se tiraba"
    assert msg.referral["source_id"] == "120214000000000000"
    assert msg.referral["headline"] == "DESAYUNOS SORPRESA 🎁"


def test_tambien_si_viene_dentro_de_context():
    """Según el tipo de mensaje, Meta lo anida en `context`."""
    msg = parse_webhook_payload(
        _payload({"context": {"referral": REFERRAL}})
    )[0]
    assert msg.referral is not None
    assert msg.referral["source_id"] == "120214000000000000"


def test_un_mensaje_normal_no_trae_anuncio():
    """La inmensa mayoría de los chats no vienen de un anuncio."""
    msg = parse_webhook_payload(_payload({}))[0]
    assert msg.referral is None


def test_un_referral_vacio_no_cuenta_como_anuncio():
    """`{}` no es un anuncio: guardarlo pintaría una tarjeta en blanco."""
    msg = parse_webhook_payload(_payload({"referral": {}}))[0]
    assert msg.referral is None


# ── El envío al CRM ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_el_referral_viaja_al_crm(monkeypatch):
    """En modo `external` el agente no mandaba `raw` ni nada parecido: el dato
    moría en el webhook aunque el CRM tuviera dónde guardarlo."""
    enviado = {}

    async def fake_request(method, path, **kwargs):
        enviado["path"] = path
        enviado["json"] = kwargs.get("json") or {}
        return {"conversation_id": 1, "contact_id": 1}

    monkeypatch.setattr(crm_http, "_request", fake_request)

    await crm_http.upsert_inbound(
        "51945692477",
        name="MariaAngelica",
        content="¡Hola! Quiero más información.",
        referral=REFERRAL,
    )

    assert enviado["json"]["referral"]["source_id"] == "120214000000000000"
    assert enviado["json"]["referral"]["headline"] == "DESAYUNOS SORPRESA 🎁"


@pytest.mark.asyncio
async def test_sin_anuncio_el_campo_va_en_null(monkeypatch):
    enviado = {}

    async def fake_request(method, path, **kwargs):
        enviado["json"] = kwargs.get("json") or {}
        return {}

    monkeypatch.setattr(crm_http, "_request", fake_request)

    await crm_http.upsert_inbound("519", content="Hola")

    assert enviado["json"]["referral"] is None
