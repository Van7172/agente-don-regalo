"""Cuánta red cuesta cada turno: medido, y con un presupuesto que no se puede pasar.

El cierre ganó consultas (cobertura, stock, ficha) y cada una suma latencia a un cliente
que espera por WhatsApp. Estos tests fallan si alguien añade una llamada sin darse
cuenta, y la traza de cada turno lo cuenta en producción.
"""
from datetime import timedelta

import httpx
import pytest

from app.config import settings
from app.harness import master as master_mod
from app.harness.orders import lima_today
from app.harness.state import ConversationState, save_state
from app.harness.trace import Trace
from app.observability import collect_turn_usage, current_turn_api, record_api_call
from app.tools import catalog, mcp_client

_cobertura_real = master_mod._checkout_coverage
_datos_reales = master_mod._load_product_facts
_stock_real = master_mod.is_available


class _Respuesta:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


@pytest.fixture
def api(monkeypatch):
    """HTTP falso en la frontera: todo lo de arriba corre de verdad."""
    llamadas = []

    async def get(self, url, params=None, **_):
        llamadas.append(url)
        if url.endswith("/distritos"):
            return _Respuesta({"data": [{
                "id_distrito": 7, "nombre_distrito": "MIRAFLORES", "tarifa_envio_distrito": 5.0,
            }]})
        if "tipo-cambio" in url:
            return _Respuesta({"data": {"tipo_cambio": 3.4}})
        if "/productos/activos" in url:
            return _Respuesta({"data": {"activos": [1], "ids_activos": [1]}})
        return _Respuesta({"data": {
            "id_producto": 1, "nombre": "Ramo", "precio": 28.0, "precio_final": 28.0,
            "categoria": {"id": 3, "nombre": "Arreglos Florales", "url_categoria": "arreglos-florales"},
        }})

    async def imagenes(_client, productos, limit=None):
        return list(productos)

    async def pedido(*_a, **_k):
        return {"id_pedido_temporal": 1}

    async def anuncia(*_a, **_k):
        return {"producto": "Ramo"}

    async def cede(*_a, **_k):
        from app.harness.contracts import EscalateReason

        return EscalateReason(motivo="x")

    catalog._cache.clear()
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(catalog, "valid_products", imagenes)
    monkeypatch.setattr(master_mod, "_checkout_coverage", _cobertura_real)
    monkeypatch.setattr(master_mod, "_load_product_facts", _datos_reales)
    monkeypatch.setattr(master_mod, "is_available", _stock_real)
    monkeypatch.setattr(master_mod, "create_temporal_order", pedido)
    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "perform_handoff", cede)
    yield llamadas
    catalog._cache.clear()


async def _turno(cid, texto):
    await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999", conversation_id=cid
    )


@pytest.mark.asyncio
async def test_el_cierre_completo_cabe_en_su_presupuesto_de_llamadas(api):
    await save_state(1, ConversationState(
        recent_products=[{"id_producto": 1, "nombre": "Ramo de Rosas"}], shown_product_ids=[1],
    ))
    # (mensaje del cliente, máximo de llamadas a la API en ese turno)
    guion = [
        ("quiero el ramo de rosas", 3),  # stock + ficha + tipo de cambio
        ("Miraflores", 1),               # lista de distritos, luego va en caché
        ((lima_today() + timedelta(days=30)).strftime("%d/%m"), 0),
        ("2", 0),
        ("no", 0),
        ("Ana Ruiz 999888777", 0),
        ("Jr. Ica 200", 0),
        ("Pedro Díaz pedro@gmail.com", 1),  # ficha otra vez: el precio del resumen
        ("sí", 0),
    ]
    for texto, maximo in guion:
        antes = len(api)
        await _turno(1, texto)
        hechas = api[antes:]
        assert len(hechas) <= maximo, f"{texto!r} hizo {len(hechas)} llamadas: {hechas}"


@pytest.mark.asyncio
async def test_la_traza_del_turno_cuenta_sus_llamadas(api, monkeypatch):
    trazas = []
    monkeypatch.setattr(Trace, "emit", lambda self: trazas.append(self))
    await save_state(2, ConversationState(
        recent_products=[{"id_producto": 1, "nombre": "Ramo de Rosas"}], shown_product_ids=[1],
    ))
    await _turno(2, "quiero el ramo de rosas")
    assert trazas[-1].api_calls == len(api)
    assert trazas[-1].api_calls >= 2
    assert trazas[-1].api_ms >= 0


def test_un_turno_sin_red_cuenta_cero():
    with collect_turn_usage():
        assert current_turn_api() == {"calls": 0, "ms": 0}


def test_acumula_llamadas_dentro_del_turno():
    with collect_turn_usage():
        record_api_call("rest", "ok", 120.4)
        record_api_call("mcp", "error", 30.0)
        assert current_turn_api() == {"calls": 2, "ms": 150}


def test_fuera_de_un_turno_no_falla():
    record_api_call("rest", "ok", 1.0)
    assert current_turn_api() is None


def test_la_traza_sin_medicion_no_falla():
    assert Trace().with_api(None).api_calls == 0


@pytest.mark.asyncio
async def test_el_camino_mcp_tambien_se_cuenta(monkeypatch):
    class Cliente:
        async def post(self, *_a, **_k):
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}},
                                  request=httpx.Request("POST", "https://mcp"))

    monkeypatch.setattr(settings, "donregalo_mcp_url", "https://mcp", raising=False)
    with collect_turn_usage():
        await mcp_client._post(Cliente(), {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert current_turn_api()["calls"] == 1
