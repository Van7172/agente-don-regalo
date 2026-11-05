"""Con la API de Don Regalo caída, el cliente no se queda sin respuesta.

Y el fallo que se probó aparte de cada paso no basta: estos recorren el cierre entero
con la avería puesta, porque lo que se rompe es la secuencia. Los stubs de `conftest`
esconden justo esto, así que aquí se restauran las consultas reales.
"""
import json
import time
from datetime import timedelta

import pytest

from app.crm import http_client as crm_http
from app.harness import coverage as cov
from app.harness import master as master_mod
from app.harness import sale as sale_mod
from app.harness import state as state_mod
from app.harness.contracts import AgentResult, EscalateReason
from app.harness.orders import lima_today
from app.harness.state import ConversationState, load_state, save_state
from app.payments import PAYMENT_METHODS_IMAGE_URL
from app.services import agent as agent_mod
from app.tools import catalog

_cobertura_real = master_mod._checkout_coverage
_datos_reales = master_mod._load_product_facts
_FECHA = (lima_today() + timedelta(days=30)).strftime("%d/%m")

DISTRITOS = {"data": [
    {"id_distrito": 140115, "nombre": "Miraflores", "tarifa_usd": 4.71, "tarifa_sol": 16.01, "informacion": ""},
]}
FORMULARIO = [_FECHA, "2", "no", "Ana Ruiz 999888777", "Jr. Ica 200", "Pedro Díaz pedro@gmail.com"]


async def _caida(*_a, **_k):
    raise RuntimeError("API caída")


@pytest.fixture
def mundo(monkeypatch):
    ev = {"enviados": [], "imagenes": [], "ventas": [], "pedidos": 0, "avisos": [], "ficha": True, "cede": True}

    async def distritos(_client, _args):
        return DISTRITOS

    async def ficha(name, args=None):
        if name != "detalle_producto":
            return json.dumps({"data": []})
        if not ev["ficha"]:
            raise RuntimeError("API caída")
        return json.dumps({"data": {
            "id_producto": args["id_producto"], "nombre": "Ramo", "precio_sol": 95.0, "precio_usd": 28.0,
            "categoria": "Arreglos Florales", "categoria_slug": "arreglos-florales",
        }})

    async def texto(_wa, mensaje):
        ev["enviados"].append(mensaje)
        return "wamid"

    async def imagen(_wa, url, caption=""):
        ev["imagenes"].append(url)
        return "wamid"

    async def aviso(mensaje):
        ev["avisos"].append(mensaje)

    async def anuncia(_cid, state):
        ev["ventas"].append(state.chosen_product_name)
        return {"producto": state.chosen_product_name}

    async def pedido(_state, _wa):
        ev["pedidos"] += 1
        return {"id_pedido_temporal": 5}

    async def cede(*_a, **_k):
        return ev["cede"]

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", distritos)
    monkeypatch.setattr(master_mod, "_checkout_coverage", _cobertura_real)
    monkeypatch.setattr(master_mod, "_load_product_facts", _datos_reales)
    monkeypatch.setattr(master_mod, "execute_tool", ficha)
    monkeypatch.setattr(master_mod, "announce_sale", anuncia)
    monkeypatch.setattr(master_mod, "create_temporal_order", pedido)
    monkeypatch.setattr(master_mod, "notify_team", aviso)
    monkeypatch.setattr(master_mod.settings, "pedido_temporal_enabled", True, raising=False)
    monkeypatch.setattr(agent_mod, "send_message", texto)
    monkeypatch.setattr(agent_mod, "send_image", imagen)
    monkeypatch.setattr(agent_mod, "notify_team", aviso)
    monkeypatch.setattr(agent_mod, "_cede_a_humano", cede)
    return ev


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999000111", conversation_id=cid
    )


def _elegido(**kw):
    base = dict(checkout_step="district", chosen_product_id=1, chosen_product_name="Ramo",
                recent_products=[{"id_producto": 1, "nombre": "Ramo"}])
    base.update(kw)
    return ConversationState(**base)


async def _hasta_el_resumen(cid, distrito="Miraflores"):
    await save_state(cid, _elegido())
    respuesta = await _turno(cid, distrito)
    for frase in FORMULARIO:
        respuesta = await _turno(cid, frase)
    return respuesta


# ── Cobertura fuera del cierre ────────────────────────────────────────

@pytest.mark.asyncio
async def test_preguntar_por_el_envio_con_la_api_caida_recibe_respuesta(mundo, monkeypatch):
    monkeypatch.setattr(cov.catalog, "distritos_cobertura", _caida)
    reply = await _turno(1, "¿llegan a Miraflores?")
    assert reply
    assert "S/" not in reply
    assert "Lima" in reply
    assert "tarifa" in reply


