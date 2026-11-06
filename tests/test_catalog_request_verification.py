"""Regresiones de intención comercial, etiquetas incompletas y capturas de producto."""
from __future__ import annotations

import json

import pytest

from app.harness import master, state as state_mod, visual_product
from app.harness.contracts import AgentResult, Product, Turn
from app.harness.state import ConversationState
from app.services import agent
from app.tools import attributes, catalog, executor, search
from app.tools.relevance import continue_query, parse_request, select_products

LOVE = {"id_producto": 235, "nombre": "DESAYUNO CRIOLLO LOVE", "categoria_slug": "desayunos-criollos", "precio_sol": 149.6, "precio_usd": 44,
        "descripcion_corta": "Desayuno de amor, desayuno de cumpleaños, desayuno de aniversario.",
        "descripcion": "Tamal con salsa criolla. Pan con chicharrón. Taza de cerámica diseño Love.", "ocasiones": []}
GUSTITO = {"id_producto": 734, "nombre": "Gustito Consintiéndote con Amor", "categoria_slug": "desayunos-de-amor", "precio_sol": 74.8,
           "descripcion": "Croissant de pollo. Bebida Bio. Alfajor de 3 pisos. Parfait de frutas."}
CRIOLLO = {"id_producto": 515, "nombre": "Gustito Criollo", "categoria_slug": "desayunos-criollos", "precio_sol": 79.9,
           "descripcion": "Pan con chicharrón. Bebida Bio."}
HOMBRE = {"id_producto": 349, "nombre": "Desayuno de cumpleaños para Hombre", "categoria_slug": "desayunos"}


@pytest.mark.parametrize("text,category,terms", [
    ("Desayuno criollo que precio tiene", "desayunos", ("criollo",)),
    ("Yo quiero desayuno criollo para cumpleaños para mujer", "desayunos", ("criollo",)),
    ("Quiero un peluche dinosaurio para mi esposa", "peluches", ("dinosaurio",)),
    ("Cuánto cuesta DESAYUNO CRIOLLO LOVE", "desayunos", ("criollo", "love")),
    ("desayuno criollo para mañana", "desayunos", ("criollo",)),
])
def test_extrae_el_nucleo_sin_ocasion_destinatario_ni_logistica(text, category, terms):
    request = parse_request(text)
    assert request.category == category
    assert request.terms == terms


def test_afinamiento_conserva_el_producto_y_correccion_reemplaza_el_modelo():
    previous = "desayuno criollo"
    assert parse_request(continue_query("para cumpleaños para mujer", previous)).terms == ("criollo",)
    assert parse_request(continue_query("otras opciones", previous)).terms == ("criollo",)
    corrected = parse_request(continue_query("no, quería dinosaurio", "peluche unicornio"))
    assert corrected.category == "peluches"
    assert corrected.terms == ("dinosaurio",)
    assert parse_request(continue_query("para mujer", "desayuno criollo para hombre")).recipient == "mujer"


def test_destinatario_descarta_hombre_y_admite_neutros():
    chosen, approximate = select_products([HOMBRE, LOVE, CRIOLLO], parse_request("desayuno criollo para mujer"))
    assert {p["id_producto"] for p in chosen} == {235, 515}
    assert approximate is False
    generic, _ = select_products([HOMBRE, GUSTITO], parse_request("desayuno para mujer"))
    assert [p["id_producto"] for p in generic] == [734]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("catalogo_categoria", {"slug": "desayunos"}),
    ("buscar_semantico", {"q": "desayuno de cumpleaños", "categoria_slug": "desayunos", "id_ocasion": 1}),
    ("buscar_productos", {"q": "desayuno criollo para cumpleaños para mujer", "id_ocasion": 1}),
])
async def test_el_executor_conserva_criollo_aunque_el_modelo_busque_generico(monkeypatch, tool, args):
    calls = []

    async def literal(_client, params):
        calls.append(params)
        return {"data": [CRIOLLO, LOVE]}

    async def no_semantic(*_a, **_k):
        pytest.fail("La primera consulta ya verificó el núcleo del pedido")

    monkeypatch.setattr(catalog, "buscar_productos", literal)
    monkeypatch.setattr(search, "buscar_semantico", no_semantic)
    token = attributes.set_turn_text("Yo quiero desayuno criollo para cumpleaños para mujer")
    try:
        result = json.loads(await executor.execute_tool(tool, args))
    finally:
        attributes.reset_turn_text(token)
    assert calls == [{"q": "desayuno criollo", "categoria": "desayunos"}]
    assert {p["id_producto"] for p in result["data"]} == {235, 515}
    assert result["aproximado"] is False


