"""Suite de regresión del harness (cobertura, releaser, checkout, render, router)."""
from __future__ import annotations

import time

import pytest

from app.harness.aliases import resolve_alias
from app.harness.contracts import Product
from app.harness.invariants import check_reply
from app.harness.master import _degrade_unsafe_reply
from app.harness.checkout import advance_checkout, parse_schedule, start_checkout, wants_checkout
from app.harness.coverage import extract_place_candidates, match_district
from app.harness.releaser import should_release_to_ai
from app.harness.render import render_coverage, render_product_list
from app.harness.router import classify_intent
from app.harness.state import ConversationState, clear_local_cache, save_state, load_state
from app.harness.toolsets import tools_for


def test_resolve_alias_palao():
    assert resolve_alias("2da de Palao") == "Callao"
    assert resolve_alias("Independencia") == "Independencia"


def test_match_district_fuzzy():
    districts = [
        {"nombre": "Independencia", "precio_sol": 17, "precio_usd": 5},
        {"nombre": "Callao", "precio_sol": 20, "precio_usd": 6},
    ]
    assert match_district("independencia creo", districts)["nombre"] == "Independencia"
    assert match_district("palao", districts)["nombre"] == "Callao"


def test_render_coverage_one_bubble_maps():
    text = render_coverage(suggest_maps=True, place_query="2da de Palao")
    assert "Google Maps" in text
    assert "Independencia" not in text or "tarifa" in text.lower()


def test_render_coverage_confirm_once():
    text = render_coverage(
        district="Independencia",
        covered=True,
        fee_sol=17,
        fee_usd=5,
        ask="¿Qué regalo quieres enviar? 🎁",
    )
    assert text.count("Independencia") == 1
    assert "S/17" in text


def test_render_products_dedupes_ids():
    products = [
        {"id_producto": 1, "nombre": "A", "precio_sol": 10, "precio": 3, "imagen_url": "https://x.com/a.jpg"},
        {"id_producto": 1, "nombre": "A", "precio_sol": 10, "precio": 3, "imagen_url": "https://x.com/a.jpg"},
        {"id_producto": 2, "nombre": "B", "precio_sol": 20, "precio": 6, "imagen_url": "https://x.com/b.jpg"},
    ]
    out = render_product_list(products)
    assert out.count("*A*") == 1
    assert out.count("*B*") == 1


def test_classify_coverage_vs_catalog():
    st = ConversationState()
    assert classify_intent("¿Qué zonas cubren el delivery en Lima?", st) == "coverage"
    assert classify_intent("busco peluches", st) == "catalog_search"
    assert classify_intent("hola", st) == "greet"


def test_classify_checkout_in_progress():
    st = ConversationState(checkout_step="schedule")
    assert classify_intent("09 AM a 11:00 AM", st) == "checkout"


def test_checkout_fsm_happy_path():
    st = ConversationState(chosen_product_name="Ramo", chosen_product_id=9)
    start_checkout(st)
    st, reply, _ = advance_checkout(st, "lo quiero")
    assert "distrito" in reply.lower() or st.checkout_step == "district"
    st.district = "Miraflores"
    st.checkout_step = "date"
    st, reply, _ = advance_checkout(st, "mañana")
    assert st.checkout_step == "schedule"
    assert "horario" in reply.lower()
    st, reply, _ = advance_checkout(st, "2")
    assert st.time_slot
    assert st.checkout_step == "card"
    # Sin tarjeta → se piden los datos que exige el pedido temporal.
    st, reply, _ = advance_checkout(st, "no")
    assert st.checkout_step == "recipient"
    assert st.dedicatoria == "Sin dedicatoria"
    st, reply, _ = advance_checkout(st, "Ana Pérez 999888777")
    assert st.checkout_step == "address"
    assert st.nombre_destinatario == "Ana"
    assert st.telefono_destinatario == "999888777"
    st, reply, _ = advance_checkout(st, "Av. Primavera 123, oficina 5")
    assert st.checkout_step == "contact"
    assert st.tipo == 1
    st, reply, _ = advance_checkout(st, "Luis Gómez luis@mail.com")
    assert st.checkout_step == "summary"
    assert st.email_cliente == "luis@mail.com"
    st, reply, meta = advance_checkout(st, "sí")
    assert meta.get("escalate")
    assert meta.get("create_order")
    assert st.checkout_step == "payment"


