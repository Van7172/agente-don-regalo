"""Envío de adjuntos del asesor (imagen, documento, nota de voz) a WhatsApp."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_sandbox.db")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test-verify")
os.environ.setdefault("DEFAULT_TENANT_SLUG", "test-tenant")
os.environ.setdefault("CRM_MODE", "local")
os.environ.setdefault("WATCHDOG_ENABLED", "0")

from app.services import messenger  # noqa: E402


class _FakeWhatsApp:
    def __init__(self):
        self.uploaded = None
        self.sent = None

    async def upload_media(self, data, mime_type="image/jpeg", filename="image.jpg"):
        self.uploaded = {"data": data, "mime": mime_type, "filename": filename}
        return "MEDIA123"

    async def send_image_id(self, to, media_id, caption=""):
        self.sent = {"kind": "image", "to": to, "media_id": media_id, "caption": caption}
        return {"messages": [{"id": "wamid.img"}]}

    async def send_audio_id(self, to, media_id):
        self.sent = {"kind": "audio", "to": to, "media_id": media_id}
        return {"messages": [{"id": "wamid.audio"}]}

    async def send_document_id(self, to, media_id, filename="", caption=""):
        self.sent = {
            "kind": "document", "to": to, "media_id": media_id,
            "filename": filename, "caption": caption,
        }
        return {"messages": [{"id": "wamid.doc"}]}


@pytest.fixture
def wa(monkeypatch):
    fake = _FakeWhatsApp()
    monkeypatch.setattr(messenger, "whatsapp_client", fake)
    return fake


@pytest.mark.asyncio
async def test_imagen_se_sube_y_se_envia_con_pie_de_foto(wa):
    mid = await messenger.send_media(
        "51999", "image", b"\x89PNG-bytes", "image/png", filename="foto.png", caption="Mira"
    )

    assert wa.uploaded["mime"] == "image/png"
    assert wa.uploaded["filename"] == "foto.png"
    assert wa.sent == {
        "kind": "image", "to": "51999", "media_id": "MEDIA123", "caption": "Mira",
    }
    assert mid == "wamid.img"


@pytest.mark.asyncio
async def test_documento_conserva_el_nombre_de_archivo(wa):
    mid = await messenger.send_media(
        "51999", "document", b"%PDF-1.4", "application/pdf", filename="boleta.pdf"
    )

    assert wa.sent["kind"] == "document"
    assert wa.sent["filename"] == "boleta.pdf"
    assert mid == "wamid.doc"


@pytest.mark.asyncio
async def test_audio_ya_compatible_no_se_reconvierte(wa):
    """ogg/opus le sirve a WhatsApp tal cual: convertirlo sería perder calidad y tiempo."""
    original = b"OggS-bytes"

    mid = await messenger.send_media("51999", "audio", original, "audio/ogg")

    assert wa.uploaded["data"] == original  # intacto
    assert wa.uploaded["mime"] == "audio/ogg"
    assert wa.sent["kind"] == "audio"
    assert mid == "wamid.audio"


@pytest.mark.asyncio
async def test_audio_no_admite_pie_de_foto(wa):
    """WhatsApp rechaza caption en audio; no debe colarse en la llamada."""
    await messenger.send_media("51999", "audio", b"OggS", "audio/mpeg", caption="hola")

    assert "caption" not in wa.sent


@pytest.mark.asyncio
async def test_webm_del_navegador_se_convierte_a_ogg(wa, monkeypatch):
    """Chrome graba en webm/opus y WhatsApp lo rechaza: hay que pasar por ffmpeg."""
    llamadas = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self, input=None):
            llamadas["input"] = input
            return b"OggS-convertido", b""

    async def fake_exec(*args, **kwargs):
        llamadas["argv"] = args
        return _FakeProc()

    monkeypatch.setattr(messenger.asyncio, "create_subprocess_exec", fake_exec)

    await messenger.send_media("51999", "audio", b"webm-bytes", "audio/webm;codecs=opus")

    assert llamadas["argv"][0] == "ffmpeg"
    assert "libopus" in llamadas["argv"]
    assert llamadas["input"] == b"webm-bytes"
    # A Meta va lo convertido, no el webm original.
    assert wa.uploaded["data"] == b"OggS-convertido"
    assert wa.uploaded["mime"] == "audio/ogg"
    assert wa.sent["kind"] == "audio"


@pytest.mark.asyncio
async def test_si_ffmpeg_falla_el_envio_falla_con_mensaje_claro(wa, monkeypatch):
    class _FakeProc:
        returncode = 1

        async def communicate(self, input=None):
            return b"", b"Invalid data found"

    async def fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(messenger.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(RuntimeError, match="No se pudo convertir el audio"):
        await messenger.send_media("51999", "audio", b"basura", "audio/webm")

    assert wa.sent is None  # no se envía nada a medias


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg no instalado en local")
@pytest.mark.asyncio
async def test_conversion_real_con_ffmpeg(wa):
    """Solo corre donde hay ffmpeg (la imagen Docker del sandbox lo instala)."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:a", "libopus", "-f", "webm", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    webm, _ = await proc.communicate()

    await messenger.send_media("51999", "audio", webm, "audio/webm")

    assert wa.uploaded["mime"] == "audio/ogg"
    assert wa.uploaded["data"].startswith(b"OggS")
