"""Pedido temporal: del estado del cierre al panel de donregalo.

Cubre las piezas deterministas (parseo de destinatario/dirección/contacto,
normalización de fecha y hora) y la creación best-effort contra la API mockeada.
Ningún test sale a la red.
"""
from __future__ import annotations

from datetime import date

import httpx
import pytest

from app.delivery_windows import hora_entrega_api, schedule_options_for
from app.harness import orders as orders_mod
from app.harness.checkout import (
    advance_checkout,
    parse_address,
    parse_contact,
    parse_recipient,
    parse_schedule,
    split_name,
)
from app.harness.orders import (
    build_body,
    create_from_state,
    display_fecha,
    normalize_fecha,
)
from app.harness.state import ConversationState

HOY = date(2026, 7, 14)  # martes


def _estado_completo(**over) -> ConversationState:
    base = dict(
        chosen_product_id=1235,
        chosen_product_name="Ramo de Girasoles",
        district="Miraflores",
        id_distrito=5,
        shipping_fee_sol=15.0,
        date="2026-07-20",
        time_slot="09:00 AM a 11:00 AM",
        dedicatoria="Feliz cumpleaños",
        nombre_destinatario="Ana",
        apellidos_destinatario="Pérez",
        telefono_destinatario="999888777",
        direccion="Av. Primavera 123, ref parque",
        tipo=0,
        nombre_cliente="Luis",
        apellidos_cliente="Gómez",
        email_cliente="luis@mail.com",
    )
    base.update(over)
    return ConversationState(**base)


# ── Parseo determinista ───────────────────────────────────────────────

def test_split_name():
    assert split_name("Ana Pérez") == ("Ana", "Pérez")
    assert split_name("Ana") == ("Ana", "")
    assert split_name("  ") == ("", "")


def test_parse_recipient_nombre_y_telefono():
    assert parse_recipient("Ana Pérez 999888777") == ("Ana", "Pérez", "999888777")


def test_parse_recipient_limpia_muletillas():
    nombre, apellidos, tel = parse_recipient("María López, teléfono 999111222")
    assert (nombre, apellidos) == ("María", "López")
    assert tel == "999111222"


def test_parse_address_detecta_oficina():
    direccion, tipo = parse_address("Av. Primavera 123, oficina 5")
    assert tipo == 1
    assert "Primavera" in direccion


def test_parse_address_casa_por_defecto():
    _, tipo = parse_address("Calle Los Olivos 456")
    assert tipo == 0


def test_parse_contact_extrae_email():
    nombre, _, email = parse_contact("Luis Gómez luis@mail.com")
    assert nombre == "Luis"
    assert email == "luis@mail.com"


def test_parse_contact_sin_email():
    assert parse_contact("solo mi nombre")[2] == ""


# ── Hora de entrega (franja → API) ────────────────────────────────────

@pytest.mark.parametrize(
    "slot,esperado",
    [
        ("07:00 AM a 09:00 AM", "07:00"),
        ("09:00 AM a 11:00 AM", "10:00"),
        ("11:00 AM a 02:00 PM", "13:00"),
        ("02:00 PM a 05:00 PM", "16:00"),
        ("04:00 PM a 07:00 PM", "16:00"),
        ("10:00", "10:00"),
        ("", None),
        ("cuando sea", None),
    ],
)
def test_hora_entrega_api(slot, esperado):
    assert hora_entrega_api(slot) == esperado


# ── Normalización de fecha ────────────────────────────────────────────

@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("2026-07-20", "2026-07-20"),
        ("20/07", "2026-07-20"),
        ("20/07/2026", "2026-07-20"),
        ("mañana", "2026-07-15"),
        ("pasado mañana", "2026-07-16"),
        ("hoy", "2026-07-14"),
        ("20 de julio", "2026-07-20"),
        ("viernes", "2026-07-17"),
        ("cuando sea", None),
        ("", None),
    ],
)
def test_normalize_fecha(texto, esperado):
    assert normalize_fecha(texto, today=HOY) == esperado


def test_normalize_fecha_sin_anio_no_salta_silenciosamente_al_siguiente():
    assert normalize_fecha("20 de enero", today=HOY) == "2026-01-20"
    assert normalize_fecha("13/07", today=HOY) == "2026-07-13"


def test_normalize_fecha_ambigua_no_elige_la_primera():
    assert normalize_fecha("20/07 o 21/07", today=HOY) is None
    assert normalize_fecha("mañana o pasado mañana", today=HOY) is None
    assert normalize_fecha("viernes o sábado", today=HOY) is None
    assert normalize_fecha("mañana o 20/07", today=HOY) is None