def test_parse_schedule_am():
    assert parse_schedule("09 AM a 11:00 AM")
    assert wants_checkout("me gusta esta")


def test_releaser_idle_20_min():
    st = ConversationState()
    now = time.time()
    assert should_release_to_ai(
        mode="HUMAN",
        human_support=True,
        state=st,
        last_human_at=now - 21 * 60,
        now=now,
    )
    assert not should_release_to_ai(
        mode="HUMAN",
        human_support=True,
        state=st,
        last_human_at=now - 5 * 60,
        now=now,
    )


def test_releaser_payment_exemption():
    st = ConversationState(checkout_step="payment", handoff_reason="pago yape")
    now = time.time()
    assert not should_release_to_ai(
        mode="HUMAN",
        human_support=True,
        state=st,
        last_human_at=now - 30 * 60,
        now=now,
    )
    assert should_release_to_ai(
        mode="HUMAN",
        human_support=True,
        state=st,
        last_human_at=now - 3 * 3600,
        now=now,
    )


def test_releaser_no_recupera_el_chat_recien_derivado():
    """Regresión (16-07): el bot dijo "te conecto con un asesor" y un minuto después
    respondía "¡Hola de nuevo! Ya estoy aquí para seguir ayudándote". Sin actividad
    del asesor no había ancla y se liberaba AL INSTANTE. El ancla es el momento de
    la derivación: solo se recupera tras el idle real.
    """
    now = time.time()
    st = ConversationState()
    st.handoff_at = now - 60  # derivado hace un minuto

    assert not should_release_to_ai(
        mode="HUMAN", human_support=True, state=st, last_human_at=None, now=now
    )

    st.handoff_at = now - 25 * 60  # el asesor lleva 25 min sin contestar
    assert should_release_to_ai(
        mode="HUMAN", human_support=True, state=st, last_human_at=None, now=now
    )


def test_releaser_sin_ancla_no_quita_el_chat_a_un_humano():
    """Sin nada que medir no se libera: un humano tiene el chat y siempre le queda
    "Devolver a Regalito". Antes esto devolvía True y el bot se metía de vuelta."""
    st = ConversationState()
    assert not should_release_to_ai(
        mode="HUMAN", human_support=True, state=st, last_human_at=None, now=time.time()
    )


def test_releaser_keep_human_pin():
    st = ConversationState(keep_human=True)
    now = time.time()
    assert not should_release_to_ai(
        mode="HUMAN",
        human_support=True,
        state=st,
        last_human_at=now - 99999,
        now=now,
    )


def test_toolsets_catalog_excludes_handoff_by_default():
    names = {t["function"]["name"] for t in tools_for("catalog_search")}
    assert "buscar_semantico" in names
    assert "escalar_a_humano" not in names


def test_toolsets_checkout_can_include_handoff():
    names = {t["function"]["name"] for t in tools_for("escalate", with_handoff=True)}
    assert "escalar_a_humano" in names


@pytest.mark.asyncio
async def test_local_state_roundtrip():
    clear_local_cache()
    st = ConversationState(district="Surco", shown_product_ids=[1, 2])
    await save_state(42, st)
    loaded = await load_state(42)
    assert loaded.district == "Surco"
    assert loaded.shown_product_ids == [1, 2]
    clear_local_cache()


