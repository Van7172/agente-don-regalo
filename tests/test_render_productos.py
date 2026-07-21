"""El listado de productos lo compone el código, no el modelo.

Durante semanas el formato vivió en el prompt ("la URL va sola en su línea"). Cada
vez que el modelo se desviaba, el cliente recibía un muro de enlaces en vez de
fotos. Los productos ya vienen tipados en `artifacts`: no hay razón para pedirle a
un LLM que los formatee.
"""
import pytest

from app.harness.contracts import Product
from app.harness.state import ConversationState
from app.harness.master import compose_product_reply, is_first_contact
from app.prompts.playbooks import WELCOME
from app.services.messenger import split_reply

PRODUCTOS = [
    Product(11, "Desayuno Dulce Despertar", 149.60, 44.0, "https://donregalo.pe/img/a.webp"),
    Product(22, "Desayuno Cumpleañero", 146.20, 43.0, "https://donregalo.pe/img/b.jpg"),
]


def _segmentos(reply):
    return split_reply(reply)


def test_aunque_el_modelo_pegue_la_url_al_texto_salen_fotos():
    """El bug real: URL + viñeta en la misma línea llegaban como enlaces."""
    desviado = (
        "Encontré estas opciones:\n"
        "https://donregalo.pe/img/a.webp • 🎁 *Desayuno Dulce Despertar* — S/149.60 ($44.00)\n"
        "https://donregalo.pe/img/b.jpg • 🎁 *Desayuno Cumpleañero* — S/146.20 ($43.00)\n"
        "¿Quieres más detalles de alguno?"
    )

    reply = compose_product_reply(desviado, PRODUCTOS)
    segmentos = _segmentos(reply)

    imagenes = [s for s in segmentos if s["type"] == "image"]
    assert len(imagenes) == 2, "cada producto tiene que salir como foto"
    assert imagenes[0]["url"] == "https://donregalo.pe/img/a.webp"
    assert "Desayuno Dulce Despertar" in imagenes[0]["caption"]


def test_se_conserva_la_intro_del_modelo():
    """El modelo aporta el tono; el listado lo pone el sistema."""
    reply = compose_product_reply("Te muestro las opciones más cercanas 😊", PRODUCTOS)

    assert reply.startswith("Te muestro las opciones más cercanas 😊")
    assert "¿Quieres más detalles de alguno" in reply
    assert "busque más opciones" in reply


def test_sin_intro_tambien_funciona():
    reply = compose_product_reply(None, PRODUCTOS)
    assert len([s for s in _segmentos(reply) if s["type"] == "image"]) == 2


def test_los_precios_del_listado_son_los_de_la_tool():
    """El modelo ya no los escribe, así que no puede inventarlos."""
    reply = compose_product_reply("Aquí tienes", PRODUCTOS)

    assert "S/149.60 ($44.00)" in reply
    assert "S/146.20 ($43.00)" in reply


def test_un_producto_sin_imagen_no_rompe_el_listado():
    sin_foto = [Product(33, "Cesta Criolla", 100.0, 29.4, "")]
    reply = compose_product_reply("Mira esto", sin_foto)

    assert "Cesta Criolla" in reply
    assert [s["type"] for s in _segmentos(reply)] == ["text"]


# ── Saludo de presentación ────────────────────────────────────────────

def test_el_primer_saludo_es_una_presentacion():
    nuevo = ConversationState()
    assert is_first_contact(nuevo, [{"role": "user", "content": "hola"}]) is True
    # El agente se llama como la tienda: "Regalito" se retiró en jul 2026 a
    # pedido del vendedor. Un diminutivo suelto en el saludo es la vía más
    # directa a que vuelva a colarse.
    assert "Regalito" not in WELCOME
    assert "Don Regalo" in WELCOME
    assert "¿en qué puedo ayudarte hoy?" in WELCOME.casefold()


def test_si_ya_hablamos_no_es_primer_contacto():
    """No nos presentamos dos veces en la misma conversación."""
    historial = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡Hola! 😊"},
        {"role": "user", "content": "hola de nuevo"},
    ]
    assert is_first_contact(ConversationState(), historial) is False


def test_ya_presentados_manda_el_estado_aunque_el_historial_se_haya_recortado():
    """La ventana de historial se recorta; el estado no.

    Al revés también: en un chat que ya existía pero al que nunca nos presentamos,
    el bot soltaba un "¡Hola! ¿En qué te ayudo?" genérico en vez de presentarse.
    """
    ya = ConversationState(presented=True)
    assert is_first_contact(ya, [{"role": "user", "content": "hola"}]) is False


@pytest.mark.asyncio
async def test_el_saludo_inicial_no_gasta_una_llamada_al_llm(monkeypatch):
    from app.harness import master as master_mod
    from app.harness import state as state_mod

    llamadas = []

    async def no_deberia_llamarse(*a, **kw):
        llamadas.append(a)
        raise AssertionError("el saludo no debe llamar al LLM")

    monkeypatch.setattr(master_mod, "run_specialist", no_deberia_llamarse)
    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: False)
    state_mod.clear_local_cache()

    reply = await master_mod.run_master(
        [{"role": "user", "content": "Hola"}], wa_id="51999", conversation_id=1
    )

    assert reply == WELCOME
    assert llamadas == []
    state_mod.clear_local_cache()
