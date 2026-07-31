"""Buscar y no encontrar no es motivo para ceder el chat — y la frase se acorta.

El incidente (30-07): *"Cuánto está hello Kitty"* → el bot derivó a un humano.
Don Regalo **sí** vende Hello Kitty: `Peluche Kitty Sunshine` (id 1279) y
`Kitty y sus Rosas lilas Mágicas` (id 1226), ambos con precio y stock.

La causa no fue comprensión del modelo. El `q` de la API es una coincidencia
LITERAL de la frase entera, no una búsqueda por palabras. Verificado contra
producción:

    q=hello kitty  → 0      q=kitty         → 2
    q=hello        → 0      q=peluche kitty → 1

Sobraba una palabra. El fallback semántico que ya existía no lo salvó porque
**sin Qdrant configurado `buscar_semantico` cae a esa misma búsqueda literal**
(`search.py`), o sea que repetía la consulta que acababa de fallar.

Dos arreglos: acortar la frase cuando la entera no da nada, y una regla que
impide rendirse — una búsqueda vacía nunca deriva.
"""
from __future__ import annotations

import json

import pytest

from app.guardrails import customer_asked_for_human, empty_search_is_not_a_handoff
from app.tools import executor
from app.tools.executor import _degrade_query, execute_tool


def _msg(texto: str) -> list[dict]:
    return [{"role": "user", "content": texto}]


# ── Acortar la consulta ───────────────────────────────────────────────

def test_la_frase_del_incidente_llega_a_kitty():
    """Con la lista de paro vieja, los tres intentos se iban en "cuanto esta" y
    "esta hello" y nunca se probaba la única palabra que encuentra algo."""
    assert _degrade_query("Cuánto está hello Kitty")[0] == "kitty"
    assert _degrade_query("hello kitty")[0] == "kitty"


def test_la_palabra_de_la_categoria_va_la_ultima():
    """"peluche" devuelve los once peluches del catálogo; "kitty", el producto.
    La palabra que nombra la categoría es la que menos distingue."""
    assert _degrade_query("peluche de hello kitty") == ["kitty", "hello", "peluche"]
    assert _degrade_query("desayuno criollo para mi mamá")[0] == "criollo"
    assert _degrade_query("arreglo floral de girasoles")[0] == "girasoles"


def test_una_sola_palabra_ya_no_se_puede_acortar():
    assert _degrade_query("kitty") == []
    assert _degrade_query("cuanto cuesta") == [], "solo eran muletillas"


def test_quitar_las_muletillas_ya_es_un_intento():
    """"cuánto está kitty" tiene una sola palabra útil, pero la frase que se
    envió llevaba ruido: limpiarla es un intento nuevo, no un no-op."""
    assert _degrade_query("cuánto está kitty") == ["kitty"]


def test_no_se_hacen_intentos_sin_fin():
    """Cada intento es una llamada HTTP con el cliente esperando."""
    largo = "quiero un peluche grande de unicornio rosado con brillos para mi hija"
    assert len(_degrade_query(largo)) <= 3


# ── La escalera completa, sin red ─────────────────────────────────────

@pytest.fixture
def api_como_produccion(monkeypatch):
    """La API real: `q` casa la frase LITERAL. Sin Qdrant, como cuando el índice
    está frío — que es justo cuando el fallback semántico no sirve de nada."""
    CATALOGO = [
        {"id_producto": 1279, "nombre": "Peluche Kitty Sunshine", "precio_sol": 95.2},
        {"id_producto": 1226, "nombre": "Kitty y sus Rosas lilas Mágicas", "precio_sol": 129.2},
        {"id_producto": 290, "nombre": "Peluche Oso Loquito de Amor", "precio_sol": 64.6},
    ]
    llamadas: list[str] = []

    async def fake_buscar(_client, args):
        q = (args.get("q") or "").lower()
        llamadas.append(q)
        data = [p for p in CATALOGO if q and q in p["nombre"].lower()]
        return {"data": data, "total": len(data)}

    from app.tools import catalog, search

    monkeypatch.setattr(catalog, "buscar_productos", fake_buscar)
    monkeypatch.setattr(search, "get_qdrant", lambda: None)

    async def sin_semantica(_client, args):
        return await fake_buscar(_client, args)

    monkeypatch.setattr(search, "buscar_semantico", sin_semantica)
    return llamadas


@pytest.mark.asyncio
async def test_hello_kitty_encuentra_los_kitty(api_como_produccion):
    """El turno que se perdió: el catálogo tenía el producto todo el tiempo."""
    payload = json.loads(await execute_tool("buscar_productos", {"q": "hello kitty"}))

    ids = [p["id_producto"] for p in payload["data"]]
    assert ids == [1279, 1226]
    assert "hello kitty" in api_como_produccion[0], "primero se prueba lo que pidió"
    assert "kitty" in api_como_produccion, "y luego se acorta"


@pytest.mark.asyncio
async def test_lo_encontrado_al_acortar_va_marcado_como_aproximado(api_como_produccion):
    """No es literalmente lo que pidió: puede que no sea Hello Kitty con licencia.
    Que el bot lo diga en vez de hacerlo pasar por exacto."""
    payload = json.loads(await execute_tool("buscar_productos", {"q": "hello kitty"}))

    assert payload["aproximado"] is True
    assert payload["consulta_original"] == "hello kitty"
    assert payload["consulta_usada"] == "kitty"


@pytest.mark.asyncio
async def test_si_la_frase_entera_acierta_no_se_acorta_nada(api_como_produccion):
    payload = json.loads(await execute_tool("buscar_productos", {"q": "Kitty Sunshine"}))

    assert [p["id_producto"] for p in payload["data"]] == [1279]
    assert not payload.get("aproximado"), "esto SÍ es lo que pidió"
    assert len(api_como_produccion) == 1, "una sola llamada"


# ── Rendirse no es una opción ─────────────────────────────────────────

def test_una_busqueda_vacia_no_deriva():
    decision = empty_search_is_not_a_handoff(
        _msg("Cuánto está hello Kitty"),
        tools_used=["buscar_productos"],
        found_products=False,
    )
    assert decision.allow is False
    assert "alternativas" in decision.reason


def test_si_encontro_productos_la_derivacion_puede_ser_legitima():
    """El motivo entonces es otro —personalización, un reclamo— y decide
    `handoff_policy`, no esto."""
    assert empty_search_is_not_a_handoff(
        _msg("¿le pueden quitar el croissant?"),
        tools_used=["buscar_productos"],
        found_products=True,
    ).allow is True


def test_si_el_cliente_pide_un_humano_se_le_da():
    """El veto es contra rendirse, no contra atender lo que el cliente pidió."""
    for texto in ("quiero hablar con un asesor", "ya pagué, acá va el comprobante"):
        assert empty_search_is_not_a_handoff(
            _msg(texto), tools_used=["buscar_productos"], found_products=False
        ).allow is True
        assert customer_asked_for_human(_msg(texto)) is True


def test_sin_busqueda_de_por_medio_esta_regla_no_opina():
    assert empty_search_is_not_a_handoff(
        _msg("necesito factura para mi empresa"),
        tools_used=["metodos_pago"],
        found_products=False,
    ).allow is True


def test_las_palabras_de_categoria_salen_de_category_hints():
    """Una segunda lista se desincronizaría de la primera."""
    for terms, _slug in executor._CATEGORY_HINTS:
        for term in terms:
            if " " not in term:
                assert executor._norm_text(term) in executor._CATEGORY_WORDS
