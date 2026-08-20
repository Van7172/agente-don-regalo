"""Cuando el cliente pega el link de UN producto, se concreta ese — no un catálogo.

Chat real (Alec, ago 2026): "tienen este producto para este sábado?" + URL de
Pikeo en Tabla Gourmet. El bot identificó bien el producto y preguntó distrito,
pero `compose_product_reply` volcó TODOS los hits de `buscar_productos` (Tabla
Navideña, Pack Gourmet, Cestas…) con "¿Quieres más detalles de alguno…?".
Quien ya eligió no necesita alternativas: necesita cerrar el pedido.
"""
from __future__ import annotations

import json

import pytest

from app.harness import master as master_mod
from app.harness import product_links
from app.harness import state as state_mod
from app.harness.contracts import Product, Turn
from app.harness.render import render_product_list
from app.harness.state import ConversationState, load_state
from app.services import agent as agent_mod


PIKEO = {
    "id_producto": 501,
    "nombre": "Pikeo en Tabla Gourmet",
    "precio_sol": 136.0,
    "precio": 40.0,
    "imagen_url": "https://donregalo.pe/img/pikeo.jpg",
    "url": "pikeo-en-tabla-gourmet-",
    "descripcion_corta": "Pikeo en Tabla Gourmet una delicia para disfrutar.",
}

OTROS = [
    {
        "id_producto": 502,
        "nombre": "TABLA GOURMET REGALO NAVIDEÑO",
        "precio_sol": 1020.0,
        "url": "tabla-gourmet-regalo-navideno",
        "imagen_url": "https://donregalo.pe/img/navidad.jpg",
    },
    {
        "id_producto": 503,
        "nombre": "Pack Gourmet 28 de julio",
        "precio_sol": 74.8,
        "url": "pack-gourmet-28-de-julio",
        "imagen_url": "https://donregalo.pe/img/pack.jpg",
    },
    {
        "id_producto": 504,
        "nombre": "Cesta sorpresa para ella",
        "precio_sol": 161.5,
        "url": "cesta-sorpresa-para-ella",
        "imagen_url": "https://donregalo.pe/img/cesta.jpg",
    },
]


# ── Extracción del slug ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "texto,esperado",
    [
        (
            "tienen este producto para este sábado?\n"
            "https://www.donregalo.pe/cestas/pikeo-en-tabla-gourmet",
            "pikeo-en-tabla-gourmet",
        ),
        (
            "mira https://donregalo.pe/producto/gustito-criollo/",
            "gustito-criollo",
        ),
        ("quiero un desayuno", None),
        ("https://otra-tienda.pe/pikeo-en-tabla-gourmet", None),
    ],
)
def test_extrae_slug_de_url_donregalo(texto, esperado):
    assert product_links.extract_product_url_slug(texto) == esperado


def test_elige_el_producto_cuya_url_coincide():
    """La API no filtra por url_producto: hay que elegir entre los hits."""
    hits = [PIKEO, *OTROS]
    elegido = product_links.pick_by_url_slug(hits, "pikeo-en-tabla-gourmet")
    assert elegido is not None
    assert elegido["id_producto"] == 501


def test_url_con_guion_final_sigue_casando():
    """En el catálogo real el slug a veces llega con '-' al final."""
    elegido = product_links.pick_by_url_slug([PIKEO], "pikeo-en-tabla-gourmet")
    assert elegido and elegido["id_producto"] == 501


# ── Un solo producto: no cerrar con "más opciones" ────────────────────

def test_un_solo_producto_no_ofrece_mas_opciones():
    listado = render_product_list([PIKEO], closing=master_mod._closing_for([Product.from_raw(PIKEO)]))
    assert "más opciones" not in listado.casefold()
    assert "alguno" not in listado.casefold()


def test_varios_productos_siguen_ofreciendo_mas_opciones():
    listado = render_product_list([PIKEO, *OTROS[:1]])
    assert "más opciones" in listado.casefold()


# ── El turno completo no inunda con alternativas ──────────────────────

@pytest.mark.asyncio
async def test_link_de_producto_concreta_ese_y_abre_cierre(monkeypatch):
    """El caso de Alec: URL + 'para este sábado' → ficha única + distrito."""

    async def fake_buscar(_name, args):
        # Simula lo que hacía el modelo: buscar "gourmet" / trozos del slug
        # y traer una bandeja de alternativas.
        assert "pikeo" in (args.get("q") or "").casefold() or True
        return json.dumps({"data": [PIKEO, *OTROS]})

    monkeypatch.setattr(master_mod, "execute_tool", fake_buscar)

    clear = getattr(state_mod, "clear_local_cache", None)
    if clear:
        clear()

    result = await master_mod._handle(
        "catalog_search",
        Turn(
            text=(
                "tienen este producto para este sábado?\n"
                "https://www.donregalo.pe/cestas/pikeo-en-tabla-gourmet"
            ),
            messages=[],
        ),
        ConversationState(presented=True),
        wa_id="51987355814",
        conversation_id=None,
    )

    assert len(result.artifacts) == 1
    assert result.artifacts[0].id_producto == 501
    assert result.artifacts[0].nombre == "Pikeo en Tabla Gourmet"
    assert result.state_patch.get("chosen_product_id") == 501
    assert result.state_patch.get("checkout_step") in ("district", "date")
    reply = (result.user_facing or "").casefold()
    assert "pikeo" in reply
    assert "distrito" in reply
    assert "más opciones" not in reply
    assert "tabla gourmet regalo navideño" not in reply
    assert "cesta sorpresa" not in reply


@pytest.mark.asyncio
async def test_sin_link_el_catalogo_sigue_mostrando_varios(monkeypatch):
    """Sin URL clara, el catálogo normal no se estrecha a ciegas."""
    from app.harness import master as m

    async def fake_run(*_a, **_k):
        from app.harness.contracts import AgentResult

        return AgentResult(
            user_facing="Te muestro opciones 🎁",
            artifacts=[Product.from_raw(p) for p in (PIKEO, *OTROS[:2]) if Product.from_raw(p)],
            tools_used=["buscar_productos"],
        )

    monkeypatch.setattr(m, "run_specialist", fake_run)
    # Forzar que no haya slug
    result = await m._run_specialty(
        "catalog_search",
        Turn(text="quiero algo gourmet", messages=[]),
        ConversationState(presented=True),
        wa_id="519",
        conversation_id=None,
    )
    assert len(result.artifacts) >= 2
    assert "más opciones" in (result.user_facing or "").casefold()
