"""Un mismo distrito puede tener varias tarifas, y cobrar la primera es adivinar.

Verificado en la API real (04/11/2026): ATE aparece dos veces, a $7 y a $15, cada una con
su zona en `informacion_distrito`; LA MOLINA y CHORRILLOS, dos veces y sin nada que las
distinga. `match_district` se quedaba con la primera: a quien vive por Puruchuco se le
decía un envío de menos de la mitad. Y notas como "No cubre Santa Rosa" no se mostraban.
"""
import pytest

from app.harness import coverage as cov
from app.harness import master as master_mod
from app.harness.aliases import pick_zone
from app.harness.contracts import EscalateReason
from app.harness.router import classify_rules
from app.harness.state import ConversationState, load_state, save_state

_cobertura_real = master_mod._checkout_coverage

ATE_1 = {"id_distrito": 140103, "nombre": "Ate", "tarifa_usd": 7.0, "tarifa_sol": 23.80,
         "informacion": "De Salamanca hasta estación mercado Santa Anita"}
ATE_2 = {"id_distrito": 250403, "nombre": "Ate", "tarifa_usd": 15.0, "tarifa_sol": 51.00,
         "informacion": "Real plaza puruchuco Hasta Nicolas Ayllon & entrada Huaycan"}
DISTRITOS = {"data": [
    ATE_1,
    ATE_2,
    {"id_distrito": 250405, "nombre": "La Molina", "tarifa_usd": 7.0, "tarifa_sol": 23.80, "informacion": ""},
    {"id_distrito": 140110, "nombre": "La Molina", "tarifa_usd": 6.0, "tarifa_sol": 20.40, "informacion": ""},
    {"id_distrito": 140120, "nombre": "Puente Piedra", "tarifa_usd": 5.89, "tarifa_sol": 20.03,
     "informacion": "No cubre Santa Rosa."},
    {"id_distrito": 140115, "nombre": "Miraflores", "tarifa_usd": 4.71, "tarifa_sol": 16.01, "informacion": ""},
]}


@pytest.fixture(autouse=True)
def distritos(monkeypatch):
    async def lista(_client, _args):
        return DISTRITOS

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", lista)
    monkeypatch.setattr(master_mod, "_checkout_coverage", _cobertura_real)


async def _turno(cid, texto):
    return await master_mod.run_master(
        [{"role": "user", "content": texto}], wa_id="51999", conversation_id=cid
    )


def _en_distrito(**kw):
    base = dict(checkout_step="district", chosen_product_id=1, chosen_product_name="Ramo",
                recent_products=[{"id_producto": 1, "nombre": "Ramo"}, {"id_producto": 2, "nombre": "Oso"}])
    base.update(kw)
    return ConversationState(**base)


# ── Zonas con tarifa distinta y descritas por la API ──────────────────

@pytest.mark.asyncio
async def test_si_el_distrito_tiene_zonas_pregunta_cual():
    raw = await cov.resolve_coverage("Ate", ConversationState())
    assert raw["structured"]["ambiguity"] == "zone"
    assert raw["structured"]["resolved_district"] is None
    assert "Salamanca" in raw["user_facing"]
    assert "puruchuco" in raw["user_facing"]
    assert "S/23.80" in raw["user_facing"]
    assert "S/51.00" in raw["user_facing"]
    assert len(raw["state_patch"]["district_options"]) == 2


@pytest.mark.parametrize(
    "texto,esperado",
    [("1", 140103), ("2", 250403), ("la 2", 250403), ("opción 1", 140103),
     ("Puruchuco", 250403), ("por salamanca", 140103), ("hola", None), ("3", None), ("Miraflores", None)],
)
def test_elegir_la_zona(texto, esperado):
    elegida = pick_zone(texto, [ATE_1, ATE_2])
    assert (elegida or {}).get("id_distrito") == esperado


@pytest.mark.asyncio
async def test_elegir_la_zona_fija_su_tarifa():
    raw = await cov.resolve_coverage("2", ConversationState(district_options=[ATE_1, ATE_2]))
    assert raw["structured"]["fee_sol"] == 51.0
    assert raw["state_patch"]["id_distrito"] == 250403
    assert raw["state_patch"]["district_options"] == []


