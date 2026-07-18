"""Los adaptadores, contra payloads REALES de donregalo.pe.

Los fixtures de `tests/fixtures/api/` están grabados de la API en producción. El
test anterior de cobertura inventaba la forma del payload
(`{"nombre": "Independencia", "precio_sol": 17.0}`), una forma que la API no
devuelve nunca: el test pasaba en verde mientras `match_district` no acertaba un
solo distrito en producción. Aquí no se inventa nada.
"""
import json
import pathlib

import pytest

from app.harness.contracts import extract_products
from app.harness.coverage import match_district
from app.tools import adapters

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "api"
RATE = 3.4


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ── Distritos ─────────────────────────────────────────────────────────

def test_los_distritos_reales_ahora_hacen_match():
    """El bug vivo: la API devuelve `nombre_distrito`, el código leía `nombre`."""
    payload = adapters.districts_payload(load("distritos"), RATE)
    distritos = payload["data"]

    for query in ("Miraflores", "Surco", "Ate", "san isidro"):
        matched = match_district(query, distritos)
        assert matched is not None, f"{query} debería tener cobertura"
        assert matched["nombre"]


def test_la_tarifa_de_envio_llega_en_ambas_monedas():
    """La API entrega la tarifa en USD (`tarifa_envio_distrito`, moneda USD)."""
    crudo = load("distritos")["data"][0]
    assert crudo["moneda"] == "USD"  # si esto cambia, la conversión ya no aplica

    d = adapters.district(crudo, RATE)
    assert d["tarifa_usd"] == pytest.approx(crudo["tarifa_envio_distrito"], abs=0.01)
    assert d["tarifa_sol"] == pytest.approx(d["tarifa_usd"] * RATE, abs=0.01)


# ── Productos: las tres formas de la API ──────────────────────────────

@pytest.mark.parametrize(
    "fixture", ["productos_destacados", "productos_buscar", "categoria_productos"]
)
def test_los_listados_reales_dan_nombre_y_precio(fixture):
    payload = adapters.products_payload(load(fixture), RATE)
    productos = payload["data"]

    assert productos, f"{fixture} no devolvió productos"
    for p in productos:
        assert p["id_producto"]
        assert p["nombre"], "sin nombre: la API usa `nombre_producto`"
        assert p["precio_usd"] is not None
        assert p["precio_sol"] == pytest.approx(p["precio_usd"] * RATE, abs=0.01)


def test_la_categoria_anida_los_productos_y_aun_asi_se_extraen():
    """`/categorias/{slug}/productos` devuelve {"categoria": …, "productos": […]}."""
    crudo = load("categoria_productos")
    assert "productos" in crudo["data"], "la API dejó de anidar; revisar adapter"

    payload = adapters.products_payload(crudo, RATE)
    assert isinstance(payload["data"], list) and payload["data"]


def test_el_detalle_usa_otra_forma_mas_y_tambien_se_normaliza():
    """El detalle trae `nombre`/`precio` e `imagenes[]`, no `imagen_url`."""
    payload = adapters.products_payload(load("producto_detalle"), RATE)
    p = payload["data"]

    assert p["nombre"] == "Peluche Oso Loquito de Amor"
    assert p["precio_usd"] == 19
    assert p["precio_sol"] == pytest.approx(19 * RATE, abs=0.01)
    assert p["imagen_url"].startswith("http"), "debe salir de imagenes[]"
    assert p["descripcion"], "el detalle conserva sus campos extra"


def test_el_slug_de_categoria_sale_de_url_categoria():
    """La API usa `url_categoria` (API.md nota #4), no `categoria_url`.

    El servidor corrigió la contradicción y renombró el campo. El adapter leía el
    nombre viejo, así que dejó de captar el slug y `enforce_category` no podía
    filtrar los resultados de las listas de la API (solo los de Qdrant).
    """
    raw = {
        "id_producto": 1,
        "nombre_producto": "Desayuno de Amor",
        "precio_producto": 30,
        "url_categoria": "desayunos-de-amor",
        "nombre_categoria": "Desayunos de Amor",
    }
    p = adapters.product(raw, RATE)
    assert p["categoria_slug"] == "desayunos-de-amor"
    assert p["categoria"] == "Desayunos de Amor"


