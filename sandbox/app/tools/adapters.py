"""Capa anticorrupción: normaliza lo que devuelven las APIs al modelo del dominio.

La API de donregalo.pe devuelve **tres formas distintas de producto**:

- listados (`/productos/destacados`, `/buscar`, `/categorias/{slug}/productos`):
  `nombre_producto`, `precio_producto`, `descripcion_corta_producto`…
- detalle (`/productos/{id}`): `nombre`, `precio`, `imagenes[]`…
- Qdrant: `nombre`, `precio`, `imagen_url`…

Y los distritos usan una cuarta (`nombre_distrito`, `tarifa_envio_distrito`).
Nadie adaptaba entre ellas: el LLM leía el JSON crudo y adivinaba los campos.
Cuando el código sí asumía una forma concreta, fallaba en silencio — `match_district`
buscaba `nombre` y la API devuelve `nombre_distrito`, así que **ningún distrito
llegó a hacer match jamás**.

Todo lo que sale de una tool pasa por aquí y llega al resto del sistema con la
misma forma, y con el precio ya convertido a soles. La conversión es aritmética,
no una tarea para un modelo de lenguaje.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Los textos del catálogo se editan en un WYSIWYG y llegan con HTML crudo:
# `<br>`, `<p>`, tabs y entidades. WhatsApp no renderiza nada de eso, así que al
# cliente le llegaba tal cual ("…el alma del Perú.<br>"). Se limpia aquí, en la
# misma frontera donde ya se normaliza el resto, y no en `render.py`: así también
# sale limpio el texto que el especialista `detail` lee para responder
# "¿qué contiene?".
_BLOCK_TAG = re.compile(
    r"<\s*/?\s*(?:br|p|div|li|ul|ol|tr|h[1-6])\b[^>]*>", re.IGNORECASE
)
_ANY_TAG = re.compile(r"<[^>]+>")


def clean_html(text: Any, *, inline: bool = False) -> str:
    """HTML del CMS → texto plano para WhatsApp.

    `inline=True` colapsa además los saltos de línea: `descripcion_corta` se
    imprime dentro de la viñeta de un producto (`render.render_product_list`) y
    un salto ahí parte el listado en dos.
    """
    if not text:
        return ""

    out = _BLOCK_TAG.sub("\n", str(text))
    out = _ANY_TAG.sub(" ", out)          # tags inline (<b>, <span>): no separan línea
    out = html.unescape(out)              # &amp;, &nbsp;, &oacute;…
    out = out.replace("\xa0", " ").replace("\t", " ").replace("\r", "\n")

    # Espacios sobrantes por línea, sin tocar los saltos que sí significan algo.
    lines = [re.sub(r"[^\S\n]+", " ", ln).strip() for ln in out.split("\n")]
    out = "\n".join(ln for ln in lines if ln)

    if inline:
        out = re.sub(r"\s*\n\s*", " ", out)
        out = re.sub(r" {2,}", " ", out)

    return out.strip()

# Si la API del tipo de cambio falla, es mejor un precio aproximado con un valor
# reciente que ningún precio. Se revisa en el log.
DEFAULT_USD_PEN = 3.40


async def usd_pen_rate(client: httpx.AsyncClient) -> float:
    """Tipo de cambio USD→PEN (cacheado 1 h en `catalog._cached_get`)."""
    from app.tools import catalog

    try:
        payload = await catalog.tipo_cambio(client, {})
        data = (payload or {}).get("data") or {}
        rate = float(data.get("tipo_cambio"))
        if rate > 0:
            return round(rate, 4)
    except Exception as err:
        log.warning("[adapters] tipo de cambio no disponible (%s); uso %.2f", err, DEFAULT_USD_PEN)
    return DEFAULT_USD_PEN


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_sol(usd: float | None, rate: float) -> float | None:
    if usd is None:
        return None
    return round(usd * rate, 2)


def _first_image(raw: dict[str, Any]) -> str:
    url = str(raw.get("imagen_url") or "").strip()
    if url:
        return url
    # El detalle no trae `imagen_url`: trae `imagenes[]` con varias resoluciones.
    for img in raw.get("imagenes") or []:
        if not isinstance(img, dict):
            continue
        for key in ("medium", "large", "original", "thumbnail", "url"):
            candidate = str(img.get(key) or "").strip()
            if candidate:
                return candidate
    return ""


def product(raw: dict[str, Any], rate: float) -> dict[str, Any] | None:
    """Cualquiera de las tres formas de producto → la forma canónica."""
    if not isinstance(raw, dict):
        return None

    pid = raw.get("id_producto")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None

    # `precio_final` ya contempla la oferta; los otros son el precio de lista.
    precio_usd = (
        _num(raw.get("precio_final"))
        if raw.get("precio_final") is not None
        else _num(raw.get("precio_producto")) or _num(raw.get("precio")) or _num(raw.get("precio_usd"))
    )
    lista_usd = _num(raw.get("precio_producto")) or _num(raw.get("precio"))

    categoria = raw.get("categoria")
    if isinstance(categoria, dict):  # forma del detalle: {"url": ..., "nombre": ...}
        categoria_nombre = str(categoria.get("nombre") or "")
        categoria_slug = str(categoria.get("url") or categoria.get("url_categoria") or "")
    else:
        categoria_nombre = str(raw.get("nombre_categoria") or categoria or "")
        # `url_categoria` es el campo canónico (API.md nota #4). `categoria_url` fue
        # la variante contradictoria que ya se corrigió en el servidor; se mantiene
        # como respaldo por si algún endpoint viejo la sigue devolviendo. Y
        # `categoria_slug` es el que trae Qdrant en su payload.
        categoria_slug = str(
            raw.get("url_categoria")
            or raw.get("categoria_url")
            or raw.get("categoria_slug")
            or ""
        )

    canonical: dict[str, Any] = {
        "id_producto": pid,
        "nombre": clean_html(
            raw.get("nombre_producto") or raw.get("nombre") or "", inline=True
        ),
        "descripcion_corta": clean_html(
            raw.get("descripcion_corta_producto") or raw.get("descripcion_corta") or "",
            inline=True,
        ),
        "precio_usd": precio_usd,
        "precio_sol": _to_sol(precio_usd, rate),
        "imagen_url": _first_image(raw),
        "url": str(raw.get("url_producto") or raw.get("url") or "").strip(),
        "categoria": categoria_nombre,
        "categoria_slug": categoria_slug,
    }

    if raw.get("tiene_oferta"):
        canonical["tiene_oferta"] = True
        canonical["precio_lista_usd"] = lista_usd
        canonical["precio_lista_sol"] = _to_sol(lista_usd, rate)

    stock = raw.get("stock_producto") if "stock_producto" in raw else raw.get("stock")
    if stock is not None:
        canonical["stock"] = stock

    if raw.get("score") is not None:  # viene de Qdrant
        canonical["score"] = raw["score"]

    return canonical


def district(raw: dict[str, Any], rate: float) -> dict[str, Any] | None:
    """`nombre_distrito` / `tarifa_envio_distrito` (USD) → forma canónica."""
    if not isinstance(raw, dict):
        return None

    nombre = str(
        raw.get("nombre_distrito") or raw.get("nombre") or raw.get("distrito") or ""
    ).strip()
    if not nombre:
        return None
    if nombre.isupper():  # la API los guarda en mayúsculas ("MIRAFLORES")
        nombre = nombre.title()

    tarifa_usd = _num(raw.get("tarifa_envio_distrito"))
    if tarifa_usd is None:
        tarifa_usd = _num(raw.get("tarifa_usd")) or _num(raw.get("precio_usd"))

    return {
        "id_distrito": raw.get("id_distrito"),
        "nombre": nombre,
        "tarifa_usd": tarifa_usd,
        "tarifa_sol": _to_sol(tarifa_usd, rate),
        "informacion": str(raw.get("informacion_distrito") or raw.get("informacion") or "").strip(),
    }


def _items(payload: Any) -> list[dict[str, Any]]:
    """Saca la lista de productos de un payload, venga como venga envuelta."""
    data = payload.get("data") if isinstance(payload, dict) else payload

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        # `/categorias/{slug}/productos` anida: {"categoria": {...}, "productos": [...]}
        for key in ("productos", "data", "items", "resultados"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [x for x in nested if isinstance(x, dict)]

    return []


def products_payload(payload: Any, rate: float, *, default_slug: str = "") -> Any:
    """Normaliza un payload de productos dejando siempre `data` como lista plana.

    `default_slug`: los productos de `/categorias/{slug}/productos` llegan SIN su
    categoría (está en el sobre, no en cada item). Sin estamparla, el filtro de
    categoría los descartaría a todos por "no poder demostrar" que pertenecen.
    """
    if not isinstance(payload, dict):
        return payload

    data = payload.get("data")

    # Detalle: `data` es un único producto.
    if isinstance(data, dict) and data.get("id_producto") and "productos" not in data:
        canonical = product(data, rate)
        if canonical is None:
            return payload
        # El detalle trae extras que el listado no: los conservamos.
        for extra in ("descripcion", "ocasiones", "imagenes", "relacionados", "tags"):
            if data.get(extra):
                canonical[extra] = data[extra]
        # `descripcion` es la lista de "¿qué contiene?" y es lo único que puede
        # responderla: se limpia conservando los saltos, que son las viñetas.
        if canonical.get("descripcion"):
            canonical["descripcion"] = clean_html(canonical["descripcion"])
        return {**payload, "data": canonical}

    items = _items(payload)
    if not items:
        return payload

    productos = [p for p in (product(raw, rate) for raw in items) if p]

    # El sobre de `/categorias/{slug}/productos` usa las claves de la API
    # (`url_categoria`, `nombre_categoria`), no `url`/`nombre`.
    sobre = data.get("categoria") if isinstance(data, dict) else None
    slug_sobre = ""
    nombre_sobre = ""
    if isinstance(sobre, dict):
        slug_sobre = str(sobre.get("url_categoria") or sobre.get("url") or "")
        nombre_sobre = str(sobre.get("nombre_categoria") or sobre.get("nombre") or "")

    slug = default_slug or slug_sobre
    if slug:
        for p in productos:
            if not p.get("categoria_slug"):
                p["categoria_slug"] = slug
            if not p.get("categoria") and nombre_sobre:
                p["categoria"] = nombre_sobre

    out = {**payload, "data": productos, "total": len(productos)}
    if isinstance(sobre, dict):
        out["categoria"] = sobre

    return out


def payment_methods_payload(payload: Any) -> Any:
    """Limpia el HTML de los métodos de pago (no lleva precios: no necesita `rate`).

    `descripcion_metodo_pago` llega con `<p>`, `<strong>` y `<br />` alrededor de
    los datos bancarios —número de cuenta, CCI, titular—. Es el texto que el
    cliente copia para pagar: si le llega con tags, lo copia mal. Se conservan
    los saltos de línea, que es lo que separa "Cuenta Corriente" de "CCI".
    """
    if not isinstance(payload, dict):
        return payload
    items = _items(payload)
    if not items:
        return payload

    metodos = []
    for raw in items:
        limpio = dict(raw)
        for campo in ("descripcion_metodo_pago", "descripcion", "nombre_metodo_pago"):
            if limpio.get(campo):
                limpio[campo] = clean_html(limpio[campo], inline=(campo == "nombre_metodo_pago"))
        metodos.append(limpio)

    return {**payload, "data": metodos}


def districts_payload(payload: Any, rate: float) -> Any:
    if not isinstance(payload, dict):
        return payload
    items = _items(payload)
    if not items:
        return payload
    distritos = [d for d in (district(raw, rate) for raw in items) if d]
    return {**payload, "data": distritos, "total": len(distritos)}
