"""El CRM externo entrega solo memoria reciente y una pista segura del anuncio."""
from datetime import datetime, timedelta, timezone

from app.services import buffer


def test_historial_externo_respeta_ventana_y_limite(monkeypatch):
    monkeypatch.setattr(buffer.settings, "memory_window_hours", 12)
    monkeypatch.setattr(buffer.settings, "memory_max_messages", 2)
    now = datetime.now(timezone.utc)
    def entry(text, age):
        return {"role": "user", "content": text, "created_at": (now - timedelta(hours=age)).isoformat()}

    result = buffer._recent_external_history([
        entry("viejo", 24), entry("uno", 3), entry("dos", 2),
        {"role": "user", "content": "sin fecha"}, entry("tres", 1),
    ])
    assert [item["content"] for item in result] == ["dos", "tres"]


def test_anuncio_de_origen_no_se_trata_como_ficha_de_catalogo():
    ad = buffer._ad_origin({
        "source_id": "123", "headline": "Desayuno sorpresa",
        "body": "Envío para cumpleaños", "url": "https://example.com/track",
    })
    assert "Desayuno sorpresa" in ad
    assert "123" in ad
    assert "example.com" not in ad
