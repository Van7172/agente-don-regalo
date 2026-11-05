"""Lo que el bot dice haber guardado tiene que quedar guardado.

La tool ofrece `nombre`, `distrito` y `nota`, pero en modo CRM externo (el de
producción) solo se traducía `nombre`: las otras dos se perdían en silencio mientras
el modelo recibía `ok`. El CRM no tiene columnas propias para ellas, así que van a
`situacion` y `resumen`, que sí se leen de vuelta en cada turno.
"""
import json

import pytest

from app.crm import http_client as crm_http
from app.services import agent as ag
from app.services import buffer as buffer_mod
from app.tools.definitions import MEMORY_TOOL


@pytest.fixture
def crm(monkeypatch):
    estado = {"guardado": [], "memoria": {}, "falla": False, "respuestas": []}

    async def put(_wa, patch):
        if estado["falla"]:
            raise RuntimeError("CRM caído")
        estado["guardado"].append(patch)
        return {"ok": True}

    async def get(_wa):
        return estado["memoria"]

    monkeypatch.setattr(crm_http, "put_memory", put)
    monkeypatch.setattr(crm_http, "get_memory", get)
    return estado


async def _guarda(monkeypatch, crm, **args):
    llamadas = {"n": 0}

    async def completion(*_a, **_k):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "guardar_datos_cliente", "arguments": json.dumps(args),
                }}],
            }}], "usage": {}}
        return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Listo"}}], "usage": {}}

    monkeypatch.setattr(ag, "_chat_completion", completion)
    await ag.run_specialist(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "hola"}],
        wa_id="51999000111",
        conversation_id=9,
        use_external_crm=True,
        tools_override=[MEMORY_TOOL],
        include_handoff=False,
        include_memory=True,
        model="m",
        max_tool_rounds=2,
    )
    return crm["guardado"]


@pytest.mark.asyncio
async def test_el_nombre_se_guarda(monkeypatch, crm):
    guardado = await _guarda(monkeypatch, crm, nombre="Pedro")
    assert guardado[0]["name"] == "Pedro"


@pytest.mark.asyncio
async def test_el_distrito_se_guarda(monkeypatch, crm):
    guardado = await _guarda(monkeypatch, crm, distrito="Surco")
    assert "Surco" in guardado[0]["situacion"]


@pytest.mark.asyncio
async def test_la_nota_se_guarda(monkeypatch, crm):
    guardado = await _guarda(monkeypatch, crm, nota="Prefiere desayunos sencillos")
    assert "Prefiere desayunos sencillos" in guardado[0]["resumen"]


@pytest.mark.asyncio
async def test_las_notas_se_acumulan(monkeypatch, crm):
    crm["memoria"] = {"resumen_memory": "[01/10/26] Compró un ramo"}
    guardado = await _guarda(monkeypatch, crm, nota="Le gustan los girasoles")
    assert "Compró un ramo" in guardado[0]["resumen"]
    assert "Le gustan los girasoles" in guardado[0]["resumen"]


@pytest.mark.asyncio
async def test_la_misma_nota_no_se_repite(monkeypatch, crm):
    crm["memoria"] = {"resumen_memory": "[01/10/26] Le gustan los girasoles"}
    guardado = await _guarda(monkeypatch, crm, nota="Le gustan los girasoles", nombre="Ana")
    assert "resumen" not in guardado[0]


@pytest.mark.asyncio
async def test_el_resumen_no_crece_sin_limite(monkeypatch, crm):
    crm["memoria"] = {"resumen_memory": "x" * 5000}
    guardado = await _guarda(monkeypatch, crm, nota="Última nota")
    assert len(guardado[0]["resumen"]) <= 2000
    assert guardado[0]["resumen"].endswith("Última nota")


@pytest.mark.asyncio
async def test_una_nota_no_se_mezcla_con_el_respaldo_del_estado(monkeypatch, crm):
    crm["memoria"] = {"resumen_memory": 'HARNESS:{"checkout_step": "date"}'}
    guardado = await _guarda(monkeypatch, crm, nota="Le gustan los girasoles")
    assert "HARNESS" not in guardado[0]["resumen"]


@pytest.mark.asyncio
async def test_si_el_crm_falla_no_dice_que_guardo(monkeypatch, crm):
    crm["falla"] = True
    respuesta = await ag._save_external_memory("51999", {"nombre": "Pedro"})
    assert json.loads(respuesta)["ok"] is False


def test_lo_guardado_vuelve_al_perfil():
    perfil = buffer_mod._profile_from_memory({
        "nombre_memory": "Pedro",
        "situacion_memory": "Distrito habitual: Surco",
        "resumen_memory": "[01/10/26] Le gustan los girasoles",
    })
    assert perfil["nombre"] == "Pedro"
    assert perfil["situacion_memory"] == "Distrito habitual: Surco"
    assert "girasoles" in perfil["resumen_memory"]


def test_el_respaldo_del_estado_no_llega_al_prompt():
    """Lleva datos del pedido en JSON: no es memoria del cliente."""
    perfil = buffer_mod._profile_from_memory({"resumen_memory": 'HARNESS:{"direccion": "Jr. Ica"}'})
    assert "resumen_memory" not in perfil