def test_fsm_guarda_manana_como_fecha_iso_y_la_muestra_legible():
    state = ConversationState(checkout_step="date", district="Miraflores")

    state, reply, _ = advance_checkout(state, "mañana", today=date(2026, 7, 17))

    assert state.date == "2026-07-18"
    assert display_fecha(state.date) == "18/07/26"
    assert state.checkout_step == "schedule"
    assert "Mañana temprano" in reply


def test_fsm_no_avanza_con_fecha_pasada_o_irreconocible():
    for text in ("16/07/2026", "cuando sea"):
        state = ConversationState(checkout_step="date", district="Miraflores")
        state, reply, _ = advance_checkout(
            state, text, today=date(2026, 7, 17)
        )
        assert state.checkout_step == "date"
        assert state.date == ""
        assert "fecha" in reply.casefold()


def test_viernes_no_ofrece_horario_temprano_y_renumera():
    friday = date(2026, 7, 17)
    options = schedule_options_for(friday)

    assert "Mañana temprano" not in options
    assert options.splitlines()[0].startswith("1. Mañana ")
    assert len(options.splitlines()) == 4
    assert parse_schedule("1", friday) == "09:00 AM a 11:00 AM"


def test_otro_dia_conserva_cinco_horarios():
    saturday = date(2026, 7, 18)
    options = schedule_options_for(saturday)

    assert "1. Mañana temprano" in options
    assert len(options.splitlines()) == 5
    assert parse_schedule("1", saturday) == "07:00 AM a 09:00 AM"


# ── Construcción del cuerpo ───────────────────────────────────────────

def test_build_body_completo():
    body = build_body(_estado_completo(), wa_id="51999888777")
    assert body is not None
    assert body["nombre_cliente"] == "Luis"
    assert body["email_cliente"] == "luis@mail.com"
    assert body["telefono_cliente"] == "51999888777"
    assert body["nombre_destinatario"] == "Ana"
    assert body["fecha_entrega"] == "2026-07-20"
    assert body["hora_entrega"] == "10:00"
    assert body["id_distrito"] == 5
    assert body["tipo"] == 0
    assert body["id_producto"] == 1235
    assert body["delivery"] == 15.0
    assert body["observaciones"].startswith("[Agente IA]")


def test_build_body_sin_email_no_se_crea():
    assert build_body(_estado_completo(email_cliente=""), wa_id="51999") is None


def test_build_body_fecha_irreconocible_no_se_crea():
    assert build_body(_estado_completo(date="algún día"), wa_id="51999") is None


def test_build_body_apellido_vacio_usa_placeholder():
    body = build_body(_estado_completo(apellidos_cliente=""), wa_id="51999")
    assert body is not None
    assert body["apellidos_cliente"] == "."


# ── Creación best-effort ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_from_state_ok(monkeypatch):
    capturado = {}

    async def fake_post(client, body):
        capturado["body"] = body
        return {"success": True, "data": {"id_pedido_temporal": 123, "nombre_distrito": "Miraflores"}}

    monkeypatch.setattr(orders_mod.orders_tool, "crear_pedido_temporal", fake_post)

    data = await create_from_state(_estado_completo(), "51999888777")
    assert data is not None
    assert data["id_pedido_temporal"] == 123
    assert capturado["body"]["email_cliente"] == "luis@mail.com"


@pytest.mark.asyncio
async def test_create_from_state_no_explota_si_la_api_falla(monkeypatch):
    async def fake_post(client, body):
        request = httpx.Request("POST", "http://x")
        response = httpx.Response(422, request=request, text="validación")
        raise httpx.HTTPStatusError("422", request=request, response=response)

    monkeypatch.setattr(orders_mod.orders_tool, "crear_pedido_temporal", fake_post)

    # No lanza: el handoff al asesor debe seguir.
    assert await create_from_state(_estado_completo(), "51999888777") is None


@pytest.mark.asyncio
async def test_create_from_state_incompleto_no_llama_api(monkeypatch):
    llamado = {"si": False}

    async def fake_post(client, body):
        llamado["si"] = True
        return {}

    monkeypatch.setattr(orders_mod.orders_tool, "crear_pedido_temporal", fake_post)

    data = await create_from_state(_estado_completo(email_cliente=""), "51999888777")
    assert data is None
    assert llamado["si"] is False, "sin datos completos no se llama a la API"
