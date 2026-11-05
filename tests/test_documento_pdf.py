"""Un enlace a un PDF se envía como documento de WhatsApp, no como texto."""
from __future__ import annotations

import pytest

from app.services import buffer as buffer_mod
from app.services.messenger import split_reply

PDF_URL = "https://www.donregalo.pe/catalogo/catalogo-de-prueba.pdf"


def test_un_pdf_se_envia_como_documento():
    segments = split_reply(
        "Aquí está el catálogo:\n\n"
        f"{PDF_URL}\n\n"
        "Dime cuál te gustó."
    )

    assert [segment["type"] for segment in segments] == ["text", "document"]
    assert segments[1]["url"] == PDF_URL
    assert segments[1]["filename"] == "catalogo-de-prueba.pdf"
    assert segments[1]["caption"] == "Dime cuál te gustó."


@pytest.mark.asyncio
async def test_el_buffer_envia_y_persiste_el_pdf(monkeypatch):
    sent = []
    persisted = []

    async def fake_message(_wa_id, text):
        sent.append(("text", text))
        return "wamid.text"

    async def fake_document(_wa_id, url, *, filename="", caption=""):
        sent.append(("document", url, filename, caption))
        return "wamid.document"

    async def persist(*, content, wa_message_id=None, media_url=None):
        persisted.append((content, wa_message_id, media_url))

    monkeypatch.setattr(buffer_mod, "send_message", fake_message)
    monkeypatch.setattr(buffer_mod, "send_document", fake_document)
    monkeypatch.setattr(buffer_mod, "human_delay", lambda _text: 0)

    await buffer_mod._send_reply_segments(
        "51999",
        991,
        "Aquí está:\n" + PDF_URL + "\nElige tu favorito.",
        persist,
    )

    assert sent[0] == ("text", "Aquí está:")
    assert sent[1][0] == "document"
    assert sent[1][1] == PDF_URL
    assert persisted[1][0] == "catalogo-de-prueba.pdf"
    assert persisted[1][2] == PDF_URL
