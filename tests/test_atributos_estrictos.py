"""Si el cliente pide un color, ese color es un límite duro.

Caso real (Marcos): pidió "flores amarillas" y recibió "Ramo de 1 rosa" (roja).
La causa no era el prompt: la búsqueda semántica bonifica coincidencias léxicas
(+0.12) pero no descarta lo que no cumple el color. Igual que con la categoría,
el atributo nombrado manda — y si no hay stock de ese color, se dice, no se
sustituye por otro.
"""
import pytest

from app.tools.attributes import (
    extract_color_attributes,
    enforce_attributes,
    product_matches_attributes,
)

ROSA_ROJA = {
    "id_producto": 1,
    "nombre": "Ramo de 1 rosa",
    "descripcion_corta": "Rosa roja con globo corazón",
}
ROSAS_AMARILLAS = {
    "id_producto": 2,
    "nombre": "Ramo de 6 rosas amarillas radiantes",
    "descripcion_corta": "Seis rosas amarillas con follaje",
}
GIRASOLES = {
    "id_producto": 3,
    "nombre": "Ramo Radiante de 6 Girasoles",
    "descripcion_corta": "Seis girasoles radiantes",
}
ROSAS_BLANCAS = {
    "id_producto": 4,
    "nombre": "Ramo de rosas blancas",
    "descripcion_corta": "Rosas blancas en papel coreano",
}


def test_detecta_amarillas_en_la_consulta():
    attrs = extract_color_attributes("Flores amarillas?")
    assert [a.id for a in attrs] == ["amarillo"]


def test_ramo_rojo_no_pasa_por_flores_amarillas():
    """El bug exacto de la captura."""
    attrs = extract_color_attributes("flores amarillas")
    result = enforce_attributes(
        {"data": [ROSA_ROJA, ROSAS_AMARILLAS, GIRASOLES]}, attrs
    )

    assert [p["id_producto"] for p in result["data"]] == [2, 3]
    assert result["total"] == 2


def test_girasol_cuenta_como_amarillo():
    """Los girasoles son amarillos aunque el nombre no diga el color."""
    attrs = extract_color_attributes("quiero flores amarillas")
    assert product_matches_attributes(GIRASOLES, attrs)


def test_rosas_blancas_no_acepta_rojas():
    attrs = extract_color_attributes("Quiero rosas blancas")
    result = enforce_attributes(
        {"data": [ROSA_ROJA, ROSAS_BLANCAS, ROSAS_AMARILLAS]}, attrs
    )
    assert [p["id_producto"] for p in result["data"]] == [4]


def test_sin_color_pedido_no_se_filtra_nada():
    data = [ROSA_ROJA, ROSAS_AMARILLAS, GIRASOLES]
    assert enforce_attributes({"data": data}, [])["data"] == data


def test_sin_coincidencias_deja_lista_vacia():
    """Mejor cero productos que colar otro color."""
    attrs = extract_color_attributes("flores azules")
    result = enforce_attributes({"data": [ROSA_ROJA, ROSAS_AMARILLAS]}, attrs)
    assert result["data"] == []
    assert result["total"] == 0


def test_colors_from_une_tool_y_turno():
    """El modelo busca `q=flores` pero el cliente dijo amarillas."""
    from app.tools.attributes import colors_from

    attrs = colors_from("flores", "Flores amarillas?")
    assert [a.id for a in attrs] == ["amarillo"]


@pytest.mark.asyncio
async def test_buscar_semantico_no_cuela_otro_color(monkeypatch):
    """Qdrant devuelve un ramo rojo ante 'flores amarillas'; el executor lo corta."""
    import json
    from app.tools import executor as ex

    async def fake_sem(client, args):
        return {
            "data": [
                {
                    "id_producto": 1,
                    "nombre": "Ramo de 1 rosa",
                    "descripcion_corta": "Rosa roja",
                    "categoria_slug": "arreglos-florales",
                },
                {
                    "id_producto": 2,
                    "nombre": "Ramo de 6 rosas amarillas",
                    "descripcion_corta": "Rosas amarillas",
                    "categoria_slug": "arreglos-florales",
                },
            ],
            "total": 2,
            "fuente": "semantico",
        }

    async def fake_api(client, args):
        return {
            "data": [
                {
                    "id_producto": 2,
                    "nombre": "Ramo de 6 rosas amarillas",
                    "descripcion_corta": "Rosas amarillas",
                    "categoria_slug": "arreglos-florales",
                },
            ],
            "total": 1,
        }

    monkeypatch.setattr(ex.search, "buscar_semantico", fake_sem)
    monkeypatch.setattr(ex.catalog, "buscar_productos", fake_api)

    raw = await ex.execute_tool(
        "buscar_semantico",
        {"q": "flores amarillas", "categoria_slug": "arreglos-florales"},
    )
    data = json.loads(raw)
    assert [p["id_producto"] for p in data["data"]] == [2]
