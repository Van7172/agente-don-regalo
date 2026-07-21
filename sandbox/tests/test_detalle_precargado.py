"""El "¿qué contiene?" se trae en código, no se le pide al modelo.

`GET /productos/buscar` solo devuelve `descripcion_corta`, que es copy de
marketing ("Sorprende con un Desayuno Regalo para enamorar"). La lista de items
solo la tiene `GET /productos/{id}`. Hasta ahora el único camino a ese dato era
que el modelo DECIDIERA llamar `detalle_producto`; cuando no lo hacía respondía
con el copy, se lo inventaba, o prometía consultarlo con un asesor.
"""
import json

import pytest

from app.harness import master
from app.harness.contracts import Turn
from app.harness.state import ConversationState

DETALLE = {
    "id_producto": 734,
    "nombre": "Gustito Consintiéndote con Amor",
    "precio_sol": 74.8,
    "imagen_url": "https://donregalo.pe/img/734.jpg",
    "descripcion_corta": "Sorprende con un Desayuno Regalo para enamorar",
    "descripcion": (
        "Contiene:\n- Croissant de pollo con papitas al hilo.\n- Bebida Bio.\n"
        "- Alfajor de 3 pisos.\n- Parfait de frutas."
    ),
}


def _con_productos(*productos: dict) -> ConversationState:
    return ConversationState(
        recent_products=[
            {"id_producto": p["id_producto"], "nombre": p["nombre"]} for p in productos
        ]
    )


# ── ¿De qué producto pregunta? ────────────────────────────────────────

def test_resuelve_el_producto_por_nombre():
    state = _con_productos(DETALLE, {"id_producto": 99, "nombre": "Ramo de Girasoles"})
    turn = Turn(text="¿qué contiene el Gustito?", messages=[])
    assert master._detalle_target(turn, state) == 734


def test_usa_el_producto_ya_elegido_si_no_lo_nombra():
    """"¿y qué trae?" sobre algo ya elegido: el resolutor mira lo MOSTRADO."""
    state = ConversationState(chosen_product_id=734, chosen_product_name="Gustito")
    assert master._detalle_target(Turn(text="¿y qué trae?", messages=[]), state) == 734


def test_si_es_ambiguo_no_adivina():
    """Con dos desayunos a la vista, precargar el equivocado es peor que nada."""
    state = _con_productos(
        {"id_producto": 1, "nombre": "Desayuno Sorpresa"},
        {"id_producto": 2, "nombre": "Desayuno Especial"},
    )
    assert master._detalle_target(Turn(text="¿qué contiene?", messages=[]), state) is None


# ── Lo que ve el modelo ───────────────────────────────────────────────

def test_el_contenido_llega_al_system_message():
    bloque = master._render_contenido(DETALLE)
    assert "Croissant de pollo" in bloque
    assert "Alfajor de 3 pisos" in bloque
    assert "734" in bloque


def test_sin_detalle_no_se_inventa_bloque():
    assert master._render_contenido(None) == ""
    assert master._render_contenido({"id_producto": 1, "nombre": "X"}) == ""


def test_el_bloque_prohibe_añadir_items():
    """El modelo tiende a completar la lista con lo que 'suele traer'."""
    bloque = master._render_contenido(DETALLE).casefold()
    assert "sin añadir ni quitar" in bloque


# ── El producto precargado es la ficha ────────────────────────────────

def test_el_precargado_sirve_de_artifact():
    arts = master._artifacts_from(DETALLE)
    assert [a.id_producto for a in arts] == [734]
    assert arts[0].precio_sol == 74.8


@pytest.mark.asyncio
async def test_si_el_modelo_no_llama_la_tool_igual_sale_la_ficha(monkeypatch):
    """Sin esto el turno saldría sin foto, sin precio y sin `chosen_product_*`."""
    from app.harness.contracts import AgentResult

    async def sin_tools(*a, **kw):
        # El modelo respondió de lo que ya tenía en el system: cero artifacts.
        return AgentResult(user_facing="Trae croissant de pollo, bebida y alfajor 😊")

    monkeypatch.setattr(master, "run_specialist", sin_tools)

    state = _con_productos(DETALLE)
    result = await master._run_specialty(
        "product_detail",
        Turn(text="¿qué contiene?", messages=[]),
        state,
        wa_id="519",
        fallback_artifacts=master._artifacts_from(DETALLE),
    )

    assert [a.id_producto for a in result.artifacts] == [734]
    assert "donregalo.pe/img/734.jpg" in result.user_facing


@pytest.mark.asyncio
async def test_lo_que_devuelve_la_tool_manda_sobre_el_precargado(monkeypatch):
    """Si el modelo SÍ llamó la tool, su resultado es el autoritativo."""
    from app.harness.contracts import AgentResult, Product

    otro = Product(id_producto=99, nombre="Ramo de Girasoles", precio_sol=50.0)

    async def con_tools(*a, **kw):
        return AgentResult(user_facing="Mira este", artifacts=[otro])

    monkeypatch.setattr(master, "run_specialist", con_tools)

    result = await master._run_specialty(
        "product_detail",
        Turn(text="¿y girasoles?", messages=[]),
        _con_productos(DETALLE),
        wa_id="519",
        fallback_artifacts=master._artifacts_from(DETALLE),
    )

    assert [a.id_producto for a in result.artifacts] == [99]


# ── El prefetch es best-effort ────────────────────────────────────────

@pytest.mark.asyncio
async def test_si_la_api_falla_se_sigue_sin_precarga(monkeypatch):
    async def revienta(name, args):
        raise RuntimeError("API caída")

    monkeypatch.setattr(master, "execute_tool", revienta)

    detalle = await master._prefetch_detalle(
        Turn(text="¿qué contiene?", messages=[]), _con_productos(DETALLE)
    )
    assert detalle is None


@pytest.mark.asyncio
async def test_precarga_desde_la_tool_real(monkeypatch):
    llamadas = []

    async def fake_tool(name, args):
        llamadas.append((name, args))
        return json.dumps({"data": DETALLE})

    monkeypatch.setattr(master, "execute_tool", fake_tool)

    detalle = await master._prefetch_detalle(
        Turn(text="¿qué contiene el Gustito?", messages=[]), _con_productos(DETALLE)
    )
    assert llamadas == [("detalle_producto", {"id_producto": 734})]
    assert "Croissant" in detalle["descripcion"]


# ── Una foto rota no puede tirar el dato ──────────────────────────────

@pytest.mark.asyncio
async def test_el_detalle_sobrevive_a_una_imagen_rota(monkeypatch):
    """Verificado contra la API real: `GET /productos/{id}` devuelve
    `imagen_url: null` y las cuatro variantes de `imagenes[]` dan 404. Antes eso
    vaciaba `data` y la pregunta se quedaba sin respuesta teniendo la lista de
    items delante."""
    from app.tools import catalog

    async def sin_imagenes_validas(client, products, limit=1):
        return []

    async def fake_get(client, url, params=None):
        return {"data": dict(DETALLE)}

    async def fake_rate(client):
        return 3.7

    monkeypatch.setattr(catalog, "valid_products", sin_imagenes_validas)
    monkeypatch.setattr(catalog, "get", fake_get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", fake_rate)

    import httpx

    async with httpx.AsyncClient() as client:
        res = await catalog._productos(client, "http://x/productos/734")

    assert res["data"]["id_producto"] == 734
    assert "Croissant" in res["data"]["descripcion"]
    assert res["data"]["imagen_url"] == "", "una foto rota no se manda a WhatsApp"
