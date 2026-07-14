"""El asesor puede mandar varias imágenes seguidas, sin escribir nada.

WhatsApp Cloud API no agrupa álbumes: cada foto es un mensaje. El CRM manda un
outbox por archivo, y el pie de foto (si lo hay) va solo en el primero. Un adjunto
sin texto es válido; un texto vacío sin adjunto, no.
"""
import pytest

from app.services import outbox_drain as od


@pytest.fixture
def wa(monkeypatch):
    espia = {"enviados": [], "guardados": []}

    async def fetch_media(key):
        return b"bytes:" + key.encode(), "image/jpeg"

    async def send_media(wa_id, kind, data, mime, filename="", caption=""):
        espia["enviados"].append(
            {"kind": kind, "filename": filename, "caption": caption, "mime": mime}
        )
        return f"wamid.{filename}"

    async def send_message(wa_id, content):
        espia["enviados"].append({"kind": "text", "content": content})
        return "wamid.text"

    monkeypatch.setattr(od.crm_http, "fetch_media", fetch_media)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: False)
    monkeypatch.setattr(od, "send_media", send_media)
    monkeypatch.setattr(od, "send_message", send_message)
    return espia


@pytest.mark.asyncio
async def test_varias_imagenes_sin_texto_salen_todas(wa):
    """El asesor arrastra tres fotos y no escribe nada."""
    for nombre in ("foto1.jpg", "foto2.jpg", "foto3.jpg"):
        await od.deliver_outbox(
            wa_id="51999",
            content="",
            msg_type="image",
            media_path=f"key/{nombre}",
            filename=nombre,
            conversation_id=1,
        )

    assert len(wa["enviados"]) == 3
    assert [e["filename"] for e in wa["enviados"]] == ["foto1.jpg", "foto2.jpg", "foto3.jpg"]
    assert all(e["kind"] == "image" for e in wa["enviados"])
    assert all(e["caption"] == "" for e in wa["enviados"]), "sin texto: sin pie de foto"


@pytest.mark.asyncio
async def test_el_pie_de_foto_va_solo_en_la_primera(wa):
    """Si el asesor sí escribe, el texto acompaña a la primera imagen, no a todas."""
    await od.deliver_outbox(
        wa_id="51999", content="Mira estas opciones", msg_type="image",
        media_path="key/a.jpg", filename="a.jpg", conversation_id=1,
    )
    await od.deliver_outbox(
        wa_id="51999", content="", msg_type="image",
        media_path="key/b.jpg", filename="b.jpg", conversation_id=1,
    )

    assert wa["enviados"][0]["caption"] == "Mira estas opciones"
    assert wa["enviados"][1]["caption"] == ""


@pytest.mark.asyncio
async def test_una_sola_imagen_sin_texto_tambien_vale(wa):
    await od.deliver_outbox(
        wa_id="51999", content="", msg_type="image",
        media_path="key/sola.jpg", filename="sola.jpg", conversation_id=1,
    )
    assert wa["enviados"] == [
        {"kind": "image", "filename": "sola.jpg", "caption": "", "mime": "image/jpeg"}
    ]


@pytest.mark.asyncio
async def test_un_texto_vacio_sin_adjunto_si_se_rechaza(wa):
    """Una foto sola es un mensaje; un mensaje de texto vacío no lo es."""
    with pytest.raises(ValueError, match="vacío"):
        await od.deliver_outbox(wa_id="51999", content="   ", msg_type="text")

    assert wa["enviados"] == []


@pytest.mark.asyncio
async def test_en_el_crm_una_imagen_sin_texto_queda_como_imagen(wa):
    """Sin esto, el hilo del asesor mostraría una burbuja vacía."""
    assert od._stored_content(content="", msg_type="image", filename="") == "[image]"
    assert od._stored_content(content="", msg_type="document", filename="guia.pdf") == "guia.pdf"
    assert od._stored_content(content="hola", msg_type="image", filename="") == "hola"
