"""El nombre del adjunto tiene que sobrevivir a un fallo del push.

Un asesor adjuntó «catalogodedesayunos.pdf» y el panel dijo "No se envió". El
nombre viajaba SOLO en el payload que el CRM le hace POST al agente y nunca se
guardaba en `crm_outbox`. Mientras el push funciona no se nota; el problema es
qué pasa cuando no funciona, que es exactamente cuando entra el drenaje: la fila
se queda en 'pending' a propósito para que el agente la recoja, pero en la fila
el nombre ya no existe.

Resultado: el camino de RESCATE entregaba el PDF con el nombre "documento", sin
extensión. Varios clientes de WhatsApp no lo abren así, o sea que el asesor da
el envío por bueno y el cliente se queda con un archivo inservible — un fallo
que solo ocurre cuando nadie está mirando.
"""
import pytest

from app.services import outbox_drain as od
from app.services.messenger import _document_name


@pytest.fixture
def wa(monkeypatch):
    espia = {"enviados": []}

    async def fetch_media(key):
        return b"%PDF-1.4", "application/pdf"

    async def send_media(wa_id, kind, data, mime, filename="", caption=""):
        espia["enviados"].append({"kind": kind, "filename": filename, "mime": mime})
        return f"wamid.{filename or 'sin-nombre'}"

    monkeypatch.setattr(od.crm_http, "fetch_media", fetch_media)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: False)
    monkeypatch.setattr(od, "send_media", send_media)
    return espia


# ── El drenaje conserva el nombre ─────────────────────────────────────

@pytest.mark.asyncio
async def test_el_drenaje_manda_el_nombre_real_del_pdf(wa, monkeypatch):
    """La fila trae `filename_outbox`; antes el drenaje lo mandaba vacío."""
    fila = {
        "id_outbox": 42,
        "wa_id": "51997531710",
        "content_outbox": "",
        "type_outbox": "document",
        "media_path": "2026-08/abc.pdf",
        "filename_outbox": "catalogodedesayunos.pdf",
        "id_conversation": 319,
    }

    async def list_pending(*_a, **_kw):
        return [fila]

    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "list_pending_outbox", list_pending)
    monkeypatch.setattr(od.crm_http, "claim_outbox", lambda _id: _true())
    monkeypatch.setattr(od.crm_http, "mark_outbox", _noop)
    monkeypatch.setattr(od.crm_http, "append_outbound", _noop)
    monkeypatch.setattr(od.crm_http, "set_mode", _noop)

    enviados = await od.drain_pending_outbox()

    assert enviados == 1
    assert wa["enviados"][0]["filename"] == "catalogodedesayunos.pdf"


@pytest.mark.asyncio
async def test_un_crm_sin_migrar_no_rompe_el_envio(wa, monkeypatch):
    """Sin la columna 015 se degrada al nombre por defecto, no falla."""
    fila = {
        "id_outbox": 43,
        "wa_id": "51997531710",
        "content_outbox": "",
        "type_outbox": "document",
        "media_path": "2026-08/abc.pdf",
        "id_conversation": 319,
    }

    async def list_pending(*_a, **_kw):
        return [fila]

    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "list_pending_outbox", list_pending)
    monkeypatch.setattr(od.crm_http, "claim_outbox", lambda _id: _true())
    monkeypatch.setattr(od.crm_http, "mark_outbox", _noop)
    monkeypatch.setattr(od.crm_http, "append_outbound", _noop)
    monkeypatch.setattr(od.crm_http, "set_mode", _noop)

    assert await od.drain_pending_outbox() == 1
    assert wa["enviados"][0]["filename"] == ""


# ── Un documento nunca sale sin extensión ─────────────────────────────

def test_el_nombre_real_manda():
    assert _document_name("catalogodedesayunos.pdf", "application/pdf") == (
        "catalogodedesayunos.pdf"
    )
    assert _document_name("  guia.docx  ", "application/pdf") == "guia.docx"


def test_sin_nombre_la_extension_sale_del_mime():
    """"documento" a secas es un archivo que el cliente no puede abrir."""
    assert _document_name("", "application/pdf") == "documento.pdf"
    assert _document_name("", "application/pdf; charset=binary") == "documento.pdf"
    assert _document_name("", "text/csv") == "documento.csv"
    assert (
        _document_name(
            "",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        == "documento.xlsx"
    )


def test_un_mime_desconocido_no_inventa_extension():
    assert _document_name("", "application/octet-stream") == "documento"
    assert _document_name("", "") == "documento"


async def _true(*_a, **_kw):
    return True


async def _noop(*_a, **_kw):
    return None