@pytest.mark.asyncio
async def test_coverage_resolve_con_payload_real(monkeypatch):
    """Con la respuesta REAL de /distritos, no con una forma inventada.

    El mock anterior devolvía `{"nombre": ..., "precio_sol": ...}`, campos que la
    API no tiene. El test pasaba mientras el bot mandaba a todo el mundo a Google
    Maps porque ningún distrito hacía match.
    """
    import json
    import pathlib

    from app.harness import coverage as cov
    from app.tools import adapters

    crudo = json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "api" / "distritos.json").read_text(
            encoding="utf-8"
        )
    )

    async def fake_distritos(client, args):
        return adapters.districts_payload(crudo, 3.4)

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", fake_distritos)
    st = ConversationState()
    text = "Independencia creo que es el distrito\nDonde es 2da de palao?\nA ver si me ayudas porfa"
    result = await cov.resolve_coverage(text, st)

    # Resuelve el distrito, en UNA sola respuesta, con la tarifa en soles.
    assert result["structured"]["resolved_district"], "debería resolver el distrito"
    assert result["structured"]["covered"] is True
    assert result["state_patch"]["shipping_fee_sol"] > 0
    assert result["user_facing"].count("S/") == 1
    assert result["user_facing"].count("¿Confirmo") == 0


def test_extract_places():
    c = extract_place_candidates("Independencia\n2da de palao")
    assert len(c) >= 1


# ── Degradación por invariante rota ───────────────────────────────────

def _producto(pid=1235, nombre="Osito", sol=149.60, usd=44.0):
    return Product(
        id_producto=pid,
        nombre=nombre,
        precio_sol=sol,
        precio_usd=usd,
        imagen_url="https://donregalo.pe/img/osito.jpg",
        descripcion="Peluche de 60cm",
    )


def test_un_precio_inventado_no_llega_al_cliente():
    """El fallo más caro del negocio: el cliente da por bueno un precio falso.

    Antes `check_reply` lo anotaba en la traza y la respuesta salía igual: la
    violación quedaba registrada DESPUÉS de que el cliente ya la había leído.
    """
    artifacts = [_producto(sol=149.60)]
    reply = "¡Tengo justo lo tuyo! Te sale S/99.00, un precio genial 😊"
    violations = check_reply(reply, artifacts=artifacts)

    assert any(v.rule == "prices_are_sourced" for v in violations)

    seguro = _degrade_unsafe_reply(reply, violations, artifacts)
    assert "S/99" not in seguro, "el precio inventado sobrevivió a la degradación"
    assert "149" in seguro, "se perdió el precio real del listado"


def test_la_degradacion_conserva_las_fotos_del_listado():
    """Degradar no es tirar el mensaje: el listado lo arma el código y es fiable."""
    artifacts = [_producto()]
    violations = check_reply("Te sale S/12.00", artifacts=artifacts)

    seguro = _degrade_unsafe_reply("Te sale S/12.00", violations, artifacts)
    assert "https://donregalo.pe/img/osito.jpg" in seguro
    assert "Osito" in seguro


def test_contraentrega_se_degrada_aunque_no_haya_productos():
    """Prosa pura (policy): no hay listado que preservar, cae al respaldo fijo."""
    reply = "Puedes pagar contra entrega cuando llegue el repartidor."
    violations = check_reply(reply, artifacts=[])

    assert any(v.rule == "no_cash_on_delivery" for v in violations)

    seguro = _degrade_unsafe_reply(reply, violations, [])
    assert "contra entrega" not in seguro.lower()
    assert not check_reply(seguro), "el respaldo viola sus propias invariantes"


def test_una_respuesta_limpia_no_se_toca():
    artifacts = [_producto(sol=149.60)]
    reply = "Mira esta opción 😊"
    assert _degrade_unsafe_reply(reply, check_reply(reply, artifacts=artifacts), artifacts) == reply


def test_las_invariantes_leves_no_degradan_la_respuesta():
    """Repetir un producto es molesto, pero no justifica romper el mensaje."""
    state = ConversationState(shown_product_ids=[1235])
    artifacts = [_producto(1235)]
    reply = "Aquí van 😊"
    violations = check_reply(reply, state=state, artifacts=artifacts)

    assert any(v.rule == "no_repeated_products" for v in violations)
    assert _degrade_unsafe_reply(reply, violations, artifacts) == reply