@pytest.mark.asyncio
async def test_no_pierde_producto_fuera_de_los_seis_primeros(monkeypatch):
    async def get(_client, _url, params):
        assert params["per_page"] >= 30
        return {"data": [GUSTITO.copy() | {"id_producto": i} for i in range(10)] + [LOVE]}

    async def rate(_client):
        return 3.4

    async def valid(_client, products, limit):
        return products[:limit]

    monkeypatch.setattr(catalog, "get", get)
    monkeypatch.setattr(catalog.adapters, "usd_pen_rate", rate)
    monkeypatch.setattr(catalog, "valid_products", valid)
    token = attributes.set_turn_text("desayuno criollo")
    try:
        result = await catalog.catalogo_categoria(None, {"slug": "desayunos"})
    finally:
        attributes.reset_turn_text(token)
    assert [p["id_producto"] for p in result["data"]] == [235]


def test_ocasion_sin_etiqueta_no_excluye_el_producto_del_indice():
    query_filter = search._build_filter({"id_ocasion": 1, "categoria_slug": "desayunos"})
    assert all(condition.key != "ocasiones_ids" for condition in query_filter.must)
    assert any(condition.key == "categoria_slug" for condition in query_filter.must)


def test_indice_conserva_procedencia_de_las_ocasiones_derivadas():
    from app.services.product_embedding_index import build_payload
    payload = build_payload(LOVE, "test", model="test", dimensions=3)
    assert payload["ocasiones_catalogo_ids"] == []
    assert payload["ocasiones_inferidas_ids"] == [1, 2]
    assert payload["ocasiones_ids"] == [1, 2]
    assert LOVE["ocasiones"] == []


@pytest.mark.asyncio
async def test_captura_sin_titulo_se_verifica_por_contenido_no_por_producto_previo(monkeypatch):
    async def evidence(_messages):
        return visual_product.VisualEvidence(phrases=("Tamal con salsa criolla", "Pan con chicharrón", "Taza de cerámica diseño Love"))

    calls = []

    async def execute(name, args):
        calls.append((name, args))
        if name == "buscar_productos":
            return json.dumps({"data": [CRIOLLO, LOVE]})
        assert name == "detalle_producto"
        return json.dumps({"data": {515: CRIOLLO, 235: LOVE}[args["id_producto"]]})

    monkeypatch.setattr(visual_product, "read_evidence", evidence)
    product = await visual_product.identify([], "desayuno criollo para cumpleaños para mujer", execute)
    assert product.id_producto == 235
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_captura_ambigua_no_asigna_identidad(monkeypatch):
    async def evidence(_messages):
        return visual_product.VisualEvidence(phrases=("Pan con chicharrón", "Bebida Bio"))

    async def execute(name, args):
        if name == "buscar_productos":
            return json.dumps({"data": [CRIOLLO, CRIOLLO | {"id_producto": 999}]})
        return json.dumps({"data": CRIOLLO | {"id_producto": args["id_producto"]}})

    monkeypatch.setattr(visual_product, "read_evidence", evidence)
    assert await visual_product.identify([], "desayuno criollo", execute) is None


@pytest.mark.asyncio
async def test_nombre_visible_no_se_sustituye_por_vecino_semantico(monkeypatch):
    async def evidence(_messages):
        return visual_product.VisualEvidence(name="DESAYUNO CRIOLLO LOVE")

    async def execute(name, args):
        assert name == "buscar_productos"
        return json.dumps({"data": [GUSTITO], "aproximado": True})

    monkeypatch.setattr(visual_product, "read_evidence", evidence)
    assert await visual_product.identify([], "desayuno criollo", execute) is None


