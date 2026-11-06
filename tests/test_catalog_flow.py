"""30 escenarios de aceptación y contratos del despliegue gradual de catálogo."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException

from app.config import settings
from app.harness import catalog_flow, catalog_rollout, master
from app.harness.contracts import AgentResult, Turn
from app.harness.state import ConversationState
from app.tools import attributes
from app.tools.executor import enforce_category
from app.tools.relevance import parse_request, select_products

LOVE = {"id_producto": 235, "nombre": "DESAYUNO CRIOLLO LOVE", "categoria_slug": "desayunos-criollos", "precio_sol": 149.6, "precio_usd": 44, "stock": 100,
        "descripcion_corta": "Desayuno de cumpleaños, amor y aniversario."}
CRIOLLO = {"id_producto": 515, "nombre": "Gustito Criollo", "categoria_slug": "desayunos-criollos", "precio_sol": 79.9, "precio_usd": 23.5, "stock": 100}
CATALOG = [LOVE, CRIOLLO,
    {"id_producto": 349, "nombre": "Desayuno de cumpleaños para Hombre", "categoria_slug": "desayunos", "precio_sol": 163.2, "precio_usd": 48},
    {"id_producto": 9001, "nombre": "Ramo de rosas blancas", "categoria_slug": "arreglos-florales", "precio_sol": 100},
    {"id_producto": 9002, "nombre": "Ramo de rosas rojas", "categoria_slug": "arreglos-florales", "precio_sol": 100},
    {"id_producto": 9003, "nombre": "Ramo de girasoles amarillos", "categoria_slug": "arreglos-florales", "precio_sol": 100},
    {"id_producto": 1279, "nombre": "Peluche Kitty Sunshine", "categoria_slug": "peluches", "precio_sol": 95.2},
    {"id_producto": 290, "nombre": "Peluche Oso Loquito de Amor", "categoria_slug": "peluches", "precio_sol": 64.6},
]


@dataclass
class Scenario:
    name: str
    text: str
    expected: tuple[int, ...] = ()
    state: dict = field(default_factory=dict)
    eligible: bool = True
    media: bool = False
    quoted: str = ""
    mode: str = "normal"


SCENARIOS = [
    Scenario("criollo-precio", "Desayuno criollo que precio tiene", (235, 515)),
    Scenario("nombre-completo", "Cuánto cuesta DESAYUNO CRIOLLO LOVE", (235,)),
    Scenario("minusculas", "desayuno criollo love", (235,)),
    Scenario("mayusculas", "DESAYUNO CRIOLLO LOVE", (235,)),
    Scenario("cortesia", "Hola quiero un desayuno criollo love por favor", (235,)),
    Scenario("cumple-mujer", "desayuno criollo para cumpleaños para mujer", (235, 515)),
    Scenario("para-hombre", "desayuno criollo para hombre", (235, 515)),
    Scenario("mas-opciones", "más opciones", (515,), {"catalog_query": "desayuno criollo", "shown_product_ids": [235]}),
    Scenario("correccion-repite", "no, yo quería desayuno criollo love", (235,), {"shown_product_ids": [235]}),
    Scenario("afinar-ocasion", "para cumpleaños para mujer", (235, 515), {"catalog_query": "desayuno criollo"}),
    Scenario("corregir-destinatario", "para mujer", (235, 515), {"catalog_query": "desayuno criollo para hombre"}),
    Scenario("presupuesto-pen", "desayuno criollo", (515,), {"budget_amount": 100, "budget_currency": "PEN"}),
    Scenario("presupuesto-usd", "desayuno criollo", (515,), {"budget_amount": 30, "budget_currency": "USD"}),
    Scenario("presupuesto-con-envio", "desayuno criollo", (515, 235), {"budget_amount": 100, "budget_currency": "PEN", "shipping_fee_sol": 30}),
    Scenario("blancas", "rosas blancas", (9001,)),
    Scenario("rojas", "rosas rojas", (9002,)),
    Scenario("amarillas", "flores amarillas", (9003,)),
    Scenario("kitty", "peluche Kitty Sunshine", (1279,)),
    Scenario("loquito", "peluche loquito de amor", (290,)),
    Scenario("inexistente-alternativas", "peluche dinosaurio para mi esposa", (1279, 290)),
    Scenario("categoria-sola-legacy", "quiero desayunos", eligible=False),
    Scenario("ambiguo-legacy", "quiero un regalo", eligible=False),
    Scenario("url-legacy", "https://donregalo.pe/p/desayuno-criollo-love", eligible=False),
    Scenario("imagen-legacy", "desayuno criollo", eligible=False, media=True),
    Scenario("respuesta-citada-legacy", "desayuno criollo", eligible=False, quoted="Ese producto"),
    Scenario("personalizacion-legacy", "desayuno criollo sin tamal", eligible=False),
    Scenario("cierre-legacy", "desayuno criollo", state={"checkout_step": "district", "chosen_product_id": 235}, eligible=False),
    Scenario("cantidades-legacy", "quiero 2 desayunos criollos", eligible=False),
    Scenario("stock-agotado", "desayuno criollo love", (), mode="out_of_stock"),
    Scenario("api-caida-fallback", "desayuno criollo love", (), mode="api_error"),
]


async def fixture_execute(name, args):
    if name == "detalle_producto":
        return json.dumps({"data": next(p for p in CATALOG if p["id_producto"] == args["id_producto"])})
    assert name == "buscar_productos"
    request = parse_request(attributes.turn_text())
    items = enforce_category({"data": copy.deepcopy(CATALOG)}, args.get("categoria", ""))["data"]
    items = attributes.enforce_attributes({"data": items}, attributes.extract_color_attributes(request.text))["data"]
    items, near = select_products(items, request)
    return json.dumps({"data": items, "aproximado": near})


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SCENARIOS, ids=lambda c: c.name)
async def test_aceptacion(case):
    state = ConversationState.from_dict(case.state)
    before = copy.deepcopy(state.to_dict())
    turn = Turn(text=case.text, has_media=case.media, quoted=case.quoted)
    assert catalog_flow.eligible(turn, state) is case.eligible
    if not case.eligible:
        return

    async def execute(name, args):
        if case.mode == "api_error":
            return json.dumps({"error": "HTTP 503"})
        if case.mode == "out_of_stock" and name == "detalle_producto":
            return json.dumps({"data": {**LOVE, "stock": 0}})
        return await fixture_execute(name, args)

    result = await catalog_flow.run(turn, state, execute)
    assert state.to_dict() == before
    if case.mode == "api_error":
        assert result is None
        return
    assert result.product_ids == list(case.expected)
    assert result.catalog_strategy == "deterministic"
    assert result.user_facing
    if "presupuesto" in case.name and case.name != "presupuesto-con-envio":
        assert all(p.precio_sol <= 100 for p in result.artifacts)
    if case.name == "presupuesto-con-envio":
        assert "no encontré" in result.user_facing
    if case.name == "inexistente-alternativas":
        assert result.catalog_approximate and "alternativas" in result.user_facing


def test_muestra_estable_y_apagado_inmediato(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "catalog_flow_enabled", True)
    monkeypatch.setattr(settings, "catalog_flow_percent", 10)
    monkeypatch.setenv("CATALOG_FLOW_DISABLE_FILE", str(tmp_path / "disabled"))
    ids = [i for i in range(1, 5001) if catalog_rollout.selected(i)]
    assert 400 < len(ids) < 600
    assert ids == [i for i in range(1, 5001) if catalog_rollout.selected(i)]
    assert catalog_rollout.selected(None) is False
    catalog_rollout.disable()
    assert not catalog_rollout.enabled()
    assert not any(catalog_rollout.selected(i) for i in ids)
    assert catalog_rollout.status()["disabled_runtime"] is True


@pytest.mark.asyncio
async def test_fallo_reutiliza_el_especialista_y_no_repite_envios(monkeypatch):
    async def execute(*_a):
        return json.dumps({"error": "HTTP 503"})

    async def specialist(*_a, **_k):
        return AgentResult(user_facing="Sigo ayudándote con tu pedido")

    async def no_menu(*_a):
        return None

    monkeypatch.setattr(catalog_rollout, "selected", lambda _id: True)
    monkeypatch.setattr(master, "execute_tool", execute)
    monkeypatch.setattr(master, "_run_specialty", specialist)
    monkeypatch.setattr(master, "_answer_menu", no_menu)
    result = await master._handle("catalog_search", Turn(text="desayuno criollo"), ConversationState(), conversation_id=12, wa_id="test")
    assert result.catalog_strategy == "legacy_fallback"
    assert result.user_facing == "Sigo ayudándote con tu pedido"


@pytest.mark.asyncio
async def test_master_cohorte_devuelve_ficha_sin_llamar_modelo(monkeypatch):
    monkeypatch.setattr(catalog_rollout, "selected", lambda _id: True)
    monkeypatch.setattr(master, "execute_tool", fixture_execute)

    async def no_llm(*_a, **_k):
        pytest.fail("Un pedido concreto seleccionado no necesita el especialista")

    monkeypatch.setattr(master, "_run_specialty", no_llm)
    result = await master._handle("catalog_search", Turn(text="desayuno criollo love"), ConversationState(), conversation_id=12, wa_id="test")
    assert result.product_ids == [235]
    assert result.catalog_strategy == "deterministic"


@pytest.mark.asyncio
async def test_cohorte_control_conserva_especialista(monkeypatch):
    monkeypatch.setattr(catalog_rollout, "selected", lambda _id: False)

    async def specialist(*_a, **_k):
        return AgentResult(user_facing="Respuesta habitual")

    async def no_menu(*_a):
        return None

    monkeypatch.setattr(master, "_run_specialty", specialist)
    monkeypatch.setattr(master, "_answer_menu", no_menu)
    result = await master._handle("catalog_search", Turn(text="desayuno criollo"), ConversationState(), wa_id="test")
    assert result.catalog_strategy == "legacy"


@pytest.mark.asyncio
async def test_cancelacion_se_propaga():
    async def execute(*_a):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await catalog_flow.run(Turn(text="desayuno criollo"), ConversationState(), execute)


@pytest.mark.asyncio
async def test_texto_de_catalogo_con_contactos_no_llega_a_la_respuesta():
    contaminated = {**LOVE, "descripcion_corta": "Contacto 911111111, ana@example.com"}

    async def execute(name, _args):
        return json.dumps({"data": contaminated if name == "detalle_producto" else [contaminated]})

    result = await catalog_flow.run(Turn(text="desayuno criollo love"), ConversationState(), execute)
    assert result.product_ids == [235]
    assert "911111111" not in result.user_facing
    assert "ana@example.com" not in result.user_facing
    assert "protegido" not in result.user_facing


def test_archivo_de_imagen_no_es_telefono_y_caption_si_se_valida():
    from app.harness.contracts import Product
    from app.guardrails.response import check_reply
    product = Product(id_producto=910, nombre="Osito", imagen_url="https://donregalo.pe/img/911111111.jpg")
    assert not check_reply(product.imagen_url, artifacts=[product])
    violations = check_reply(product.imagen_url + "\nContacta al 911111111", artifacts=[product])
    assert any(v.rule == "no_third_party_contact" for v in violations)
    assert any(v.rule == "no_third_party_contact" for v in check_reply("https://example.org/911111111.jpg"))


@pytest.mark.asyncio
async def test_interno_apagado_exige_token_fuerte(monkeypatch, tmp_path):
    from app.api_internal import catalog_flow_disable
    monkeypatch.setenv("CATALOG_FLOW_DISABLE_FILE", str(tmp_path / "disabled"))
    monkeypatch.setattr(settings, "agent_internal_token", "dev-agent-token-change-me")
    with pytest.raises(HTTPException) as error:
        await catalog_flow_disable(x_agent_token="dev-agent-token-change-me")
    assert error.value.status_code == 503
    monkeypatch.setattr(settings, "agent_internal_token", "test-internal-key")
    with pytest.raises(HTTPException) as error:
        await catalog_flow_disable(x_agent_token="wrong")
    assert error.value.status_code == 401
    await catalog_flow_disable(x_agent_token="test-internal-key")
    assert catalog_rollout.disable_path().exists()
