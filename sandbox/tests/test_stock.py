"""Disponibilidad: no cerramos el pedido de un producto que ya no existe.

`/productos/activos` es la única fuente que sabe si un producto sigue vivo. Importa
porque las dos fuentes del catálogo van desfasadas: Qdrant se sincroniza cada cierto
tiempo, y el estado de la conversación guarda ids que el cliente vio hace horas.

Sin esta comprobación, el bot puede cerrar un pedido entero —distrito, fecha,
horario— de un producto dado de baja, y el asesor entra al chat verde a cobrar algo
que no existe.
"""
import pytest

from app.harness import master as master_mod
from app.harness import state as state_mod
from app.harness import stock as stock_mod
from app.harness.state import ConversationState
from app.tools import catalog


class _FakeClient:
    """Devuelve lo que la API real devuelve: la lista de ids que siguen activos."""

    def __init__(self, activos, boom=False):
        self.activos = activos
        self.boom = boom
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append(params)
        if self.boom:
            raise RuntimeError("API caída")

        class R:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"success": True, "data": self_activos}

        self_activos = self.activos
        return R()


@pytest.mark.asyncio
async def test_productos_activos_devuelve_los_vivos(monkeypatch):
    async def fake_get(client, url, params=None):
        assert params["ids"] == "290,1081,999999"
        return {"success": True, "data": [290, 1081]}  # el inventado se cae

    monkeypatch.setattr(catalog, "get", fake_get)

    activos = await catalog.productos_activos(None, [290, 1081, 999999])
    assert activos == {290, 1081}


@pytest.mark.asyncio
async def test_si_la_api_falla_devolvemos_none_no_vacio(monkeypatch):
    """`None` = "no se pudo verificar". Un set vacío diría "nada está activo",
    y eso bloquearía TODAS las ventas ante un timeout."""
    async def fake_get(client, url, params=None):
        raise RuntimeError("504")

    monkeypatch.setattr(catalog, "get", fake_get)

    assert await catalog.productos_activos(None, [290]) is None


@pytest.mark.asyncio
async def test_is_available(monkeypatch):
    async def activos(client, ids):
        return {11}

    monkeypatch.setattr(stock_mod, "productos_activos", activos)

    assert await stock_mod.is_available(11) is True
    assert await stock_mod.is_available(22) is False


@pytest.mark.asyncio
async def test_si_no_se_puede_verificar_la_venta_sigue(monkeypatch):
    """Bloquear un pedido sano por un timeout es peor que el riesgo que evitamos."""
    async def sin_respuesta(client, ids):
        return None

    monkeypatch.setattr(stock_mod, "productos_activos", sin_respuesta)

    assert await stock_mod.is_available(11) is None


# ── El cierre, de punta a punta ───────────────────────────────────────

def _estado_con_producto():
    return ConversationState(
        recent_products=[{"id_producto": 11, "nombre": "Terrario Familia Panditas"}],
        shown_product_ids=[11],
    )


@pytest.fixture
def sin_crm(monkeypatch):
    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: False)
    state_mod.clear_local_cache()
    yield
    state_mod.clear_local_cache()


@pytest.mark.asyncio
async def test_no_se_abre_el_cierre_de_un_producto_dado_de_baja(sin_crm, monkeypatch):
    async def no_disponible(product_id):
        return False

    monkeypatch.setattr(master_mod, "is_available", no_disponible)
    await state_mod.save_state(1, _estado_con_producto())

    reply = await master_mod.run_master(
        [{"role": "user", "content": "lo quiero"}], wa_id="51999", conversation_id=1
    )

    assert "ya no está disponible" in reply
    estado = await state_mod.load_state(1)
    assert estado.checkout_step == "idle", "no se arranca un pedido fantasma"
    assert estado.chosen_product_id is None
    # Y desaparece de la memoria del chat: otro "ese" no puede resolver al muerto.
    assert estado.recent_products == []
    assert estado.shown_product_ids == []


@pytest.mark.asyncio
async def test_si_sigue_disponible_el_cierre_arranca_normal(sin_crm, monkeypatch):
    async def disponible(product_id):
        return True

    monkeypatch.setattr(master_mod, "is_available", disponible)
    await state_mod.save_state(1, _estado_con_producto())

    reply = await master_mod.run_master(
        [{"role": "user", "content": "lo quiero"}], wa_id="51999", conversation_id=1
    )

    estado = await state_mod.load_state(1)
    assert estado.checkout_step == "district"
    assert estado.chosen_product_id == 11
    assert "distrito" in reply.casefold()


@pytest.mark.asyncio
async def test_un_fallo_de_la_api_no_bloquea_la_venta(sin_crm, monkeypatch):
    async def no_verificable(product_id):
        return None

    monkeypatch.setattr(master_mod, "is_available", no_verificable)
    await state_mod.save_state(1, _estado_con_producto())

    await master_mod.run_master(
        [{"role": "user", "content": "lo quiero"}], wa_id="51999", conversation_id=1
    )

    estado = await state_mod.load_state(1)
    assert estado.checkout_step == "district", "ante la duda, la venta sigue"
    assert estado.chosen_product_id == 11
