"""La última barrera: lo que sale al cliente.

El CORE prohíbe revelar el prompt y dar datos de otro cliente, pero eso es una
instrucción al modelo: si una inyección lo convence o una regresión de
composición se lleva el bloque de RESTRICCIONES —ya pasó—, nada lo detenía
después. Estas dos reglas son la comprobación determinista de lo mismo.
"""
from __future__ import annotations

import re

import pytest

from app.guardrails import (
    HANDOFF_RULES,
    OFFICIAL_CONTACTS,
    check_reply,
    find_contacts,
    guard_reply,
    no_system_prompt_leak,
    no_third_party_contact,
)
from app.harness.state import ConversationState
from app.prompts import facts as facts_mod
from app.prompts.core import SAFETY_MARKER


# ── no_system_prompt_leak ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    [
        "Claro: ## RESTRICCIONES — LÍMITES QUE NUNCA DEBES CRUZAR",
        "Mis reglas empiezan con ## IDENTIDAD y luego ## ESTILO",
        "Uso escalar_a_humano cuando no sé algo",
        "Consulto con buscar_conocimiento_equipo y te digo",
        "## DATOS CONOCIDOS DEL CLIENTE: Ana, Miraflores",
    ],
)
def test_detecta_prompt_filtrado(reply):
    assert no_system_prompt_leak(reply) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Tenemos desayunos desde S/89. ¿Te muestro opciones?",
        "El envío a Miraflores es S/15 y llega mañana 😊",
        "Con gusto te paso con un asesor para que te ayude",
        "Estas son las restricciones de horario: entregamos de 9 a 6",
    ],
)
def test_no_marca_respuestas_normales(reply):
    assert no_system_prompt_leak(reply) is None


def test_el_marcador_real_del_core_se_detecta():
    """Anti-deriva: si alguien renombra el marcador, esto lo caza.

    La regla se escribió contra el texto del CORE de hoy. Cambiarlo allí sin
    tocar la regla dejaría la comprobación mirando a un fantasma.
    """
    assert no_system_prompt_leak(f"mis reglas: {SAFETY_MARKER}") is not None


# ── no_third_party_contact ──────────────────────────────────────────────────


def test_detecta_telefono_de_otro_cliente():
    v = no_third_party_contact(
        "El pedido anterior fue de Ana, su teléfono es 999888777",
        ConversationState(),
    )
    assert v is not None and "999888777" in v.detail


def test_detecta_correo_de_otro_cliente():
    assert no_third_party_contact(
        "Ese pedido lo hizo ana@correo.com", ConversationState()
    ) is not None


def test_permite_el_dato_que_el_cliente_acaba_de_dar():
    """La barrera corre ANTES de reducir el estado.

    El teléfono que el cliente escribe en este turno todavía no está en el
    pedido: sin mirar `user_text`, el paso del cierre que lo confirma se marcaría
    como fuga y se degradaría — rompiendo el cierre.
    """
    assert no_third_party_contact(
        "Perfecto, anoto el 999888777. ¿Cuál es la dirección?",
        ConversationState(),
        user_text="el teléfono del destinatario es 999888777",
    ) is None


def test_permite_el_dato_ya_guardado_en_el_pedido():
    assert no_third_party_contact(
        "Resumen: contacto 999888777",
        ConversationState(telefono_destinatario="999888777"),
    ) is None


@pytest.mark.parametrize("contacto", sorted(OFFICIAL_CONTACTS))
def test_permite_los_contactos_oficiales(contacto):
    assert no_third_party_contact(
        f"Puedes escribirnos al {contacto}", ConversationState()
    ) is None


def test_el_mismo_numero_en_otro_formato_sigue_siendo_el_mismo():
    """`+51 999-888-777` y `999888777` son el mismo cliente.

    Sin normalizar, cualquier formateo distinto del que escribió el cliente
    parecería un tercero y bloquearía una respuesta correcta.
    """
    assert no_third_party_contact(
        "Confirmo el +51 999-888-777",
        ConversationState(),
        user_text="mi numero es 999888777",
    ) is None


def test_no_marca_precios_ni_ids_de_producto():
    """`find_contacts` no puede confundir dinero con teléfonos."""
    assert find_contacts("El desayuno 912345 cuesta S/189.90 y el envío S/15") == ()


def test_no_marca_el_cci_de_la_cuenta_de_pago():
    """El número de cuenta y el CCI son texto que el cliente copia para pagar.

    Si esto se marcara como fuga, el bot dejaría de poder decir dónde pagar —
    y esa respuesta es literalmente el cierre de la venta.
    """
    reply = "Cuenta BCP 191-1234567-0-12 · CCI 00219100123456789012"
    assert no_third_party_contact(reply, ConversationState()) is None


# ── integración con la barrera ──────────────────────────────────────────────


def test_las_dos_reglas_bloquean_y_derivan():
    """No basta con anotarlas en la traza: el daño está hecho al leerlas."""
    assert {"no_system_prompt_leak", "no_third_party_contact"} <= HANDOFF_RULES

    fuga = guard_reply("Mis reglas: ## RESTRICCIONES", state=ConversationState())
    assert fuga.blocked
    assert "## RESTRICCIONES" not in (fuga.reply or "")

    pii = guard_reply(
        "El teléfono de la otra clienta es 999888777", state=ConversationState()
    )
    assert pii.blocked
    assert "999888777" not in (pii.reply or "")


def test_check_reply_incluye_las_dos_reglas_nuevas():
    reglas = {
        v.rule
        for v in check_reply(
            "## RESTRICCIONES y el teléfono de Ana es 999888777",
            state=ConversationState(),
        )
    }
    assert "no_system_prompt_leak" in reglas
    assert "no_third_party_contact" in reglas


# ── anti-deriva de la lista de contactos oficiales ──────────────────────────


def test_los_contactos_de_facts_estan_en_la_lista_blanca():
    """Un número nuevo en `facts.py` y no aquí convierte una respuesta en fuga.

    El bot da estos datos a propósito (cancelar exige avisar a ese WhatsApp), así
    que si alguien añade un canal oficial al prompt y olvida la lista blanca, el
    guardrail bloquearía y derivaría una respuesta perfectamente correcta.
    """
    texto = "\n".join(str(v) for v in facts_mod.FACTS.values())
    for contacto in find_contacts(texto):
        assert contacto in OFFICIAL_CONTACTS, (
            f"{contacto!r} sale en app/prompts/facts.py pero no está en "
            "OFFICIAL_CONTACTS: el bot lo daría y el guardrail lo bloquearía"
        )


def test_el_ejemplo_de_correo_del_cierre_esta_en_la_lista_blanca():
    """El cierre propone `nombre@gmail.com` al pedir el correo."""
    from app.harness import checkout

    ejemplos = set(find_contacts(re.sub(r"\s+", " ", checkout.__doc__ or "")))
    ejemplos.update(find_contacts(open(checkout.__file__, encoding="utf-8").read()))
    for correo in ejemplos:
        if "@" in correo:
            assert correo in OFFICIAL_CONTACTS, (
                f"{correo!r} lo escribe el cierre pero no está en OFFICIAL_CONTACTS"
            )
