"""La cita de WhatsApp es contexto del sistema, y nuestros fallos no se muestran.

Incidente (25-07-2026, Rocío): la clienta respondió a la cotización de un asesor
(«Brunch de Feliz Cumpleaños modificado … + delivery 15.00 = 150») preguntando si
los productos venían dentro de la canasta que iba a pagar. El marcador de la cita
viajaba dentro del texto del cliente, así que:

  1. la palabra "delivery" —de la CITA, no de ella— enrutó el turno a cobertura;
  2. cobertura no halló ningún distrito en la frase y le citó de vuelta el
     marcador entero: *No ubico “[El cliente está respondiendo al mensaje:
     «Brunch de Feliz Cumpleaños modificado»” en nuestra lista 😊 ¿Lo buscas en
     Google Maps…?*

Tres candados, uno por capa: la cita se separa al percibir, cobertura no cita lo
que no parece un lugar, y si un marcador interno se cuela igualmente en la
respuesta, esa respuesta no sale: se cede el chat a un humano.
"""
import pytest

from app.guardrails import (
    SAFE_TECHNICAL_FALLBACK,
    check_reply,
    guard_reply,
    no_internal_context,
)
from app.harness import master as master_mod
from app.harness import state as state_mod
from app.harness.contracts import EscalateReason, Turn
from app.harness.coverage import _looks_like_place, resolve_coverage
from app.harness.quoting import build_quote_marker, internal_leak, split_quote
from app.harness.render import render_coverage
from app.harness.router import classify_rules
from app.harness.state import ConversationState, clear_local_cache

CITA = "Brunch de Feliz Cumpleaños modificado con chicharron y taml $23.15 (S/95.00) + canasta 40.00 + delivery 15.00 = 150"
PREGUNTA = "En este caso, como voy a pagar la canasta quisiera que los productos vengan dentro de este"
TURNO = f"{build_quote_marker(CITA)}\n{PREGUNTA}"


# ── 1. La cita se separa de lo que escribió el cliente ────────────

def test_split_quote_separa_cita_y_texto():
    texto, cita = split_quote(TURNO)
    assert texto == PREGUNTA
    assert cita == CITA


def test_split_quote_con_varios_mensajes_en_la_misma_rafaga():
    """El buffer junta mensajes seguidos: puede haber dos citas en un turno."""
    crudo = (
        f"{build_quote_marker('Ramo de Girasoles')}\nquiero este\n"
        f"{build_quote_marker('Terrario Panditas')}\ny este también"
    )
    texto, cita = split_quote(crudo)
    assert texto == "quiero este\ny este también"
    assert cita == "Ramo de Girasoles\nTerrario Panditas"


def test_perceive_deja_turn_text_limpio_y_la_cita_aparte():
    turn = master_mod.perceive([{"role": "user", "content": TURNO}])
    assert turn.text == PREGUNTA
    assert turn.quoted == CITA
    # Para resolver producto sí se usan las dos: responder a la foto ES nombrarlo.
    assert CITA in turn.text_with_quote and PREGUNTA in turn.text_with_quote


# ── 2. La cita no enruta ──────────────────────────────────────────

def test_una_palabra_de_la_cita_no_manda_el_turno_a_cobertura():
    """"delivery 15.00" lo escribió el asesor, no la clienta."""
    con_marcador = classify_rules(TURNO)
    limpio = classify_rules(PREGUNTA)

    assert limpio.intent != "coverage"
    # Y el turno real (percibido) tampoco: es lo que ve `run_master`.
    turn = master_mod.perceive([{"role": "user", "content": TURNO}])
    assert classify_rules(turn.text, quoted=turn.quoted).intent != "coverage"
    # El texto crudo era justo el que sí enrutaba mal.
    assert con_marcador.intent == "coverage"


def test_la_cita_sigue_sirviendo_para_saber_de_que_producto_habla():
    state = ConversationState(
        shown_product_ids=[11, 22],
        recent_products=[
            {"id_producto": 11, "nombre": "Terrario Familia Panditas"},
            {"id_producto": 22, "nombre": "Ramo de Girasoles"},
        ],
    )
    turno = f"{build_quote_marker('• 🎁 *Ramo de Girasoles* — S/120.00')}\nquiero este"
    turn = master_mod.perceive([{"role": "user", "content": turno}])

    assert classify_rules(turn.text, state, quoted=turn.quoted).intent == "checkout"
    assert master_mod._detalle_target(turn, state) == 22


# ── 3. Cobertura no cita de vuelta lo que no es un lugar ──────────