@pytest.mark.asyncio
async def test_en_el_cierre_un_numero_elige_la_zona_y_no_un_producto():
    await save_state(1, _en_distrito())
    await _turno(1, "Ate")
    state = await load_state(1)
    assert state.checkout_step == "district"
    assert state.step_retries == 0, "preguntar la zona no es no haber entendido"

    reply = await _turno(1, "2")
    state = await load_state(1)
    assert state.checkout_step == "date"
    assert state.id_distrito == 250403
    assert state.shipping_fee_sol == 51.0
    assert state.chosen_product_id == 1
    assert "S/51.00" in reply
    assert state.district_options == []


@pytest.mark.asyncio
async def test_en_el_cierre_puede_cambiar_de_distrito_en_vez_de_elegir_zona():
    await save_state(2, _en_distrito())
    await _turno(2, "Ate")
    await _turno(2, "mejor Miraflores")
    state = await load_state(2)
    assert state.id_distrito == 140115
    assert state.district_options == []


@pytest.mark.asyncio
async def test_fuera_del_cierre_un_numero_tambien_elige_la_zona():
    await _turno(3, "¿llegan a Ate?")
    state = await load_state(3)
    assert len(state.district_options) == 2
    assert classify_rules("1", state).intent == "coverage"
    reply = await _turno(3, "1")
    state = await load_state(3)
    assert state.id_distrito == 140103
    assert "S/23.80" in reply


# ── Tarifas distintas sin forma de distinguirlas ──────────────────────

@pytest.mark.asyncio
async def test_sin_datos_para_distinguir_no_se_inventa_la_tarifa():
    raw = await cov.resolve_coverage("La Molina", ConversationState())
    assert raw["structured"]["covered"] is True
    assert raw["structured"]["resolved_district"] == "La Molina"
    assert raw["structured"]["fee_sol"] is None
    assert "S/" not in raw["user_facing"]
    assert "confirmamos" in raw["user_facing"]


@pytest.mark.asyncio
async def test_una_tarifa_anterior_no_se_queda_pegada_en_el_cierre():
    await save_state(4, _en_distrito(district="Miraflores", shipping_fee_sol=16.01, shipping_fee_usd=4.71))
    await _turno(4, "La Molina")
    state = await load_state(4)
    assert state.district == "La Molina"
    assert state.shipping_fee_sol is None
    assert state.shipping_fee_usd is None


@pytest.mark.asyncio
async def test_una_tarifa_anterior_no_se_queda_pegada_fuera_del_cierre():
    await save_state(5, ConversationState(district="Miraflores", shipping_fee_sol=16.01, shipping_fee_usd=4.71))
    await _turno(5, "¿llegan a La Molina?")
    state = await load_state(5)
    assert state.district == "La Molina"
    assert state.shipping_fee_sol is None


# ── Lo que la API avisa llega al cliente ──────────────────────────────

@pytest.mark.asyncio
async def test_las_exclusiones_del_distrito_se_le_dicen_al_cliente():
    raw = await cov.resolve_coverage("Puente Piedra", ConversationState())
    assert "No cubre Santa Rosa" in raw["user_facing"]
    assert "S/20.03" in raw["user_facing"]


@pytest.mark.asyncio
async def test_la_nota_sin_punto_no_se_pega_a_la_pregunta(monkeypatch):
    async def lista(_client, _args):
        return {"data": [{"id_distrito": 1, "nombre": "Callao", "tarifa_usd": 5.0, "tarifa_sol": 17.0,
                          "informacion": "No cubre Ventanilla"}]}

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", lista)
    raw = await cov.resolve_coverage("Callao", ConversationState())
    assert "No cubre Ventanilla. ¿Qué regalo" in raw["user_facing"]


@pytest.mark.asyncio
async def test_un_distrito_normal_no_cambia():
    raw = await cov.resolve_coverage("Miraflores", ConversationState())
    assert raw["structured"]["fee_sol"] == 16.01
    assert raw["structured"]["ambiguity"] is None
    assert "district_options" not in raw["state_patch"] or raw["state_patch"]["district_options"] == []


# ── La misma tarifa dos veces no es una ambigüedad ────────────────────

@pytest.mark.asyncio
async def test_repetido_con_la_misma_tarifa_no_pregunta(monkeypatch):
    async def lista(_client, _args):
        doble = dict(id_distrito=9, nombre="Lince", tarifa_usd=5.0, tarifa_sol=17.0, informacion="")
        return {"data": [doble, {**doble, "id_distrito": 10}]}

    monkeypatch.setattr(cov.catalog, "distritos_cobertura", lista)
    raw = await cov.resolve_coverage("Lince", ConversationState())
    assert raw["structured"]["fee_sol"] == 17.0
    assert raw["structured"]["ambiguity"] is None
