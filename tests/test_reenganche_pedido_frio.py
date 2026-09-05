"""Pedido a medias de otro día: preguntar antes de empujar el cierre.

Chat real (sep 2026): el cliente dejó Gustito Consentidor + San Isidro sin
fecha; al volver otro día con «Hola», el bot saltó a «¿Para qué fecha…?»
sin consultar. Cortesía = preguntar si sigue vivo ese pedido.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.harness.checkout import advance_checkout
from app.harness.state import ConversationState

_LIMA = ZoneInfo("America/Lima")
HOY = date(2026, 9, 5)


def _epoch_lima(y: int, m: int, d: int, h: int = 18) -> float:
    return datetime(y, m, d, h, 0, tzinfo=_LIMA).timestamp()


def _pedido_ayer(**kw) -> ConversationState:
    return ConversationState(
        checkout_step="date",
        chosen_product_id=880,
        chosen_product_name="Gustito Consentidor",
        district="San Isidro",
        checkout_updated_at=_epoch_lima(2026, 9, 4),
        **kw,
    )


def test_hola_otro_dia_pregunta_si_seguir_con_el_producto():
    state = _pedido_ayer()
    state, reply, meta = advance_checkout(state, "Hola", today=HOY)
    assert not meta.get("handoff")
    assert state.checkout_step == "resume_confirm"
    assert state.checkout_resume_step == "date"
    low = reply.casefold()
    assert "gustito consentidor" in low
    assert "nuevo" in low
    assert "fecha" not in low
    assert "deseas" not in low


def test_mismo_dia_hola_retoma_el_dato_pendiente():
    """Sin cambio de día no hace falta la pregunta de reenganche."""
    state = ConversationState(
        checkout_step="date",
        chosen_product_name="Gustito Consentidor",
        district="San Isidro",
        checkout_updated_at=_epoch_lima(2026, 9, 5, 9),
    )
    state, reply, meta = advance_checkout(state, "Hola", today=HOY)
    assert state.checkout_step == "date"
    assert "fecha" in reply.casefold()
    assert "seguimos" in reply.casefold()


def test_seguimos_retoma_la_fecha():
    state = _pedido_ayer()
    state, _, _ = advance_checkout(state, "Hola", today=HOY)
    state, reply, meta = advance_checkout(state, "sí, seguimos", today=HOY)
    assert state.checkout_step == "date"
    assert state.checkout_resume_step in ("", None)
    assert "fecha" in reply.casefold()
    assert not meta.get("handoff")


def test_algo_nuevo_limpia_el_cierre():
    state = _pedido_ayer()
    state, _, _ = advance_checkout(state, "Hola", today=HOY)
    state, reply, meta = advance_checkout(state, "prefiero algo nuevo", today=HOY)
    assert state.checkout_step == "idle"
    assert state.chosen_product_id is None
    assert state.chosen_product_name == ""
    assert state.district == ""
    assert "hoy" in reply.casefold() or "buscar" in reply.casefold() or "ver" in reply.casefold()
