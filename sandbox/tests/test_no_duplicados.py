"""Un mensaje no puede enviarse ni pintarse dos veces.

Bug real: un asesor escribió "No disculpe. Somos de Lima" y al cliente le llegó
tres veces. Dos caminos entregan la misma fila de `crm_outbox` — el push del CRM
al agente y el drenaje periódico, cada 12s — y la fila seguía en `pending`
durante TODA la llamada a la Cloud API. Ninguno la reclamaba antes de enviarla.
"""
import pytest

from app.channels.whatsapp.parser import InboundMessage
from app.services import buffer as buf
from app.services import outbox_drain as od


def _mock_entrega(monkeypatch, enviados: list, *, claim_gana: bool = True):
    async def fake_send(wa_id, content, *, reply_to=None):
        enviados.append(content)
        return f"wamid.{len(enviados)}"

    async def fake_claim(outbox_id):
        return claim_gana

    async def fake_noop(*a, **kw):
        return {}

    monkeypatch.setattr(od, "send_message", fake_send)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "claim_outbox", fake_claim)
    monkeypatch.setattr(od.crm_http, "append_outbound", fake_noop)
    monkeypatch.setattr(od.crm_http, "mark_outbox", fake_noop)
    monkeypatch.setattr(od.crm_http, "set_mode", fake_noop)


# ── Salida: el asesor ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sin_ganar_el_claim_no_se_envia_nada(monkeypatch):
    """El drenaje llega tarde a una fila que ya tiene el push: se retira."""
    enviados: list[str] = []
    _mock_entrega(monkeypatch, enviados, claim_gana=False)

    res = await od.deliver_outbox(
        wa_id="51982445818",
        content="No disculpe. Somos de Lima",
        conversation_id=1,
        outbox_id=7,
    )

    assert res["status"] == "skipped"
    assert enviados == [], "se mandó un mensaje que otro camino ya tenía"


@pytest.mark.asyncio
async def test_el_claim_se_pide_antes_de_hablar_con_meta(monkeypatch):
    """Reclamar después de enviar no sirve de nada: el duplicado ya salió."""
    orden: list[str] = []

    async def fake_send(wa_id, content, *, reply_to=None):
        orden.append("send")
        return "wamid.1"

    async def fake_claim(outbox_id):
        orden.append("claim")
        return True

    async def fake_noop(*a, **kw):
        return {}

    monkeypatch.setattr(od, "send_message", fake_send)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "claim_outbox", fake_claim)
    monkeypatch.setattr(od.crm_http, "append_outbound", fake_noop)
    monkeypatch.setattr(od.crm_http, "mark_outbox", fake_noop)
    monkeypatch.setattr(od.crm_http, "set_mode", fake_noop)

    await od.deliver_outbox(wa_id="519", content="hola", conversation_id=1, outbox_id=7)

    assert orden == ["claim", "send"]


