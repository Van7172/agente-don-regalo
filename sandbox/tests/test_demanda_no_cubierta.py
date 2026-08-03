"""Lo que nos piden y no tenemos se cuenta, y contarlo no puede costar nada.

`aproximado` se marcaba en el executor para que el bot dijera "te muestro lo más
cercano", vivía dentro del resultado de la tool ese turno y moría ahí. Sabíamos
decirle al cliente que no lo teníamos y no sabíamos cuántos lo habían pedido —
que es justo la pregunta de qué producto lanzar.

Lo que se protege aquí no es que la fila se escriba (eso es un INSERT), sino las
tres formas en que este módulo podría hacer daño: añadirle latencia al turno,
tumbarlo con una excepción, o guardar el término equivocado y mandar a comprar
stock de algo que sí está en el catálogo.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import demand
from app.tools import executor


@pytest.fixture
def crm_encendido(monkeypatch):
    """El conftest apaga el CRM en toda la suite; aquí hace falta encendido."""
    monkeypatch.setattr(demand.crm_http, "crm_enabled", lambda: True)


@pytest.fixture
def anotadas(monkeypatch, crm_encendido) -> list[dict]:
    """Captura lo que se habría mandado al CRM."""
    enviadas: list[dict] = []

    async def fake_record(query, *, resultado, n_resultados, categoria, conversation_id):
        enviadas.append(
            {
                "query": query,
                "resultado": resultado,
                "n_resultados": n_resultados,
                "categoria": categoria,
                "conversation_id": conversation_id,
            }
        )

    monkeypatch.setattr(demand.crm_http, "record_demand_miss", fake_record)
    return enviadas


async def _drenar() -> None:
    """`record_miss` lanza una tarea y vuelve: hay que dejarla correr."""
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_una_busqueda_vacia_se_anota(anotadas):
    demand.set_conversation(42)
    executor._record_demand({"q": "globos metálicos"}, {"data": [], "total": 0})
    await _drenar()

    assert len(anotadas) == 1
    assert anotadas[0]["query"] == "globos metálicos"
    assert anotadas[0]["resultado"] == demand.VACIO
    assert anotadas[0]["conversation_id"] == 42


@pytest.mark.asyncio
async def test_alternativas_no_son_lo_mismo_que_nada(anotadas):
    """Quien se llevó una alternativa pudo comprar; quien no se fue vacío.

    Colapsar las dos en "no encontrado" haría que el panel ordenara por volumen
    y mandara a fabricar lo que ya se estaba resolviendo solo.
    """
    demand.set_conversation(7)
    executor._record_demand(
        {"q": "unicornio gigante"},
        {"data": [{"id_producto": 1}, {"id_producto": 2}], "aproximado": True},
    )
    await _drenar()

    assert anotadas[0]["resultado"] == demand.APROXIMADO
    assert anotadas[0]["n_resultados"] == 2


@pytest.mark.asyncio
async def test_se_guarda_la_consulta_original_no_la_que_funciono(anotadas):
    """El escalón 3 acorta la frase hasta que algo devuelve resultados.

    Si "unicornio de peluche gigante" solo encontró algo al quedarse en
    "peluche", lo que falta en el catálogo es el unicornio gigante. Guardar
    "peluche" —el término que SÍ tiene productos— invertiría la conclusión.
    """
    demand.set_conversation(1)
    executor._record_demand(
        {"q": "unicornio de peluche gigante"},
        {
            "data": [{"id_producto": 9}],
            "aproximado": True,
            "consulta_usada": "peluche",
            "consulta_original": "unicornio de peluche gigante",
        },
    )
    await _drenar()

    assert anotadas[0]["query"] == "unicornio de peluche gigante"


@pytest.mark.asyncio
async def test_una_busqueda_que_encuentra_no_es_demanda_insatisfecha(anotadas):
    executor._record_demand(
        {"q": "rosas rojas"}, {"data": [{"id_producto": 3}], "total": 1}
    )
    await _drenar()

    assert anotadas == []


@pytest.mark.asyncio
async def test_sin_terminos_no_hay_senal(anotadas):
    """Navegar por categoría o pedir destacados no dice qué falta en el catálogo."""
    executor._record_demand({"categoria": "peluches"}, {"data": [], "total": 0})
    await _drenar()

    assert anotadas == []


@pytest.mark.asyncio
async def test_el_termino_pasa_por_el_filtro_de_datos_personales(anotadas):
    """El argumento lo compone el modelo desde la frase del cliente.

    Nada garantiza que no arrastre el teléfono del destinatario, y esta tabla se
    lee entera desde el CRM: es analítica, no una conversación.
    """
    demand.set_conversation(3)
    executor._record_demand(
        {"q": "flores para el 987654321"}, {"data": [], "total": 0}
    )
    await _drenar()

    assert "987654321" not in anotadas[0]["query"]


@pytest.mark.asyncio
async def test_el_termino_se_corta_al_ancho_de_la_columna(anotadas):
    demand.set_conversation(4)
    executor._record_demand({"q": "peluche " * 100}, {"data": [], "total": 0})
    await _drenar()

    assert len(anotadas[0]["query"]) <= demand.MAX_QUERY


@pytest.mark.asyncio
async def test_un_crm_caido_no_rompe_el_turno(monkeypatch, crm_encendido):
    """La respuesta al cliente ya está compuesta: anotarla es lo accesorio."""

    async def explota(*_args, **_kwargs):
        raise RuntimeError("CRM caído")

    monkeypatch.setattr(demand.crm_http, "record_demand_miss", explota)

    demand.set_conversation(5)
    executor._record_demand({"q": "algo"}, {"data": [], "total": 0})
    await _drenar()  # si la excepción escapara, reventaría aquí


@pytest.mark.asyncio
async def test_no_se_espera_al_crm(monkeypatch, crm_encendido):
    """Es telemetría: un CRM lento no puede añadirle un segundo al cliente."""
    empezada = asyncio.Event()

    async def lentisima(*_args, **_kwargs):
        empezada.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(demand.crm_http, "record_demand_miss", lentisima)

    demand.set_conversation(6)
    # Sin await: si `record_miss` esperase, este test tardaría 30 segundos.
    executor._record_demand({"q": "algo"}, {"data": [], "total": 0})
    await asyncio.wait_for(empezada.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_con_el_crm_apagado_no_se_intenta_nada(monkeypatch):
    """El conftest lo deja apagado: la suite no puede salir a la red por esto."""
    llamadas: list[str] = []

    async def registrar(query, **_kwargs):
        llamadas.append(query)

    monkeypatch.setattr(demand.crm_http, "crm_enabled", lambda: False)
    monkeypatch.setattr(demand.crm_http, "record_demand_miss", registrar)

    executor._record_demand({"q": "algo"}, {"data": [], "total": 0})
    await _drenar()

    assert llamadas == []