@pytest.mark.parametrize("candidato", ["Villa María del Triunfo", "2da de Palao", "sanisidro"])
def test_un_distrito_si_se_cita(candidato):
    assert _looks_like_place(candidato)


@pytest.mark.parametrize("candidato", [PREGUNTA, build_quote_marker(CITA), "quisiera pagar la canasta"])
def test_una_frase_del_cliente_no_se_cita(candidato):
    assert not _looks_like_place(candidato)


def test_sin_lugar_reconocible_se_pregunta_sin_citar():
    texto = render_coverage(suggest_maps=True, place_query="")
    assert "No ubico" not in texto
    assert "distrito" in texto


@pytest.mark.asyncio
async def test_cobertura_no_devuelve_el_mensaje_del_cliente(monkeypatch):
    """El caso exacto del incidente, aunque el turno llegue mal enrutado."""
    async def distritos(_client, _args):
        return {"data": [{"nombre": "Miraflores", "tarifa_sol": 17.0, "tarifa_usd": 5.0}]}

    monkeypatch.setattr("app.harness.coverage.catalog.distritos_cobertura", distritos)

    out = await resolve_coverage(PREGUNTA, ConversationState())
    texto = out["user_facing"]

    assert "No ubico" not in texto
    assert "canasta" not in texto
    assert internal_leak(texto) is None


# ── 4. Si el marcador se cuela igual, la respuesta no sale ────────

def test_ve_el_marcador_aunque_salga_cortado():
    """Tal cual salió en producción: `place_query` lo recortaba a 80 caracteres,
    así que el marcador llegó SIN su `»]` de cierre. Exigir el cierre habría
    dejado pasar justo el texto del incidente."""
    real = (
        "No ubico “[El cliente está respondiendo al mensaje: «Brunch de Feliz "
        "Cumpleaños modificado” en nuestra lista 😊 ¿Lo buscas un momento en "
        "Google Maps y me dices el distrito que aparece?"
    )
    assert internal_leak(real) is not None
    assert no_internal_context(real) is not None


def test_la_invariante_ve_el_marcador():
    fuga = f"No ubico “{build_quote_marker(CITA)}” en nuestra lista 😊"
    assert no_internal_context(fuga) is not None
    assert any(v.rule == "no_internal_context" for v in check_reply(fuga))


@pytest.mark.parametrize(
    "reply",
    [
        "Te confirmo el envío al [teléfono protegido]",
        "Sobre tu [image], ¿qué te muestro?",
        "Eso que dices: [contenido omitido por seguridad]",
    ],
)
def test_otros_marcadores_internos_tampoco_salen(reply):
    assert no_internal_context(reply) is not None


def test_una_respuesta_normal_no_dispara_la_invariante():
    limpia = "¡Sí llegamos a Miraflores! El envío es S/17.00. ¿Qué regalo quieres enviar? 🎁"
    assert no_internal_context(limpia) is None
    assert check_reply(limpia) == []


def test_guard_reply_descarta_la_prosa_con_marcador():
    fuga = f"No ubico “{build_quote_marker(CITA)}” en nuestra lista"
    guarded = guard_reply(fuga)
    assert guarded.blocked
    assert guarded.reply == SAFE_TECHNICAL_FALLBACK
    assert internal_leak(guarded.reply) is None


@pytest.mark.asyncio
async def test_un_fallo_tecnico_se_deriva_a_un_humano(monkeypatch):
    """No se le pinta el problema al cliente: entra un asesor."""
    clear_local_cache()
    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: False)

    async def specialty(*_a, **_kw):
        from app.harness.contracts import AgentResult

        return AgentResult(user_facing=f"No ubico “{build_quote_marker(CITA)}” en la lista")

    derivado = {}

    async def fake_handoff(**kwargs):
        derivado.update(kwargs)
        return EscalateReason(motivo=kwargs.get("motivo", ""))

    monkeypatch.setattr(master_mod, "_run_specialty", specialty)
    monkeypatch.setattr(master_mod, "perform_handoff", fake_handoff)

    reply = await master_mod.run_master(
        [{"role": "user", "content": "una consulta sobre el desayuno"}],
        wa_id="51999",
        conversation_id=77,
    )

    from app.services.agent import HANDOFF_DONE

    assert reply == HANDOFF_DONE, "el texto roto no se envía"
    assert "contexto interno" in derivado.get("motivo", "")
    clear_local_cache()


def test_turn_sin_cita_no_cambia_de_forma():
    turn = Turn(text="hola")
    assert turn.text_with_quote == "hola"
