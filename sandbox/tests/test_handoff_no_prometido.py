"""Un asesor se promete cuando el chat YA está cedido, y no antes.

El incidente (Ell Tall Bandido, 30-07): el cliente preguntó *"Cuánto está hello
Kitty"*. Nadie pidió un asesor. El modelo llamó a `escalar_a_humano` por su
cuenta, el bot mandó *"¡Claro! Te conecto con un asesor de nuestro equipo 🙏
Dame un momento, en seguida continúan contigo."* … y en el mensaje siguiente
estaba preguntando otra vez, con el CRM marcando "Don Regalo escuchando". La
promesa salió; el asesor, no.

Dos fallos independientes, uno detrás del otro:

1. **`handoff_policy` dejó pasar la derivación.** Es la red que existe justo para
   vetar las escaladas espontáneas del modelo, pero su caso por defecto es
   `allow=True` y `_SALES_CONTINUE_RE` no traía NI UNA palabra de precio ni la
   palabra "peluche" —una de las siete categorías padre—. Preguntar cuánto
   cuesta algo no le sonaba a venta en curso.
2. **La promesa iba antes que el cambio de estado.** `_say(_HANDOFF_WAIT_MSG)`
   corría primero y el paso a HUMAN después, condicional y sin `try`. Si el CRM
   no aceptaba —o no había por dónde cambiarlo—, el cliente se quedaba con la
   promesa y el bot seguía al mando.

Misma regla que el claim del outbox: se reclama antes de hablar.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.guardrails import handoff_policy
from app.harness.contracts import EscalateReason
from app.harness.taxonomy import parse_navegacion
from app.services import agent as agent_mod

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"


def _msg(texto: str) -> list[dict]:
    return [{"role": "user", "content": texto}]


@pytest.fixture
def espias(monkeypatch):
    estado = {"enviados": [], "avisos": [], "cedido": []}

    async def fake_send(_wa_id, text):
        estado["enviados"].append(text)
        return "wamid.1"

    async def fake_notify(text):
        estado["avisos"].append(text)

    from datetime import date

    import app.harness.holidays as hol

    monkeypatch.setattr(hol, "staff_offline", lambda today=None: False)
    monkeypatch.setattr(hol, "lima_today", lambda: date(2026, 7, 15))
    monkeypatch.setattr(agent_mod, "send_message", fake_send)
    monkeypatch.setattr(agent_mod, "notify_team", fake_notify)
    return estado


# ── 1. La red que dejó pasar la derivación ────────────────────────────

@pytest.mark.parametrize(
    "texto",
    [
        "Cuánto está hello Kitty",   # el mensaje literal del incidente
        "cuanto cuesta el peluche",
        "¿qué precio tiene?",
        "cuánto vale una orquídea",
        "precio de las coronas fúnebres",
        "quiero un peluche",
    ],
)
def test_preguntar_un_precio_no_es_motivo_para_ceder_el_chat(texto):
    assert handoff_policy(_msg(texto)).allow is False


@pytest.mark.parametrize(
    "texto",
    [
        "quiero hablar con un asesor",
        "pásame con una persona",
        "ya pagué, acá está el comprobante",
        "quiero cancelar mi pedido",
        "me pueden dar un descuento",
        # Con las dos cosas manda el pedido explícito, no el precio.
        "cuánto cuesta? mejor quiero cancelar",
    ],
)
def test_lo_que_si_debe_ceder_el_chat_sigue_cediendolo(texto):
    """El fallo caro en el otro sentido: suprimir una escalada legítima deja al
    cliente atrapado con el bot."""
    assert handoff_policy(_msg(texto)).allow is True


def test_la_lista_de_venta_cubre_las_categorias_reales():
    """El agujero era exactamente este: "peluche" no estaba, y Peluches es una
    de las siete categorías. Igual que `OFFICIAL_CONTACTS` contra `facts.py`,
    esto falla si la taxonomía crece y la lista se queda atrás."""
    navegacion = json.loads(
        (FIXTURES / "catalogo_navegacion.json").read_text(encoding="utf-8")
    )
    for categoria in parse_navegacion(navegacion):
        pregunta = f"cuánto cuesta {categoria['nombre'].lower()}"
        assert handoff_policy(_msg(pregunta)).allow is False, (
            f"preguntar el precio de {categoria['nombre']!r} escala a un humano"
        )


# ── 2. Primero se cede, después se promete ────────────────────────────

@pytest.mark.asyncio
async def test_si_no_se_puede_ceder_no_se_promete_nada(espias, monkeypatch):
    """El corazón del incidente: la promesa salía pasara lo que pasara."""
    async def cede_roto(*_a, **_k):
        return False

    monkeypatch.setattr(agent_mod, "_cede_a_humano", cede_roto)

    escalate = await agent_mod.perform_handoff(
        wa_id="51999", conversation_id=7, motivo="prueba"
    )

    assert escalate.ceded is False
    assert agent_mod._HANDOFF_WAIT_MSG not in espias["enviados"]
    assert espias["enviados"] == [], "no se le promete un asesor que no va a entrar"
    # Pero el equipo se entera igual: alguien tiene que mirarlo.
    assert espias["avisos"] and "FALLIDO" in espias["avisos"][0]


@pytest.mark.asyncio
async def test_si_se_cede_se_promete(espias, monkeypatch):
    async def cede_ok(*_a, **_k):
        return True

    monkeypatch.setattr(agent_mod, "_cede_a_humano", cede_ok)

    escalate = await agent_mod.perform_handoff(
        wa_id="51999", conversation_id=7, motivo="prueba"
    )

    assert escalate.ceded is True
    assert espias["enviados"] == [agent_mod._HANDOFF_WAIT_MSG]


@pytest.mark.asyncio
async def test_sin_crm_ni_sesion_no_hay_forma_de_ceder():
    """Ninguna de las dos ramas aplica: antes se saltaba el cambio de modo en
    silencio y se prometía igual."""
    assert await agent_mod._cede_a_humano(7, use_external_crm=False, session=None) is False
    assert await agent_mod._cede_a_humano(None, use_external_crm=True, session=None) is False


@pytest.mark.asyncio
async def test_si_el_crm_revienta_no_se_cede(monkeypatch):
    class FakeCrm:
        @staticmethod
        async def set_mode(*_a, **_k):
            raise RuntimeError("CRM caído")

    import app.crm.http_client as crm_http

    monkeypatch.setattr(crm_http, "set_mode", FakeCrm.set_mode)

    assert await agent_mod._cede_a_humano(7, use_external_crm=True, session=None) is False


def test_una_cesion_fallida_no_deja_al_cliente_en_silencio():
    """`HANDOFF_DONE` significa "ya se habló y manda un humano". Con la cesión
    fallida no es cierto ninguno de los dos, y callarse deja al cliente
    esperando a alguien que no existe."""
    from app.harness import master

    assert master.HANDOFF_FAILED_MSG
    assert "asesor" not in master.HANDOFF_FAILED_MSG.lower(), (
        "prometer dos veces lo que no llegó la primera es peor que no prometerlo"
    )


def test_el_contrato_de_escalate_asume_que_si_se_cedio():
    """Por defecto `True`: los call sites que no saben nada del transporte —y
    los tests viejos— siguen significando lo mismo que antes."""
    assert EscalateReason(motivo="x").ceded is True
