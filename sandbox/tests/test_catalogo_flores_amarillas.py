"""Flores Amarillas: aviso de corte same-day (sin PDF de preventa)."""
from __future__ import annotations

import pytest

from app.harness import master as master_mod
from app.harness.campaigns import (
    YELLOW_FLOWERS_CATALOG_URL,
    is_yellow_flowers_query,
    yellow_flowers_catalog_reply,
)
from app.harness.state import clear_local_cache, load_state
from app.services import buffer as buffer_mod
from app.services.messenger import split_reply


@pytest.mark.parametrize(
    "text",
    [
        "Flores amarillas?",
        "¿Tiene algo con temática de las flores amarillas?",
        "Quiero una flor amarilla",
        "¿Hay desayunos con flores de color amarillo?",
    ],
)
def test_reconoce_formas_reales_de_pedir_la_campana(text):
    assert is_yellow_flowers_query(text)


@pytest.mark.parametrize(
    "text",
    ["quiero flores", "busco flores moradas", "algo de color amarillo"],
)
def test_no_secuestra_consultas_genericas(text):
    assert not is_yellow_flowers_query(text)


def test_el_aviso_de_corte_no_incluye_el_pdf():
    reply = yellow_flowers_catalog_reply()
    assert YELLOW_FLOWERS_CATALOG_URL not in reply
    assert "Flores Amarillas" in reply
    assert "ya no estamos tomando pedidos" in reply


@pytest.mark.asyncio
async def test_primer_turno_avisa_el_corte_sin_pdf(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        raise AssertionError("Flores Amarillas no debe depender del LLM")

    async def no_tool(*_args, **_kwargs):
        raise AssertionError("Flores Amarillas no debe consultar el catálogo general")

    monkeypatch.setattr(master_mod, "_run_specialty", no_llm)
    monkeypatch.setattr(master_mod, "execute_tool", no_tool)
    clear_local_cache()

    reply = await master_mod.run_master(
        [{"role": "user", "content": "Hola, ¿tienen flores amarillas?"}],
        wa_id="51999",
        conversation_id=991,
    )

    assert reply is not None
    assert YELLOW_FLOWERS_CATALOG_URL not in reply
    assert "Flores Amarillas" in reply
    assert "ya no estamos tomando pedidos" in reply
    assert (await load_state(991)).campaign_slug == "flores-amarillas"


def test_el_pdf_historico_sigue_enviandose_como_documento():
    """Si algún mensaje viejo aún lleva el enlace, WhatsApp lo manda como PDF."""
    segments = split_reply(
        "Aquí está el catálogo:\n\n"
        f"{YELLOW_FLOWERS_CATALOG_URL}\n\n"
        "Dime cuál te gustó."
    )

    assert [segment["type"] for segment in segments] == ["text", "document"]
    assert segments[1]["url"] == YELLOW_FLOWERS_CATALOG_URL
    assert segments[1]["filename"] == "catalogodepreventaFLORESAMARILLAS_.pdf"
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
        "Aquí está:\n" + YELLOW_FLOWERS_CATALOG_URL + "\nElige tu favorito.",
        persist,
    )

    assert sent[0] == ("text", "Aquí está:")
    assert sent[1][0] == "document"
    assert sent[1][1] == YELLOW_FLOWERS_CATALOG_URL
    assert persisted[1][0] == "catalogodepreventaFLORESAMARILLAS_.pdf"
    assert persisted[1][2] == YELLOW_FLOWERS_CATALOG_URL
