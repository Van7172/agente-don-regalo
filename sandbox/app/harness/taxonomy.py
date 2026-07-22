"""El menú de categorías, compuesto por el código a partir de la taxonomía real.

Por qué aquí y no en el prompt: el playbook YA obligaba a copiar los nombres
literalmente de `explorar_catalogo`, a no inventar un tercer nivel y a no pasar
de dos menús antes de mostrar productos. El modelo se saltó las tres cosas con
Yudith (22-07): al elegir "Plantas" le ofreció **siete** tipos de planta —existen
tres: Orquideas, Suculentas, Terrarios— y al elegir uno le escribió **seis
terrarios inventados**, con descripción y sin precio, ninguno del catálogo.
Terminó ofreciéndole un asesor para enseñarle fotos de un producto que no existe.
Cuatro menús, cero productos, veinte minutos y una derivación.

La lección es la misma que ya dejó el listado de productos (`render_product_list`)
y el formato de precios: **lo que tiene que ser exacto lo arma el código**. Los
nombres salen del payload y la numeración es nuestra, así que el número que
responde el cliente se resuelve sin volver a preguntarle — y por eso `master`
puede decidir en código cuándo dejar de preguntar y mostrar productos.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Dos menús como máximo (padres → hijas) y luego productos. El cliente que ya
# sabe lo que quiere no vuelve tras el tercer formulario.
MAX_MENU_DEPTH = 2


def _norm(s: str) -> str:
    s = (s or "").casefold()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def parse_navegacion(payload: Any) -> list[dict]:
    """Categorías padre con sus hijas reales, desde `GET /catalogo/navegacion`.

    Las `landings` son cruces SEO (categoría × filtro), no categorías: entran
    como hijas porque para el cliente son igual de válidas ("Desayunos de
    Cumpleaños") y se buscan con su propio slug.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []

    options: list[dict] = []
    for cat in data.get("categorias") or []:
        if not isinstance(cat, dict):
            continue
        nombre = str(cat.get("nombre") or "").strip()
        slug = str(cat.get("url_categoria") or "").strip("/")
        if not nombre or not slug:
            continue

        hijos: list[dict] = []
        for sub in cat.get("subcategorias") or []:
            if isinstance(sub, dict) and sub.get("nombre") and sub.get("url_categoria"):
                hijos.append({
                    "nombre": str(sub["nombre"]).strip(),
                    "slug": str(sub["url_categoria"]).strip("/"),
                    "tipo": "categoria",
                    "hijos": [],
                })
        for land in cat.get("landings") or []:
            if isinstance(land, dict) and land.get("nombre") and land.get("slug_landing"):
                hijos.append({
                    "nombre": str(land["nombre"]).strip(),
                    "slug": str(land["slug_landing"]).strip("/"),
                    "tipo": "landing",
                    "hijos": [],
                })

        options.append({
            "nombre": nombre, "slug": slug, "tipo": "categoria", "hijos": hijos
        })
    return options


def render_menu(options: list[dict], header: str) -> str:
    """El menú numerado. La numeración es la del código, no la del modelo.

    Que la escriba el mismo sitio que luego la resuelve es justo lo que permite
    entender el "7" del cliente sin preguntarle a qué se refería.
    """
    lineas = "\n".join(
        f"{i}) {o['nombre']}" for i, o in enumerate(options, start=1)
    )
    return f"{header}\n\n{lineas}"


_ORDINALS = {
    "primero": 1, "primera": 1, "uno": 1,
    "segundo": 2, "segunda": 2, "dos": 2,
    "tercero": 3, "tercera": 3, "tres": 3,
    "cuarto": 4, "cuarta": 4, "cuatro": 4,
    "quinto": 5, "quinta": 5, "cinco": 5,
    "sexto": 6, "sexta": 6, "seis": 6,
    "septimo": 7, "septima": 7, "siete": 7,
    "octavo": 8, "octava": 8, "ocho": 8,
}


def resolve_option(text: str, options: list[dict]) -> dict | None:
    """¿A qué opción del menú corresponde lo que escribió el cliente?

    Devuelve `None` ante la duda: seguir con el modelo es mejor que meter al
    cliente en una categoría que no pidió.
    """
    if not options:
        return None
    norm = _norm(text)
    if not norm:
        return None

    # 1. El número, que es como responde casi todo el mundo ("7", "la 3").
    numeros = re.findall(r"\d+", norm)
    if len(numeros) == 1:
        idx = int(numeros[0])
        if 1 <= idx <= len(options):
            return options[idx - 1]

    # 2. El ordinal escrito ("el segundo", "quiero la tercera").
    for palabra, idx in _ORDINALS.items():
        if re.search(rf"\b{palabra}\b", norm) and 1 <= idx <= len(options):
            return options[idx - 1]

    # 3. El nombre ("terrarios", "plantas"). Solo si es inequívoco: entre
    #    "Suculentas" y "Terrarios" un "quiero suculentas" es claro, pero si dos
    #    opciones casan preferimos no elegir por el cliente.
    hits = [o for o in options if (n := _norm(o["nombre"])) and n in norm]
    if len(hits) > 1:
        hits = sorted(hits, key=lambda o: len(o["nombre"]), reverse=True)[:1]
    if len(hits) == 1:
        return hits[0]
    return None


def as_state(options: list[dict]) -> list[dict]:
    """Lo mínimo que hay que recordar del menú para resolver la respuesta."""
    return [
        {"nombre": o["nombre"], "slug": o["slug"], "hijos": o.get("hijos") or []}
        for o in options
    ]
