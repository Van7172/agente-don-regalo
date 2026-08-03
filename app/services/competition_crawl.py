"""Crawl periódico del catálogo de competidores + matching.

Best-effort: un sitio caído o un CRM lento no tumba el watchdog. El tick real
vive en `watchdog.check_competition`; aquí está la lógica.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.config import settings
from app.crm import http_client as crm_http
from app.observability import record_operation
from app.services.competition_adapters import ADAPTERS, USER_AGENT, ScrapedProduct
from app.services import competition_match

log = logging.getLogger(__name__)

COOLDOWN_KEY = "competition_crawl_last"


async def maybe_run_crawl() -> Optional[dict[str, Any]]:
    """Corre un crawl si está habilitado y pasó el intervalo. None = skip."""
    if not settings.competition_crawl_enabled:
        return None
    if not crm_http.crm_enabled():
        return None

    if await _en_cooldown():
        return None

    summary = await run_crawl()
    await _marcar_hecho()
    return summary


async def run_crawl(*, max_products: int | None = None) -> dict[str, Any]:
    """Crawl de todos los adaptadores conocidos + upsert al CRM."""
    limit = max_products if max_products is not None else settings.competition_max_products
    summary: dict[str, Any] = {"competidores": {}, "errores": []}
    crawl_started = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
        follow_redirects=True,
    ) as client:

        async def fetch(url: str) -> httpx.Response:
            await asyncio.sleep(settings.competition_request_delay_seconds)
            return await client.get(url)

        for slug, adapter in ADAPTERS.items():
            try:
                products = await adapter(fetch, limit)
                upserted = await _upsert_batch(slug, products, crawl_started)
                summary["competidores"][slug] = {
                    "scraped": len(products),
                    "upserted": upserted,
                }
                record_operation("competition.crawl", "ok", tags={"slug": slug})
            except Exception as err:
                log.warning("[competencia] crawl %s falló: %s", slug, err)
                summary["errores"].append({"slug": slug, "error": str(err)[:200]})
                record_operation("competition.crawl", "error", tags={"slug": slug})

    return summary


async def _upsert_batch(
    slug: str,
    products: list[ScrapedProduct],
    crawl_started: str,
) -> int:
    if not products:
        # Aun así avisamos al CRM para que marque como inactivos los que ya no
        # aparecen — un catálogo vacío es una señal, no un no-op.
        await crm_http.upsert_competition_products(
            slug,
            [],
            crawl_started=crawl_started,
            mark_missing_inactive=True,
        )
        return 0

    rows: list[dict[str, Any]] = []
    for product in products:
        match = await competition_match.match_product(product.nombre)
        rows.append(
            {
                "clave_externa": product.clave_externa,
                "nombre": product.nombre,
                "url": product.url,
                "precio_sol": product.precio_sol,
                "precio_tachado_sol": product.precio_tachado_sol,
                "match_id_producto": match.match_id_producto,
                "match_score": match.match_score,
                "match_nombre": match.match_nombre,
                "es_hueco": bool(match.es_hueco),
            }
        )

    # Lotes pequeños: el hosting PHP suele limitar el body.
    batch_size = 40
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        last = i + batch_size >= len(rows)
        n = await crm_http.upsert_competition_products(
            slug,
            chunk,
            crawl_started=crawl_started,
            mark_missing_inactive=last,
        )
        total += int(n or 0)
    return total


async def _en_cooldown() -> bool:
    import time

    try:
        raw = await crm_http.get_setting(COOLDOWN_KEY)
    except Exception:
        return False
    if not raw:
        return False
    try:
        last = float(raw)
    except (TypeError, ValueError):
        return False
    return (time.time() - last) < settings.competition_crawl_interval_seconds


async def _marcar_hecho() -> None:
    import time

    try:
        await crm_http.put_setting(COOLDOWN_KEY, str(time.time()))
    except Exception as err:
        log.warning("[competencia] no pude marcar cooldown: %s", err)
