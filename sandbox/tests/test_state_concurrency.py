"""Concurrencia del estado: nadie puede borrar lo que otro acaba de escribir.

`load_state` → mutar → `save_state` es leer-modificar-escribir de un documento
COMPLETO, y hay tres escritores sobre el mismo documento:

- el turno del cliente, que tarda segundos (el LLM está en medio);
- el releaser, que corre en segundo plano y FUERA del lock de Redis;
- `perform_handoff`, que escribe a mitad del propio turno.

El lock por conversación de Redis serializa los turnos ENTRANTES, así que no
protege de los otros dos: quedaba una ventana de *lost update* del tamaño de un
turno entero. Cada test de aquí es una de las formas en que se perdía.
"""
from __future__ import annotations

import time

import pytest

from app.crm import http_client as crm_http
from app.harness import state as state_mod
from app.harness.state import ConversationState, load_state, save_state, state_delta

CONV = 4242


async def _cargar():
    """Carga el estado y su foto, que es como debe cargarlo todo escritor."""
    st = await load_state(CONV)
    return st, st.to_dict()


@pytest.mark.asyncio
async def test_load_state_devuelve_copias_independientes():
    """El caché local no puede ser memoria compartida entre escritores.

    Devolvía EL MISMO objeto a todo el que lo pidiera, así que dos escritores se
    veían las mutaciones sin guardar y ninguna carrera se podía reproducir en un
    test: el bug solo aparecía en producción, contra el CRM.
    """
    inicial = ConversationState(district="Miraflores")
    await save_state(CONV, inicial)

    uno = await load_state(CONV)
    otro = await load_state(CONV)
    assert uno is not otro

    uno.district = "Surco"
    assert (await load_state(CONV)).district == "Miraflores"


@pytest.mark.asyncio
async def test_guardar_no_pisa_el_campo_que_toco_el_otro():
    """Dos escritores, campos distintos: sobreviven los dos."""
    await save_state(CONV, ConversationState())

    lento, base_lento = await _cargar()  # el turno: carga y se va a pensar

    rapido, base_rapido = await _cargar()  # el releaser, en paralelo
    rapido.keep_human = True
    await save_state(CONV, rapido, base=base_rapido)

    lento.checkout_step = "date"  # el turno vuelve del LLM y guarda lo suyo
    await save_state(CONV, lento, base=base_lento)

    final = await load_state(CONV)
    assert final.checkout_step == "date", "el turno perdió su avance"
    assert final.keep_human is True, "el turno borró lo que escribió el releaser"


@pytest.mark.asyncio
async def test_handoff_at_sobrevive_al_guardado_final_del_turno():
    """El caso que ya estaba vivo en producción.

    `perform_handoff` corre DENTRO del turno: carga el estado fresco, escribe
    `handoff_at` y guarda. Al terminar, `master` guardaba su propia copia —
    cargada ANTES del handoff, con `handoff_at` en None— y lo borraba.

    Ese campo es el ancla del releaser mientras el asesor todavía no ha escrito:
    sin él, `should_release_to_ai` no tiene con qué medir y el bot recuperaba el
    chat al instante de haber prometido un asesor. O sea que el guardado final
    del turno deshacía justo el arreglo de ese incidente.
    """
    await save_state(CONV, ConversationState())

    turno, base_turno = await _cargar()

    handoff, base_handoff = await _cargar()
    handoff.handoff_at = time.time()
    handoff.handoff_reason = "pide un asesor"
    await save_state(CONV, handoff, base=base_handoff)

    turno.intent_last = "escalate"
    await save_state(CONV, turno, base=base_turno)

    final = await load_state(CONV)
    assert final.handoff_at is not None, "el turno borró el ancla del releaser"
    assert final.handoff_reason == "pide un asesor"
    assert final.intent_last == "escalate"


@pytest.mark.asyncio
async def test_releaser_no_deshace_el_avance_del_cierre():
    """El releaser solo escribe lo suyo, aunque su foto sea de hace un turno."""
    await save_state(CONV, ConversationState(checkout_step="district"))

    releaser, base_releaser = await _cargar()  # lee y empieza a decidir

    # Mientras tanto el cliente avanza dos pasos del cierre.
    turno, base_turno = await _cargar()
    turno.checkout_step = "schedule"
    turno.district = "San Isidro"
    await save_state(CONV, turno, base=base_turno)

    releaser.keep_human = False
    await save_state(CONV, releaser, base=base_releaser)

    final = await load_state(CONV)
    assert final.checkout_step == "schedule", "el releaser devolvió el cierre atrás"
    assert final.district == "San Isidro"


@pytest.mark.asyncio
async def test_el_ultimo_escritor_de_un_mismo_campo_gana():
    """Fusionar no es inventarse una tercera respuesta.

    Si los dos tocan el MISMO campo no hay nada que conservar: gana quien escribe
    después. Lo que no puede pasar es que gane quien escribió antes.
    """
    await save_state(CONV, ConversationState())

    uno, base_uno = await _cargar()
    dos, base_dos = await _cargar()

    dos.district = "Barranco"
    await save_state(CONV, dos, base=base_dos)

    uno.district = "Lince"
    await save_state(CONV, uno, base=base_uno)

    assert (await load_state(CONV)).district == "Lince"