@pytest.mark.asyncio
async def test_dos_entregas_de_la_misma_fila_solo_mandan_una(monkeypatch):
    """El escenario exacto del incidente: push y drenaje sobre el mismo outbox."""
    enviados: list[str] = []
    reclamados: set[int] = set()

    async def fake_send(wa_id, content, *, reply_to=None):
        enviados.append(content)
        return f"wamid.{len(enviados)}"

    async def claim_real(outbox_id):
        # Igual que el UPDATE condicional del CRM: gana el primero.
        if outbox_id in reclamados:
            return False
        reclamados.add(outbox_id)
        return True

    async def fake_noop(*a, **kw):
        return {}

    monkeypatch.setattr(od, "send_message", fake_send)
    monkeypatch.setattr(od.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(od.crm_http, "claim_outbox", claim_real)
    monkeypatch.setattr(od.crm_http, "append_outbound", fake_noop)
    monkeypatch.setattr(od.crm_http, "mark_outbox", fake_noop)
    monkeypatch.setattr(od.crm_http, "set_mode", fake_noop)

    for _ in range(3):
        await od.deliver_outbox(
            wa_id="51982445818",
            content="No disculpe. Somos de Lima",
            conversation_id=1,
            outbox_id=7,
        )

    assert enviados == ["No disculpe. Somos de Lima"]


@pytest.mark.asyncio
async def test_si_el_crm_no_sabe_reclamar_se_envia_igual(monkeypatch):
    """Si el CRM aún no tiene el endpoint, degradar — no callar al asesor.

    Fallar cerrado dejaría al equipo sin poder escribirle a nadie hasta que se
    suba el CRM. Fallar abierto vuelve al comportamiento de antes, que ya
    convivíamos con él.
    """
    enviados: list[str] = []
    _mock_entrega(monkeypatch, enviados)

    async def claim_404(outbox_id):
        raise RuntimeError("404 Not Found")

    monkeypatch.setattr(od.crm_http, "claim_outbox", claim_404)

    res = await od.deliver_outbox(
        wa_id="519", content="hola", conversation_id=1, outbox_id=7
    )

    assert res["status"] == "ok"
    assert enviados == ["hola"]


@pytest.mark.asyncio
async def test_sin_outbox_id_se_envia_igual(monkeypatch):
    """Un envío suelto (sin fila en cola) no tiene nada que reclamar."""
    enviados: list[str] = []
    _mock_entrega(monkeypatch, enviados, claim_gana=False)

    res = await od.deliver_outbox(wa_id="519", content="hola", conversation_id=1)

    assert res["status"] == "ok"
    assert enviados == ["hola"]


# ── Entrada: reintentos del webhook de Meta ───────────────────────────

def _inbound(wamid: str, text: str = "Son de Huaral !??") -> InboundMessage:
    return InboundMessage(
        wa_id="51982445818",
        wa_message_id=wamid,
        message_type="text",
        text=text,
        contact_name="willian160188",
    )


@pytest.mark.asyncio
async def test_el_mismo_wamid_solo_se_procesa_una_vez(monkeypatch):
    procesados: list[str] = []

    async def fake_local(msg):
        procesados.append(msg.wa_message_id)
        return {"status": "ok"}

    monkeypatch.setattr(buf, "_enqueue_local", fake_local)

    primero = await buf.enqueue_inbound(_inbound("wamid.ABC"))
    segundo = await buf.enqueue_inbound(_inbound("wamid.ABC"))

    assert procesados == ["wamid.ABC"]
    assert primero["status"] == "ok"
    assert segundo == {"status": "ignored", "reason": "duplicate"}


@pytest.mark.asyncio
async def test_wamids_distintos_con_el_mismo_texto_si_pasan(monkeypatch):
    """Escribir "sí" dos veces es normal: son dos mensajes, no un duplicado."""
    procesados: list[str] = []

    async def fake_local(msg):
        procesados.append(msg.wa_message_id)
        return {"status": "ok"}

    monkeypatch.setattr(buf, "_enqueue_local", fake_local)

    await buf.enqueue_inbound(_inbound("wamid.1", "sí"))
    await buf.enqueue_inbound(_inbound("wamid.2", "sí"))

    assert procesados == ["wamid.1", "wamid.2"]


@pytest.mark.asyncio
async def test_sin_wamid_no_se_descarta_nada(monkeypatch):
    """Sin id no se puede afirmar que sea repetido; filtra el CRM por contenido."""
    procesados: list[str] = []

    async def fake_local(msg):
        procesados.append(msg.text or "")
        return {"status": "ok"}

    monkeypatch.setattr(buf, "_enqueue_local", fake_local)

    await buf.enqueue_inbound(_inbound("", "hola"))
    await buf.enqueue_inbound(_inbound("", "hola"))

    assert len(procesados) == 2


def test_la_cache_de_wamids_no_crece_sin_limite():
    buf.reset_seen_wa_message_ids()
    for i in range(buf._SEEN_LIMIT + 500):
        buf._already_seen(f"wamid.{i}")
    assert len(buf._seen_wa_message_ids) == buf._SEEN_LIMIT
    # Se olvidan los más viejos, no los recientes.
    assert buf._already_seen(f"wamid.{buf._SEEN_LIMIT + 499}") is True
