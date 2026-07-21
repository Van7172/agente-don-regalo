"""La suite no sale a la red.

El `.env` de desarrollo trae `CRM_MODE=external`, así que sin esto `load_state`
hacía una llamada HTTP real al CRM en mitad de cada test. Efectos: la suite tardaba
segundos de más, dependía de que el hosting estuviera vivo, y el "filler" de 0.7s
del agente se disparaba por la latencia y ensuciaba las aserciones — dos tests
fallaban o pasaban según el orden en que corrieran.

Un test que depende de la red no es un test: es una comprobación de que internet
funciona. Cada fixture de aquí puede sobreescribirse desde el propio test cuando lo
que se quiere probar ES la integración.
"""
import pytest

from app.config import settings as app_settings
from app.crm import http_client as crm_http
from app.harness import master as master_mod
from app.harness import state as state_mod
from app.services import buffer as buffer_mod


@pytest.fixture(autouse=True)
def sin_wamids_vistos():
    """El guardia anti-redelivery no puede filtrarse entre tests.

    `_seen_wa_message_ids` es global del proceso (como `_buffers`). Varios tests
    reusan el mismo `wamid` de prueba, así que sin limpiarlo el segundo test que
    corriera vería su mensaje descartado como duplicado — y fallaría según el
    orden de la suite, que es la peor clase de fallo.
    """
    buffer_mod.reset_seen_wa_message_ids()
    yield
    buffer_mod.reset_seen_wa_message_ids()


@pytest.fixture(autouse=True)
def sin_pedido_temporal(monkeypatch):
    """El cierre no llama a `POST /pedidos/temporales` en los tests.

    Un test de cierre no debería crear pedidos en el panel real. Los tests que sí
    prueban esa integración lo activan y mockean el HTTP a mano.
    """
    monkeypatch.setattr(app_settings, "pedido_temporal_enabled", False, raising=False)


@pytest.fixture(autouse=True)
def sin_crm_externo(monkeypatch):
    """El estado vive en memoria, no en el CRM del hosting."""
    monkeypatch.setattr(crm_http, "crm_enabled", lambda: False)
    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: False)
    state_mod.clear_local_cache()
    yield
    state_mod.clear_local_cache()


@pytest.fixture(autouse=True)
def sin_verificar_stock(monkeypatch):
    """`/productos/activos` no se consulta salvo que el test lo pida.

    `None` = "no se pudo verificar", que es el camino que NO bloquea la venta. Es el
    default correcto: un test de cierre no debería depender de que un id de prueba
    exista en el catálogo real.
    """
    async def no_verificable(_product_id):
        return None

    monkeypatch.setattr(master_mod, "is_available", no_verificable)