@pytest.mark.asyncio
async def test_sin_base_se_vuelve_a_pisar():
    """Prueba de que estos tests detectan la regresión, no de que el bug sea bueno.

    Quitar el `base=` de cualquiera de los tres escritores devuelve exactamente
    el comportamiento anterior: el documento entero es el delta y el último en
    guardar borra lo del otro. Si este test empezara a fallar querría decir que
    la fusión pasó a ser incondicional y los de arriba dejarían de probar nada.
    """
    await save_state(CONV, ConversationState())

    lento = await load_state(CONV)  # sin foto: escritura a ciegas

    otro, base_otro = await _cargar()
    otro.handoff_at = 1234.0
    await save_state(CONV, otro, base=base_otro)

    lento.checkout_step = "date"
    await save_state(CONV, lento)  # sin base=

    assert (await load_state(CONV)).handoff_at is None


@pytest.mark.asyncio
async def test_la_version_avanza_en_cada_escritura():
    await save_state(CONV, ConversationState())
    primera = (await load_state(CONV)).version

    st, base = await _cargar()
    st.district = "Miraflores"
    await save_state(CONV, st, base=base)

    assert (await load_state(CONV)).version == primera + 1


def test_state_patch_no_puede_tocar_la_version():
    """Un `state_patch` de un especialista rompería el control de concurrencia."""
    st = ConversationState(version=7)
    st.patch({"version": 99, "district": "Surco"})
    assert st.version == 7
    assert st.district == "Surco"


def test_state_delta_sin_base_es_el_documento_entero():
    """Quien no tomó la foto escribe como siempre: pisando."""
    st = ConversationState(district="Surco")
    delta = state_delta(None, st)
    assert delta["district"] == "Surco"
    assert "version" not in delta, "la versión la lleva la persistencia, no el delta"


def test_state_delta_solo_trae_lo_que_cambio():
    st = ConversationState(district="Surco")
    base = st.to_dict()
    st.checkout_step = "date"
    assert state_delta(base, st) == {"checkout_step": "date"}


# ── camino externo: CAS contra el CRM ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cas_reintenta_y_fusiona_cuando_pierde_la_carrera(monkeypatch):
    """Ante `stored: false`, el agente relee y reaplica SOLO su delta."""
    almacen = {"doc": ConversationState(version=3, district="Miraflores").to_dict()}
    intentos: list[int] = []

    async def fake_cas(key, value, expected_version):
        import json

        intentos.append(expected_version)
        actual = int(almacen["doc"].get("version", 0))
        if expected_version != actual:
            return False
        almacen["doc"] = json.loads(value)
        return True

    async def fake_get_setting(key):
        import json

        return json.dumps(almacen["doc"], ensure_ascii=False)

    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(state_mod.crm_http, "put_setting_cas", fake_cas)
    monkeypatch.setattr(state_mod.crm_http, "get_setting", fake_get_setting)

    st = await load_state(CONV)
    base = st.to_dict()
    assert st.version == 3

    # Otro escritor se adelanta: el documento pasa a la versión 4 con keep_human.
    almacen["doc"] = {**almacen["doc"], "version": 4, "keep_human": True}

    st.checkout_step = "date"
    await save_state(CONV, st, base=base)

    assert intentos == [3, 4], "debía reintentar con la versión fresca"
    assert almacen["doc"]["checkout_step"] == "date"
    assert almacen["doc"]["keep_human"] is True, "el reintento pisó al otro escritor"
    assert almacen["doc"]["version"] == 5


@pytest.mark.asyncio
async def test_crm_sin_cas_guarda_igual(monkeypatch):
    """Un CRM viejo no puede dejar al agente sin persistir el estado.

    Mismo criterio que el claim del outbox: si el CRM todavía no sabe hacerlo, se
    escribe a pelo. Quedarse sin guardar el estado es peor que la carrera.
    """
    escrito: dict = {}

    async def sin_soporte(key, value, expected_version):
        return None

    async def fake_put_setting(key, value):
        escrito["value"] = value

    monkeypatch.setattr(state_mod.crm_http, "crm_enabled", lambda: True)
    monkeypatch.setattr(state_mod.crm_http, "put_setting_cas", sin_soporte)
    monkeypatch.setattr(state_mod.crm_http, "put_setting", fake_put_setting)

    st = ConversationState(district="Surco")
    await save_state(CONV, st, base=st.to_dict())

    assert "Surco" in escrito["value"]


@pytest.mark.asyncio
async def test_cas_no_soportado_no_se_reintenta_en_cada_guardado(monkeypatch):
    """El 404 se recuerda: un CRM viejo no puede sumar un fallo por turno.

    `_request` cuenta los 4xx como error del CRM en el circuit breaker. Volver a
    llamar a un endpoint que no existe en cada guardado acabaría abriendo el
    circuito y dejando al agente sin CRM por una función opcional.
    """
    import httpx

    llamadas = {"n": 0}

    async def fake_request(method, path, **kwargs):
        llamadas["n"] += 1
        response = httpx.Response(404, request=httpx.Request(method, "http://crm/x"))
        raise httpx.HTTPStatusError("404", request=response.request, response=response)

    monkeypatch.setattr(crm_http, "_request", fake_request)
    crm_http.reset_cas_support()

    assert await crm_http.put_setting_cas("k", "v", 0) is None
    assert await crm_http.put_setting_cas("k", "v", 0) is None
    assert llamadas["n"] == 1, "el 404 debía recordarse"

    crm_http.reset_cas_support()
