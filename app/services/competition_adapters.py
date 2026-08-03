"""Adaptadores de catálogo público por competidor.

Solo leen lo que el sitio publica. Magia y Sorprende Lima son Shopify
(`/products.json`). Rosatel es VTEX: se usa el host comercial público de
búsqueda, no `www.rosatel.pe/api` (robots lo prohíbe).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "DonRegaloBot/1.0 (+https://donregalo.pe; catalog-research)"
DEFAULT_DELAY_S = 1.0


@dataclass(frozen=True)
class ScrapedProduct:
    clave_externa: str
    nombre: str
    url: str
    precio_sol: Optional[float] = None
    precio_tachado_sol: Optional[float] = None


FetchFn = Callable[[str], Awaitable[httpx.Response]]


async def allowed_by_robots(fetch: FetchFn, base: str, path: str) -> bool:
    """True si robots.txt permite el path. Ante duda (robots caído), no crawl."""
    robots_url = urljoin(base.rstrip("/") + "/", "robots.txt")
    try:
        resp = await fetch(robots_url)
        if resp.status_code >= 400:
            log.warning("[competencia] robots %s → HTTP %s; se omite", robots_url, resp.status_code)
            return False
        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        return bool(parser.can_fetch(USER_AGENT, urljoin(base.rstrip("/") + "/", path.lstrip("/"))))
    except Exception as err:
        log.warning("[competencia] no se pudo leer robots %s: %s", robots_url, err)
        return False


def _money(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        # Shopify manda precios como string "135.00"; a veces en centavos int.
        amount = float(value)
    except (TypeError, ValueError):
        text = re.sub(r"[^\d.,]", "", str(value))
        text = text.replace(",", ".") if text.count(",") == 1 and "." not in text else text.replace(",", "")
        try:
            amount = float(text)
        except ValueError:
            return None
    if amount < 0 or amount > 1_000_000:
        return None
    return round(amount, 2)


async def crawl_shopify(
    fetch: FetchFn,
    *,
    base: str,
    max_products: int,
) -> list[ScrapedProduct]:
    """Paginación de /products.json. Respeta robots del host."""
    if not await allowed_by_robots(fetch, base, "/products.json"):
        log.warning("[competencia] robots bloquea products.json en %s", base)
        return []

    out: list[ScrapedProduct] = []
    page = 1
    while len(out) < max_products:
        url = f"{base.rstrip('/')}/products.json?limit=50&page={page}"
        resp = await fetch(url)
        if resp.status_code >= 400:
            log.warning("[competencia] Shopify %s → HTTP %s", url, resp.status_code)
            break
        try:
            payload = resp.json()
        except Exception:
            log.warning("[competencia] Shopify %s no devolvió JSON", url)
            break
        products = payload.get("products") or []
        if not products:
            break
        for raw in products:
            item = _shopify_product(base, raw)
            if item is None:
                continue
            out.append(item)
            if len(out) >= max_products:
                break
        page += 1
        if len(products) < 50:
            break
    return out


def _shopify_product(base: str, raw: dict) -> Optional[ScrapedProduct]:
    handle = str(raw.get("handle") or "").strip()
    title = str(raw.get("title") or "").strip()
    pid = raw.get("id")
    if not handle or not title or pid is None:
        return None
    variants = raw.get("variants") or []
    price = None
    compare = None
    if variants:
        price = _money(variants[0].get("price"))
        compare = _money(variants[0].get("compare_at_price"))
        if compare is not None and price is not None and compare <= price:
            compare = None
    return ScrapedProduct(
        clave_externa=str(pid),
        nombre=title[:255],
        url=f"{base.rstrip('/')}/products/{handle}",
        precio_sol=price,
        precio_tachado_sol=compare,
    )


async def crawl_rosatel_vtex(
    fetch: FetchFn,
    *,
    max_products: int,
    store_host: str = "https://rosatelpe.vtexcommercestable.com.br",
    storefront: str = "https://www.rosatel.pe",
) -> list[ScrapedProduct]:
    """Catálogo VTEX vía host comercial público (no www.rosatel.pe/api)."""
    path = "/api/catalog_system/pub/products/search"
    # www.rosatel.pe/robots Disallow /api — por eso NO se llama a ese host.
    # El host comercial a veces no publica robots; 404/error → se permite el
    # path /pub/ (API pública de catálogo). Solo se aborta si niega explícito.
    robots_url = urljoin(store_host.rstrip("/") + "/", "robots.txt")
    try:
        robots_resp = await fetch(robots_url)
        if robots_resp.status_code < 400:
            parser = RobotFileParser()
            parser.parse(robots_resp.text.splitlines())
            if not parser.can_fetch(USER_AGENT, urljoin(store_host.rstrip("/") + "/", path.lstrip("/"))):
                log.warning("[competencia] robots VTEX bloquea %s", path)
                return []
    except Exception as err:
        log.info("[competencia] robots VTEX no legible (%s); se usa /pub/", err)

    out: list[ScrapedProduct] = []
    step = 49  # VTEX: _from/_to inclusivos, máx ~50
    start = 0
    while len(out) < max_products:
        end = start + step
        url = f"{store_host.rstrip('/')}{path}?_from={start}&_to={end}"
        resp = await fetch(url)
        if resp.status_code >= 400 and resp.status_code != 206:
            log.warning("[competencia] VTEX %s → HTTP %s", url, resp.status_code)
            break
        try:
            items = resp.json()
        except Exception:
            log.warning("[competencia] VTEX %s no devolvió JSON", url)
            break
        if not isinstance(items, list) or not items:
            break
        for raw in items:
            item = _vtex_product(storefront, raw)
            if item is None:
                continue
            out.append(item)
            if len(out) >= max_products:
                break
        if len(items) < step + 1:
            break
        start = end + 1
    return out


def _vtex_product(storefront: str, raw: dict) -> Optional[ScrapedProduct]:
    pid = raw.get("productId")
    name = str(raw.get("productName") or raw.get("productTitle") or "").strip()
    link = str(raw.get("linkText") or "").strip()
    if pid is None or not name or not link:
        return None
    price = None
    compare = None
    for item in raw.get("items") or []:
        for seller in item.get("sellers") or []:
            offer = seller.get("commertialOffer") or seller.get("commercialOffer") or {}
            price = _money(offer.get("Price"))
            compare = _money(offer.get("ListPrice"))
            if price is not None:
                break
        if price is not None:
            break
    if compare is not None and price is not None and compare <= price:
        compare = None
    return ScrapedProduct(
        clave_externa=str(pid),
        nombre=name[:255],
        url=f"{storefront.rstrip('/')}/{link}/p",
        precio_sol=price,
        precio_tachado_sol=compare,
    )


ADAPTERS: dict[str, Callable[..., Awaitable[list[ScrapedProduct]]]] = {
    "magia": lambda fetch, max_products: crawl_shopify(
        fetch, base="https://magia.pe", max_products=max_products
    ),
    "sorprendelima": lambda fetch, max_products: crawl_shopify(
        fetch, base="https://www.sorprendelima.pe", max_products=max_products
    ),
    "rosatel": lambda fetch, max_products: crawl_rosatel_vtex(
        fetch, max_products=max_products
    ),
}
