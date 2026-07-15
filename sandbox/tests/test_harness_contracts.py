"""El lazo de estado: lo que el especialista aprende vuelve al orquestador.

Antes `run_agent` devolvía `str` y el master intentaba rescatar los ids de
producto con una regex sobre la prosa de la respuesta. La respuesta al cliente
nunca contiene JSON, así que la regex devolvía siempre vacío: `excluir_ids` no se
enviaba nunca (productos repetidos) y el resumen del pedido decía literalmente
"Producto elegido".
"""
import pytest

from app.harness.checkout import advance_checkout, resolve_chosen_product
from app.harness.contracts import AgentResult, Product, extract_products
from app.harness.master import _reduce
from app.harness.policies import dedupe_artifacts, grounding_violation
from app.harness.state import ConversationState

PANDA = Product(id_producto=11, nombre="Terrario Familia Panditas", precio_sol=87.5)
GIRASOL = Product(id_producto=22, nombre="Ramo de 3 Girasoles Radiantes", precio_sol=120.0)


def test_extract_products_lee_el_resultado_real_de_una_tool():
    payload = {
        "data": [
            {"id_producto": 11, "nombre": "Terrario Familia Panditas", "precio_sol": 87.5},
            {"id_producto": 22, "nombre": "Ramo de Girasoles", "precio": 25.0},
        ]
    }
    products = extract_products(payload)
    assert [p.id_producto for p in products] == [11, 22]
    assert products[0].precio_sol == 87.5


def test_extract_products_acepta_detalle_producto_sin_envoltorio():
    products = extract_products({"id_producto": 7, "nombre": "Cesta Criolla"})
    assert [p.id_producto for p in products] == [7]


def test_reduce_llena_shown_product_ids_desde_los_artifacts():
    """El bug original: los ids salían de una regex sobre la prosa, y nunca llegaban."""
    state = ConversationState()
    result = AgentResult(
        user_facing="• 🎁 *Terrario Familia Panditas* — S/87.50",  # sin JSON, como siempre
        artifacts=[PANDA, GIRASOL],
    )

    _reduce(state, result)

    assert state.shown_product_ids == [11, 22]
    assert [p["nombre"] for p in state.recent_products] == [
        "Terrario Familia Panditas",
        "Ramo de 3 Girasoles Radiantes",
    ]


def test_no_se_repite_un_producto_ya_mostrado():
    state = ConversationState(shown_product_ids=[11])
    nuevos = dedupe_artifacts(state.shown_product_ids, [PANDA, GIRASOL])
    assert [p.id_producto for p in nuevos] == [22]


def test_el_resumen_del_pedido_nombra_el_producto_real():
    """Antes: 'Producto: Producto elegido'."""
    state = ConversationState(
        chosen_product_name="Terrario Familia Panditas",
        district="Surco",
        date="viernes",
        time_slot="09:00 AM a 11:00 AM",
        dedicatoria="Sin dedicatoria",
        nombre_destinatario="Ana",
        apellidos_destinatario="Pérez",
        telefono_destinatario="999888777",
        direccion="Av. Primavera 123",
        tipo=0,
        checkout_step="contact",
    )
    # Al completar los datos del comprador se muestra el resumen final.
    _, reply, _ = advance_checkout(state, "Luis Gómez luis@mail.com")

    assert state.checkout_step == "summary"
    assert "Terrario Familia Panditas" in reply
    assert "Producto elegido" not in reply


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("quiero el panditas", 11),
        ("me gusta el de girasoles", 22),
        ("el segundo", 22),
        ("la opción 1", 11),
    ],
)
def test_resolve_chosen_product(texto, esperado):
    state = ConversationState(
        recent_products=[
            {"id_producto": 11, "nombre": "Terrario Familia Panditas"},
            {"id_producto": 22, "nombre": "Ramo de 3 Girasoles Radiantes"},
        ]
    )
    chosen = resolve_chosen_product(state, texto)
    assert chosen is not None and chosen[0] == esperado


def test_referencia_ambigua_no_adivina_el_producto():
    """'ese' con dos productos a la vista: preguntar, no cerrar el pedido equivocado."""
    state = ConversationState(
        recent_products=[
            {"id_producto": 11, "nombre": "Terrario Familia Panditas"},
            {"id_producto": 22, "nombre": "Ramo de 3 Girasoles Radiantes"},
        ]
    )
    assert resolve_chosen_product(state, "ese lo quiero") is None


def test_un_solo_producto_a_la_vista_si_es_univoco():
    state = ConversationState(
        recent_products=[{"id_producto": 11, "nombre": "Terrario Familia Panditas"}]
    )
    chosen = resolve_chosen_product(state, "lo quiero")
    assert chosen == (11, "Terrario Familia Panditas")


def test_grounding_caza_un_precio_que_ninguna_tool_devolvio():
    assert grounding_violation("Te lo dejo en S/50.00 😊", [PANDA]) is not None
    assert grounding_violation("Cuesta S/87.50 ($25.00)", [PANDA]) is None


def test_grounding_no_opina_si_el_turno_no_cito_productos():
    assert grounding_violation("¿A qué distrito lo enviamos?", []) is None
