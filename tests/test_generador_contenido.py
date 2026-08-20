"""El estudio de contenido del CRM: copys de redes a partir del catálogo real.

Lo que se protege aquí no es el estilo del texto —eso no es testeable— sino las
dos cosas que convierten un post en un problema: que el precio salga del
catálogo y no del modelo, y que no se publique nada que el modelo se inventó.
"""
from __future__ import annotations

import json

import pytest

from app.services import social_copy


PRODUCTO = {
    "id_producto": 1234,
    "nombre": "Desayuno Criollo Sorpresa",
    "descripcion_corta": "Bandeja criolla con jugo, café y postre.",
    "precio_sol": 149.0,
    "precio_usd": 40.0,
    "imagen_url": "https://donregalo.pe/img/medium/criollo.jpg",
    "categoria": "Desayunos",
}


def _respuesta(variantes: list[dict]) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps({"variantes": variantes})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@pytest.fixture
def llm(monkeypatch):
    """Sustituye la llamada a OpenAI y guarda el payload que se le mandó."""
    capturado: dict = {}

    def _instalar(variantes: list[dict]):
        async def _fake_call(self, func):  # firma de circuit_breaker(...).call
            return _respuesta(variantes)

        monkeypatch.setattr(
            "app.resilience.CircuitBreaker.call", _fake_call, raising=False
        )
        return capturado

    monkeypatch.setattr(
        "app.config.settings.openai_api_key", "sk-test", raising=False
    )
    return _instalar


@pytest.mark.asyncio
async def test_el_precio_lo_pone_el_catalogo_no_el_modelo(llm):
    """El importe del post sale del producto canónico, en soles."""
    llm([{"titular": "Sorprende hoy", "cuerpo": "Un desayuno que se recuerda.", "cta": "Escríbenos", "hashtags": ["regalos"]}])

    variantes = await social_copy.generar_copys(producto=PRODUCTO)

    assert variantes[0]["precio"] == "S/ 149.00"
    assert "S/ 149.00" in variantes[0]["texto"]


@pytest.mark.asyncio
async def test_una_variante_con_precio_inventado_se_descarta_entera(llm):
    """Publicidad engañosa: la variante se tira, no se parchea.

    Borrar solo la cifra dejaba "Solo hoy a con de descuento", y esto no es un
    chat donde una frase rara se arregla en el mensaje siguiente: es una pieza
    que alguien pega en Instagram tal cual. Las variantes limpias sobreviven.
    """
    llm(
        [
            {
                "titular": "Llévalo por S/ 99",
                "cuerpo": "Solo hoy a $29 con 50% de descuento.",
                "cta": "Pide el tuyo",
                "hashtags": [],
            },
            {
                "titular": "Sorprende hoy",
                "cuerpo": "Un desayuno que se recuerda.",
                "cta": "Escríbenos",
                "hashtags": [],
            },
        ]
    )

    variantes = await social_copy.generar_copys(producto=PRODUCTO)

    assert len(variantes) == 1
    assert variantes[0]["titular"] == "Sorprende hoy"
    # El precio real sí, y una sola vez.
    assert variantes[0]["texto"].count("S/ 149.00") == 1


@pytest.mark.asyncio
async def test_un_porcentaje_legitimo_no_descarta_la_variante(llm):
    """"100% peruano" es marketing normal, no un descuento inventado.

    Un filtro que se lleve por delante cualquier porcentaje acaba mutilando
    copys buenos, y entonces el equipo deja de usar la sección.
    """
    llm([{"titular": "Café 100% peruano", "cuerpo": "Del norte a tu mesa.", "cta": "", "hashtags": []}])

    variantes = await social_copy.generar_copys(producto=PRODUCTO)

    assert variantes[0]["titular"] == "Café 100% peruano"


@pytest.mark.asyncio
async def test_sin_precio_no_se_cuela_ninguna_cifra(llm):
    """Si el asesor apaga el precio, la pieza sale sin ningún importe."""
    llm(
        [
            {"titular": "Desde S/ 120", "cuerpo": "x", "cta": "", "hashtags": []},
            {"titular": "Un detalle que llega", "cuerpo": "Directo a su puerta.", "cta": "", "hashtags": []},
        ]
    )

    variantes = await social_copy.generar_copys(producto=PRODUCTO, incluir_precio=False)

    assert len(variantes) == 1
    assert variantes[0]["precio"] == ""
    assert "S/" not in variantes[0]["texto"]


@pytest.mark.asyncio
async def test_la_oferta_muestra_el_precio_anterior(llm):
    llm([{"titular": "Oferta", "cuerpo": "Aprovecha.", "cta": "", "hashtags": []}])

    variantes = await social_copy.generar_copys(
        producto={**PRODUCTO, "tiene_oferta": True, "precio_lista_sol": 199.0}
    )

    assert variantes[0]["precio"] == "S/ 149.00 (antes S/ 199.00)"


