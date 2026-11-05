"""El presupuesto del cliente es un límite, no una anotación.

"Tengo como 100 soles" no llegaba a ninguna parte: la vía indicada para una
categoría no admite precio, y `precio_max` iba en USD para un modelo al que se le
prohíbe convertir monedas. El bot enseñaba desayunos de S/180 a quien dijo 100.
"""
import json

import pytest

from app.harness import master as master_mod
from app.harness.budget import extract_budget, fit_budget
from app.harness.contracts import AgentResult, Product
from app.harness.state import ConversationState, load_state, save_state
from app.prompts.compose import render_state
from app.tools import adapters, attributes, catalog, executor, search


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("tengo como 100 soles", (100.0, "PEN")),
        ("mi presupuesto es de 150", (150.0, "PEN")),
        ("presupuesto: S/ 80", (80.0, "PEN")),
        ("algo de S/.120", (120.0, "PEN")),
        ("hasta 200", (200.0, "PEN")),
        ("máximo 90 soles", (90.0, "PEN")),
        ("no más de 70", (70.0, "PEN")),
        ("tengo 50 lucas", (50.0, "PEN")),
        ("unos $30", (30.0, "USD")),
        ("hasta 40 dólares", (40.0, "USD")),
    ],
)
def test_reconoce_el_presupuesto(texto, esperado):
    assert extract_budget(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    ["hasta las 10", "2 desayunos", "para el 15 de noviembre", "Av. Lima 100",
     "hasta 15 personas", "quiero el 2", "somos 20", "999888777"],
)
def test_otros_numeros_no_son_un_presupuesto(texto):
    assert extract_budget(texto) is None


def _p(pid, sol):
    return Product(id_producto=pid, nombre=f"P{pid}", precio_sol=sol, precio_usd=round(sol / 3.4, 2))


def test_se_queda_con_lo_que_cabe():
    productos = [_p(1, 90), _p(2, 180), _p(3, 100)]
    assert fit_budget(productos, 100.0, "PEN") == ([productos[0], productos[2]], False)


def test_el_envio_cuenta_contra_el_presupuesto():
    productos = [_p(1, 90), _p(2, 80)]
    assert fit_budget(productos, 100.0, "PEN", shipping=15.0) == ([productos[1]], False)


def test_si_nada_cabe_devuelve_lo_mas_cercano_y_lo_avisa():
    productos = [_p(1, 300), _p(2, 150), _p(3, 200), _p(4, 180)]
    elegidos, excedido = fit_budget(productos, 100.0, "PEN")
    assert excedido is True
    assert [p.id_producto for p in elegidos] == [2, 4, 3]


def test_en_dolares_se_compara_en_dolares():
    productos = [_p(1, 102), _p(2, 170)]  # 30.0 y 50.0 USD
    assert fit_budget(productos, 40.0, "USD") == ([productos[0]], False)


def test_sin_presupuesto_no_se_toca_nada():
    productos = [_p(1, 300)]
    assert fit_budget(productos, None, None) == (productos, False)


@pytest.fixture
def catalogo(monkeypatch):
    productos = {"lista": [_p(1, 90), _p(2, 180)]}

    async def especialista(messages, **_):
        return AgentResult(user_facing="¡Genial! Te muestro opciones 🎁", artifacts=list(productos["lista"]))

    monkeypatch.setattr(master_mod, "run_specialist", especialista)
    return productos


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999", conversation_id=cid
    )


@pytest.mark.asyncio
async def test_solo_se_ensena_lo_que_cabe(catalogo):
    reply = await _turno(50, "quiero un desayuno, tengo como 100 soles")
    assert "P1" in reply
    assert "P2" not in reply


@pytest.mark.asyncio
async def test_el_presupuesto_se_recuerda_entre_turnos(catalogo):
    await _turno(51, "tengo como 100 soles, quiero un regalo")
    catalogo["lista"] = [_p(3, 95), _p(4, 300)]
    reply = await _turno(51, "quiero un desayuno")
    state = await load_state(51)
    assert (state.budget_amount, state.budget_currency) == (100.0, "PEN")
    assert "P3" in reply
    assert "P4" not in reply


@pytest.mark.asyncio
async def test_un_presupuesto_nuevo_reemplaza_al_anterior(catalogo):
    await _turno(52, "hasta 100 soles, quiero un regalo")
    reply = await _turno(52, "mejor hasta 200 soles, quiero un regalo")
    assert "P2" in reply


@pytest.mark.asyncio
async def test_si_nada_cabe_lo_dice_con_honestidad_y_sin_tumbarse(catalogo):
    catalogo["lista"] = [_p(2, 180)]
    reply = await _turno(53, "quiero un regalo, tengo 100 soles")
    assert "P2" in reply
    assert "S/100.00" in reply
    assert "más cercan" in reply
    assert "dato equivocado" not in reply, "la barrera de precios no debe tumbar el aviso"


@pytest.mark.asyncio
async def test_el_formulario_no_toma_un_numero_por_presupuesto(catalogo):
    await save_state(54, ConversationState(checkout_step="address", chosen_product_id=1, chosen_product_name="X"))
    await _turno(54, "hasta 100 soles queda mi casa, Jr Ica 200")
    assert (await load_state(54)).budget_amount is None


def test_el_modelo_ve_el_presupuesto_en_el_estado():
    bloque = render_state(ConversationState(budget_amount=100.0, budget_currency="PEN"))
    assert "S/100.00" in bloque


@pytest.mark.asyncio
async def test_la_busqueda_semantica_recibe_el_tope_en_dolares(monkeypatch):
    visto = {}

    async def semantica(_client, args):
        visto.update(args)
        return {"data": []}

    async def tasa(_client):
        return 4.0

    monkeypatch.setattr(search, "buscar_semantico", semantica)
    monkeypatch.setattr(adapters, "usd_pen_rate", tasa)
    token = attributes.set_turn_budget((100.0, "PEN"))
    try:
        await executor.execute_tool("buscar_semantico", {"q": "algo romántico"})
    finally:
        attributes.reset_turn_budget(token)
    assert visto["precio_max"] == 25.0


@pytest.mark.asyncio
async def test_con_presupuesto_la_categoria_trae_primero_lo_barato(monkeypatch):
    visto = {}

    async def productos(_client, _url, params, **_):
        visto.update(params)
        return {"data": [{"id_producto": 1}]}

    monkeypatch.setattr(catalog, "_productos", productos)
    monkeypatch.setattr(executor.settings, "donregalo_use_mcp", False, raising=False)
    token = attributes.set_turn_budget((100.0, "PEN"))
    try:
        await executor.execute_tool("catalogo_categoria", {"slug": "desayunos"})
    finally:
        attributes.reset_turn_budget(token)
    assert visto["orden"] == "asc"