@pytest.mark.asyncio
async def test_repetir_producto_es_valido_al_corregir_y_se_excluye_solo_en_mas(monkeypatch):
    async def specialist(*_a, **_k):
        return AgentResult(user_facing="Te muestro", artifacts=[Product.from_raw(LOVE)])

    monkeypatch.setattr(master, "run_specialist", specialist)
    state = ConversationState(shown_product_ids=[235])
    correction = await master._run_specialty("catalog_search", Turn(text="yo quiero desayuno criollo"), state, wa_id="test")
    assert correction.product_ids == [235]
    more = await master._run_specialty("catalog_search", Turn(text="más opciones"), state, wa_id="test")
    assert more.product_ids == []


def test_barrera_de_salida_admite_reconfirmar_y_detecta_repeticion_si_pide_mas():
    from app.guardrails.response import check_reply
    from app.tools.relevance import wants_more
    state = ConversationState(shown_product_ids=[235])
    kwargs = {"state": state, "artifacts": [Product.from_raw(LOVE)]}
    assert not any(v.rule == "no_repeated_products" for v in check_reply("Esta es su ficha", user_text="yo quiero desayuno criollo para mujer", **kwargs))
    assert any(v.rule == "no_repeated_products" for v in check_reply("Más opciones", user_text="tienes más", **kwargs))
    assert wants_more("muéstrame más peluches")


@pytest.mark.asyncio
async def test_replay_conversacion_criollo_con_modelo_que_elige_tool_generica(monkeypatch):
    async def literal(_client, args):
        assert args.get("q") == "desayuno criollo"
        return {"data": [CRIOLLO, LOVE]}

    responses = iter([
        ("catalogo_categoria", {"slug": "desayunos"}), None,
        ("buscar_semantico", {"q": "desayuno para cumpleaños", "id_ocasion": 1}), None,
    ])

    async def chat(_client, _payload):
        call = next(responses)
        message = {"role": "assistant", "content": "Te muestro opciones"}
        if call:
            message["content"] = None
            message["tool_calls"] = [{"id": "test", "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]
        return {"choices": [{"message": message}]}

    monkeypatch.setattr(catalog, "buscar_productos", literal)
    monkeypatch.setattr(agent, "_chat_completion", chat)
    monkeypatch.setattr(agent, "execute_tool", executor.execute_tool)
    for text in ("Desayuno criollo que precio tiene", "Yo quiero desayuno criollo para cumpleaños para mujer"):
        reply = await master.run_master([{"role": "user", "content": text}], wa_id="test", conversation_id=9001)
        assert "DESAYUNO CRIOLLO LOVE" in reply
        assert "149.60" in reply
        assert "para Hombre" not in reply
    state = await state_mod.load_state(9001)
    assert "criollo" in state.catalog_query


@pytest.mark.asyncio
async def test_segunda_consulta_verifica_un_producto_y_descarta_candidatos_previos(monkeypatch):
    from app.harness.registry import AGENTS
    responses = iter([
        ("buscar_productos", {"q": "criollo"}),
        ("detalle_producto", {"id_producto": 235}),
        None,
    ])
    executions = []

    async def chat(_client, payload):
        call = next(responses)
        message = {"role": "assistant", "content": "Esta es la ficha correcta"}
        if call:
            message["tool_calls"] = [{"id": str(len(executions)), "type": "function", "function": {"name": call[0], "arguments": json.dumps(call[1])}}]
        else:
            assert "tools" not in payload
        return {"choices": [{"message": message}]}

    async def execute(name, args):
        executions.append(name)
        return json.dumps({"data": LOVE if name == "detalle_producto" else [CRIOLLO, LOVE]})

    monkeypatch.setattr(agent, "_chat_completion", chat)
    monkeypatch.setattr(agent, "execute_tool", execute)
    spec = AGENTS["catalog"]
    result = await agent.run_specialist([{"role": "user", "content": "el criollo love"}], wa_id="test",
        tools_override=spec.tools(), include_handoff=False, include_memory=False,
        max_tool_calls=spec.max_tool_calls, max_tool_rounds=spec.max_tool_rounds, parallel_tool_calls=False)
    assert result.product_ids == [235]
    assert executions == ["buscar_productos", "detalle_producto"]