@pytest.mark.asyncio
async def test_con_producto_y_api_caida_la_parte_comercial_sigue(mundo, monkeypatch):
    monkeypatch.setattr(cov.catalog, "distritos_cobertura", _caida)

    async def especialista(messages, **_):
        mundo["sistema"] = messages[0]["content"]
        return AgentResult(user_facing="Te muestro desayunos 🎁")

    monkeypatch.setattr(master_mod, "run_specialist", especialista)

    async def taxonomia():
        return [{"nombre": "Desayunos", "slug": "desayunos", "hijos": []}]

    monkeypatch.setattr(master_mod, "_taxonomia", taxonomia)
    reply = await _turno(2, "quiero un desayuno para mañana, ¿llegan a Miraflores?")
    assert "Te muestro desayunos" in reply


# ── Paso de distrito sin cobertura y sin copia ────────────────────────

@pytest.mark.asyncio
async def test_un_distrito_conocido_sigue_aunque_no_se_pueda_validar(mundo, monkeypatch):
    monkeypatch.setattr(cov.catalog, "distritos_cobertura", _caida)
    await save_state(3, _elegido())
    await _turno(3, "Surco")
    state = await load_state(3)
    assert state.district == "Surco"
    assert state.checkout_step == "date"
    assert state.shipping_fee_sol is None


@pytest.mark.asyncio
async def test_un_lugar_desconocido_no_se_da_por_bueno_sin_poder_validarlo(mundo, monkeypatch):
    monkeypatch.setattr(cov.catalog, "distritos_cobertura", _caida)
    await save_state(4, _elegido())
    await _turno(4, "Arequipa")
    state = await load_state(4)
    assert state.district == ""
    assert state.checkout_step == "district"
    assert state.step_retries == 1


@pytest.mark.asyncio
async def test_tras_tres_lugares_sin_validar_cede_el_chat(mundo, monkeypatch):
    monkeypatch.setattr(cov.catalog, "distritos_cobertura", _caida)
    await save_state(5, _elegido())
    salidas = [await _turno(5, t) for t in ("Arequipa", "Cusco", "Trujillo")]
    assert salidas[-1] == master_mod.HANDOFF_DONE


# ── El cierre entero con una avería puesta ────────────────────────────

@pytest.mark.asyncio
async def test_sin_ficha_llega_al_resumen_sin_total_y_no_invita_a_pagar(mundo):
    mundo["ficha"] = False
    resumen = await _hasta_el_resumen(6)
    assert "Resumen" in resumen
    assert "Total: S/" not in resumen

    salida = await _turno(6, "sí")
    assert salida == master_mod.HANDOFF_DONE
    assert mundo["ventas"] == ["Ramo"]
    assert mundo["imagenes"] == [], "no se invita a pagar una cifra que no sabemos"


@pytest.mark.asyncio
async def test_la_ficha_caida_al_elegir_y_de_vuelta_al_resumen_da_el_total(mundo):
    mundo["ficha"] = False
    await save_state(7, _elegido())
    await _turno(7, "Miraflores")
    for frase in FORMULARIO[:-1]:
        await _turno(7, frase)
    mundo["ficha"] = True
    resumen = await _turno(7, FORMULARIO[-1])
    assert "Total: S/111.01" in resumen


@pytest.mark.asyncio
async def test_sin_cobertura_llega_al_resumen_sin_total(mundo, monkeypatch):
    monkeypatch.setattr(cov.catalog, "distritos_cobertura", _caida)
    resumen = await _hasta_el_resumen(8, distrito="Surco")
    assert "Resumen" in resumen
    assert "Surco" in resumen
    assert "Total: S/" not in resumen


@pytest.mark.asyncio
async def test_con_todo_bien_llega_el_total_y_los_medios_de_pago(mundo):
    resumen = await _hasta_el_resumen(9)
    assert "Total: S/111.01" in resumen
    await _turno(9, "sí")
    assert mundo["imagenes"] == [PAYMENT_METHODS_IMAGE_URL]
    assert any("S/111.01" in m for m in mundo["enviados"])


@pytest.mark.asyncio
async def test_si_el_pedido_temporal_falla_la_venta_sigue(mundo, monkeypatch):
    async def sin_pedido(_state, _wa):
        return None

    monkeypatch.setattr(master_mod, "create_temporal_order", sin_pedido)
    await _hasta_el_resumen(10)
    assert await _turno(10, "sí") == master_mod.HANDOFF_DONE
    assert mundo["ventas"] == ["Ramo"]


@pytest.mark.asyncio
async def test_cesion_fallida_y_reintento_un_solo_pedido_y_una_sola_venta(mundo):
    await _hasta_el_resumen(11)
    mundo["cede"] = False
    primera = await _turno(11, "sí")
    assert "confirmas" in primera
    assert mundo["ventas"] == []
    mundo["cede"] = True
    assert await _turno(11, "sí") == master_mod.HANDOFF_DONE
    assert mundo["ventas"] == ["Ramo"]
    assert mundo["pedidos"] == 1


# ── Un solo aviso al equipo por venta ─────────────────────────────────

