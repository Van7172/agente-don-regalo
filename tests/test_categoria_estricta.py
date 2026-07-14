"""Si el cliente pide una categoría, esa categoría es un límite duro.

Caso real (Diana): pidió "desayunos para aniversario" y el bot le mostró un arreglo
floral entre los desayunos. La causa no era el prompt: cuando la búsqueda semántica
traía pocos resultados, el executor **soltaba el filtro de categoría en silencio** y
rellenaba con lo que fuera.

La regla ahora es la del negocio: la API manda. Solo si la API no tiene nada de esa
categoría se recurre al vectorial, y esos productos van marcados como alternativas.
"""
import pytest

from app.tools.executor import enforce_category

DESAYUNO = {"id_producto": 1, "nombre": "Desayuno Buen Día", "categoria_slug": "desayunos"}
DESAYUNO_SUB = {
    "id_producto": 2,
    "nombre": "Desayuno Criollo",
    "categoria_slug": "desayunos-criollos",
}
RAMO = {
    "id_producto": 3,
    "nombre": "Arreglo floral flechaste mi corazón",
    "categoria_slug": "arreglos-florales",
}
PELUCHE = {"id_producto": 4, "nombre": "Peluche Oso", "categoria": "Peluches"}
SIN_CATEGORIA = {"id_producto": 5, "nombre": "Producto misterioso"}


def test_un_ramo_no_entra_en_una_lista_de_desayunos():
    """El bug exacto de la captura."""
    result = enforce_category({"data": [DESAYUNO, RAMO, PELUCHE]}, "desayunos")

    assert [p["id_producto"] for p in result["data"]] == [1]
    assert result["total"] == 1


def test_las_subcategorias_si_cuentan():
    """`desayunos` incluye `desayunos-criollos`: es el mismo producto para el cliente."""
    result = enforce_category({"data": [DESAYUNO, DESAYUNO_SUB]}, "desayunos")

    assert [p["id_producto"] for p in result["data"]] == [1, 2]


def test_un_producto_sin_categoria_no_se_cuela():
    """Sin categoría no podemos afirmar que pertenezca. Mejor mostrar menos."""
    result = enforce_category({"data": [DESAYUNO, SIN_CATEGORIA]}, "desayunos")

    assert [p["id_producto"] for p in result["data"]] == [1]


def test_sin_categoria_pedida_no_se_filtra_nada():
    """Una búsqueda libre ("algo romántico") no tiene categoría que imponer."""
    data = [DESAYUNO, RAMO, PELUCHE]
    assert enforce_category({"data": data}, "")["data"] == data


def test_qdrant_devuelve_el_slug_de_categoria():
    """Estaba en el payload de Qdrant y `_hit_to_producto` lo tiraba.

    Sin el slug, no había forma de comprobar que un resultado semántico fuera de
    verdad de la categoría pedida.
    """
    from app.tools.search import _hit_to_producto

    class Hit:
        payload = {
            "id_producto": 9,
            "nombre": "Desayuno X",
            "categoria": "Desayunos",
            "categoria_slug": "desayunos",
        }
        score = 0.9

    assert _hit_to_producto(Hit())["categoria_slug"] == "desayunos"


@pytest.mark.parametrize(
    "slug,esperados",
    [
        ("desayunos", [1, 2]),
        ("arreglos-florales", [3]),
        ("peluches", [4]),
    ],
)
def test_cada_categoria_solo_trae_lo_suyo(slug, esperados):
    result = enforce_category(
        {"data": [DESAYUNO, DESAYUNO_SUB, RAMO, PELUCHE, SIN_CATEGORIA]}, slug
    )
    assert [p["id_producto"] for p in result["data"]] == esperados