def test_una_oferta_conserva_el_precio_de_lista():
    raw = {
        "id_producto": 9,
        "nombre_producto": "Cesta",
        "precio_producto": 40,
        "precio_final": 30,
        "tiene_oferta": True,
    }
    p = adapters.product(raw, RATE)
    assert p["precio_usd"] == 30, "el precio que paga el cliente es el final"
    assert p["precio_lista_usd"] == 40


# ── El puente con el harness ──────────────────────────────────────────

def test_los_artifacts_del_harness_ya_traen_nombre_y_precio():
    """Antes: `Product(id=1235, nombre='', usd=None)` para todo lo de la API."""
    payload = adapters.products_payload(load("productos_destacados"), RATE)
    productos = extract_products(payload)

    assert productos
    for p in productos:
        assert p.nombre
        assert p.precio_sol is not None and p.precio_usd is not None


def test_el_tipo_de_cambio_tiene_un_valor_de_respaldo():
    """Sin tipo de cambio, un precio aproximado es mejor que ningún precio."""
    assert adapters.DEFAULT_USD_PEN > 0


# ── HTML del CMS ──────────────────────────────────────────────────────

def test_las_descripciones_no_llegan_con_html_al_cliente():
    """Deuda #1: `descripcion` traía `<br>` y tabs, y nadie los limpiaba.

    El cliente leía literalmente "…el alma del Perú.<br>". Se limpia en el
    adapter, que es la frontera donde ya se normaliza todo lo demás.
    """
    payload = adapters.products_payload(load("producto_detalle"), RATE)
    detalle = payload["data"]

    for campo in ("descripcion", "descripcion_corta", "nombre"):
        texto = detalle.get(campo) or ""
        assert "<br" not in texto and "<p" not in texto, f"{campo} conserva HTML"
        assert "\t" not in texto, f"{campo} conserva tabs"
        assert "&nbsp;" not in texto and "&amp;" not in texto


def test_la_descripcion_del_detalle_conserva_las_vinetas():
    """Es la única respuesta a "¿qué contiene?": los saltos SON la lista."""
    detalle = adapters.products_payload(load("producto_detalle"), RATE)["data"]
    desc = detalle["descripcion"]

    # El fixture es el peluche de 60cm: cada `<br>` separaba un item.
    assert "Osito de peluche" in desc
    assert "Tarjeta dedicatoria" in desc
    assert desc.count("\n") >= 2, "se perdieron los saltos que separan los items"
    assert "\n\n" not in desc, "quedaron saltos dobles del HTML"


def test_la_descripcion_corta_va_en_una_sola_linea():
    """Se imprime dentro de la viñeta del listado; un salto parte el listado."""
    payload = adapters.products_payload(load("productos_destacados"), RATE)

    for p in payload["data"]:
        assert "\n" not in (p.get("descripcion_corta") or "")
        assert "\n" not in (p.get("nombre") or "")


def test_los_metodos_de_pago_no_llegan_con_html():
    """Son los datos bancarios que el cliente copia para pagar."""
    payload = adapters.payment_methods_payload(load("metodos_pago"))

    for metodo in payload["data"]:
        desc = metodo.get("descripcion_metodo_pago") or ""
        assert "<p" not in desc and "<br" not in desc and "<strong" not in desc
        assert "&" not in desc or "&" in desc.replace("&amp;", "")
    # Y el dato que importa sigue ahí, no lo comió la limpieza.
    bcp = next(m for m in payload["data"] if m["nombre_metodo_pago"] == "BCP")
    assert "194 1987991 0 15" in bcp["descripcion_metodo_pago"]


def test_limpiar_html_no_rompe_un_texto_ya_limpio():
    assert adapters.clean_html("Desayuno criollo para dos") == "Desayuno criollo para dos"
    assert adapters.clean_html("") == ""
    assert adapters.clean_html(None) == ""