@pytest.mark.asyncio
async def test_una_venta_no_avisa_dos_veces(mundo):
    await _hasta_el_resumen(12)
    await _turno(12, "sí")
    assert mundo["avisos"] == [], "el aviso de la venta lo da `announce_sale`, aquí simulado"


@pytest.mark.asyncio
async def test_si_la_venta_no_se_pudo_anunciar_el_equipo_se_entera_igual(mundo, monkeypatch):
    async def incompleta(_cid, _state):
        return None

    monkeypatch.setattr(master_mod, "announce_sale", incompleta)
    await _hasta_el_resumen(13)
    await _turno(13, "sí")
    assert len(mundo["avisos"]) == 1
    assert "13" in mundo["avisos"][0]


@pytest.mark.asyncio
async def test_si_falla_guardar_la_venta_el_aviso_sale_igual(monkeypatch):
    avisos = []

    async def rota(*_a, **_k):
        raise RuntimeError("CRM caído")

    async def aviso(mensaje):
        avisos.append(mensaje)

    monkeypatch.setattr(sale_mod.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(sale_mod.crm_http, "put_setting", rota)
    monkeypatch.setattr("app.services.messenger.notify_team", aviso)
    state = _elegido(district="Miraflores", date="2026-12-01")
    venta = await sale_mod.announce(1, state)
    assert venta is not None
    assert len(avisos) == 1


# ── Copia de seguridad de lo que casi no cambia ───────────────────────

@pytest.fixture
def cache_limpia(monkeypatch):
    catalog._cache.clear()
    yield
    catalog._cache.clear()


@pytest.mark.asyncio
async def test_con_la_api_caida_se_usa_la_ultima_copia_buena(cache_limpia, monkeypatch):
    llamadas = {"n": 0}

    async def responde(_client, _url, _params=None):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return {"data": ["bueno"]}
        raise RuntimeError("API caída")

    monkeypatch.setattr(catalog, "get", responde)
    monkeypatch.setattr(catalog.settings, "cache_ttl_seconds", 0, raising=False)
    assert await catalog._cached_get(None, "https://x/distritos") == {"data": ["bueno"]}
    assert await catalog._cached_get(None, "https://x/distritos") == {"data": ["bueno"]}
    assert llamadas["n"] == 2, "se intentó refrescar"


@pytest.mark.asyncio
async def test_una_copia_demasiado_vieja_no_se_usa(cache_limpia, monkeypatch):
    async def caida(_client, _url, _params=None):
        raise RuntimeError("API caída")

    monkeypatch.setattr(catalog, "get", caida)
    viejo = time.monotonic() - catalog._STALE_MAX_SECONDS - 10
    catalog._cache["https://x/distritos"] = (viejo, {"data": ["viejo"]})
    with pytest.raises(RuntimeError):
        await catalog._cached_get(None, "https://x/distritos")


@pytest.mark.asyncio
async def test_sin_copia_el_error_se_propaga(cache_limpia, monkeypatch):
    async def caida(_client, _url, _params=None):
        raise RuntimeError("API caída")

    monkeypatch.setattr(catalog, "get", caida)
    with pytest.raises(RuntimeError):
        await catalog._cached_get(None, "https://x/distritos")


# ── Conflicto real al guardar, por el camino del CRM ──────────────────

@pytest.fixture
def crm_con_versiones(monkeypatch):
    datos = {"doc": {}, "conflictos": 0}

    async def get_setting(key):
        return datos["doc"].get(key)

    async def put_setting_cas(key, payload, expected):
        actual = json.loads(datos["doc"][key])["version"] if key in datos["doc"] else 0
        if int(actual) != int(expected):
            datos["conflictos"] += 1
            return False
        datos["doc"][key] = payload
        return True

    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(state_mod.crm_http, "get_setting", get_setting)
    monkeypatch.setattr(state_mod.crm_http, "put_setting_cas", put_setting_cas)
    return datos


@pytest.mark.asyncio
async def test_la_cesion_a_mitad_de_turno_no_se_pierde_con_el_olvido_diario(crm_con_versiones, monkeypatch):
    """Dos escritores de verdad: el turno y `perform_handoff`, contra versiones reales."""
    hace_dias = time.time() - 3 * 86400
    await save_state(20, ConversationState(
        shown_product_ids=[1, 2], last_turn_at=hace_dias, intent_last="catalog_search",
    ))

    async def cede(*_a, **_k):
        return True

    async def nada(*_a, **_k):
        return "wamid"

    monkeypatch.setattr(agent_mod, "_cede_a_humano", cede)
    monkeypatch.setattr(agent_mod, "send_message", nada)
    monkeypatch.setattr(agent_mod, "notify_team", nada)

    salida = await _turno(20, "quiero hablar con un asesor")
    state = await load_state(20)
    assert salida == master_mod.HANDOFF_DONE
    assert crm_con_versiones["conflictos"] >= 1, "la carrera ocurrió de verdad"
    assert state.handoff_at, "lo que escribió la cesión sobrevive"
    assert state.shown_product_ids == [], "y lo que olvidó el turno, también"