@pytest.mark.asyncio
async def test_se_piden_varias_variantes_distintas(llm):
    """Tres redacciones para elegir: es lo que pidió el equipo comercial."""
    llm(
        [
            {"titular": "Uno", "cuerpo": "A", "cta": "", "hashtags": []},
            {"titular": "Dos", "cuerpo": "B", "cta": "", "hashtags": []},
            {"titular": "Tres", "cuerpo": "C", "cta": "", "hashtags": []},
        ]
    )

    variantes = await social_copy.generar_copys(producto=PRODUCTO, variantes=3)

    assert [v["titular"] for v in variantes] == ["Uno", "Dos", "Tres"]


@pytest.mark.asyncio
async def test_nunca_mas_de_tres_variantes(llm):
    """El tope es del código: pedir seis se paga y nadie compara seis."""
    llm([{"titular": f"V{i}", "cuerpo": "x", "cta": "", "hashtags": []} for i in range(6)])

    variantes = await social_copy.generar_copys(producto=PRODUCTO, variantes=99)

    assert len(variantes) <= social_copy.MAX_VARIANTES


@pytest.mark.asyncio
async def test_una_respuesta_vacia_falla_en_vez_de_publicar_relleno(llm):
    """Mejor "no se pudo generar" que un texto de relleno que acabe publicado."""
    llm([])

    with pytest.raises(Exception):
        await social_copy.generar_copys(producto=PRODUCTO)


@pytest.mark.asyncio
async def test_el_titular_largo_se_corta_por_palabra(llm):
    largo = "Regala " + "sorpresas increíbles " * 10
    llm([{"titular": largo, "cuerpo": "x", "cta": "", "hashtags": []}])

    variantes = await social_copy.generar_copys(producto=PRODUCTO)

    titular = variantes[0]["titular"]
    assert len(titular) <= social_copy.MAX_TITULAR + 1  # +1 por el "…"
    assert not titular.endswith(" …")


@pytest.mark.asyncio
async def test_el_cta_se_omite_si_el_asesor_lo_apaga(llm):
    llm([{"titular": "Hola", "cuerpo": "x", "cta": "Escríbenos ya", "hashtags": []}])

    variantes = await social_copy.generar_copys(producto=PRODUCTO, incluir_cta=False)

    assert variantes[0]["cta"] == ""
    assert "Escríbenos" not in variantes[0]["texto"]


@pytest.mark.asyncio
async def test_el_buscador_calla_por_debajo_de_tres_caracteres(monkeypatch):
    """El selector se habilita a los 3 caracteres: por debajo no se sale a la red.

    Con menos, la API devuelve medio catálogo y el asesor elige a ciegas — y
    encima se paga una llamada por cada tecla.
    """
    from app import api_internal

    llamado = False

    async def _no_deberia(*_a, **_k):
        nonlocal llamado
        llamado = True
        return {"data": []}

    monkeypatch.setattr("app.tools.catalog.buscar_productos", _no_deberia)

    salida = await api_internal.catalog_search(
        q="de", x_agent_token=api_internal.settings.agent_internal_token
    )

    assert salida == {"data": [], "total": 0}
    assert llamado is False


@pytest.mark.asyncio
async def test_el_buscador_devuelve_la_forma_canonica(monkeypatch):
    """Los productos salen del adapter, en soles, con la foto viva del listado."""
    from app import api_internal

    async def _buscar(_client, args):
        assert args["q"] == "desayuno"
        return {"data": [PRODUCTO], "total": 1}

    monkeypatch.setattr("app.tools.catalog.buscar_productos", _buscar)

    salida = await api_internal.catalog_search(
        q="desayuno", x_agent_token=api_internal.settings.agent_internal_token
    )

    assert salida["total"] == 1
    assert salida["data"][0]["precio_sol"] == 149.0
    assert salida["data"][0]["imagen_url"].endswith("criollo.jpg")


@pytest.mark.asyncio
async def test_el_estudio_de_contenido_no_es_publico():
    """Mismo token interno que el resto de `/internal`."""
    from fastapi import HTTPException

    from app import api_internal

    if not api_internal.settings.agent_internal_token:
        pytest.skip("sin token configurado no hay nada que comprobar")

    with pytest.raises(HTTPException):
        await api_internal.catalog_search(q="desayuno", x_agent_token="incorrecto")


@pytest.mark.asyncio
async def test_sin_producto_no_se_redacta_nada():
    """El post se arma sobre un producto del catálogo o no se arma."""
    from fastapi import HTTPException

    from app import api_internal

    cuerpo = api_internal.ContentDraftBody(producto={})
    with pytest.raises(HTTPException) as err:
        await api_internal.content_draft(
            cuerpo, x_agent_token=api_internal.settings.agent_internal_token
        )
    assert err.value.status_code == 400


def test_los_tonos_ofrecidos_estan_documentados():
    """El selector del CRM y el prompt tienen que hablar de los mismos tonos."""
    assert "casual" in social_copy.TONOS
    assert social_copy.TONO_POR_DEFECTO in social_copy.TONOS
    assert social_copy.FORMATO_POR_DEFECTO == "9:16"
    # 9:16 por defecto: es historia de Instagram/TikTok, de donde vienen los leads.
    assert list(social_copy.FORMATOS)[0] == "9:16"
