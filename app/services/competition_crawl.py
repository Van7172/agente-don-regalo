"""Crawl periódico del catálogo de competidores + matching.

Best-effort: un sitio caído o un CRM lento no tumba el watchdog. El tick real
vive en `watchdog.check_competition`; aquí está la lógica.
"""
from __future__ import annotations

import asyncio
import logging
import time
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

# Rate limit y caídas pasajeras del sitio ajeno. 429 es el que importa: es lo
# que devuelve Shopify a la IP del contenedor.
_FETCH_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_FETCH_MAX_ATTEMPTS = 4
# Tope de espera POR reintento. El crawl vive dentro del tick del watchdog; un
# `Retry-After` de una hora no puede secuestrarlo.
_FETCH_BACKOFF_CAP = 20.0


def _retry_after(resp: Any, intento: int) -> float:
    """Cuánto esperar: lo que pida el servidor, si lo pide y es razonable.

    `Retry-After` manda sobre nuestro backoff — es el sitio diciendo a qué ritmo
    tolera que le pidamos, y respetarlo es la diferencia entre un crawler
    educado y uno al que acaban baneando. Pero se acota igual: llega como texto
    de un tercero y puede venir en horas.
    """
    cabecera = ""
    try:
        cabecera = (resp.headers or {}).get("retry-after", "") or ""
    except Exception:
        cabecera = ""
    try:
        pedido = float(str(cabecera).strip())
    except (TypeError, ValueError):
        pedido = 0.0
    if pedido > 0:
        return min(pedido, _FETCH_BACKOFF_CAP)
    return min(2.0 ** intento, _FETCH_BACKOFF_CAP)


async def maybe_run_crawl() -> Optional[dict[str, Any]]:
    """Corre un crawl si está habilitado y pasó el intervalo. None = skip."""
    if not settings.competition_crawl_enabled:
        return None
    if not crm_http.crm_enabled():
        return None

    if await _en_cooldown():
        return None

    summary = await run_crawl()
    # Solo enfriar si hubo avance. Un primer fallo (red, CRM 404, robots)
    # no puede castigar 12 horas de silencio.
    if _crawl_had_progress(summary):
        await _marcar_hecho()
    else:
        log.warning(
            "[competencia] crawl sin progreso; no se marca cooldown: %s", summary
        )
    return summary


async def run_crawl(
    *,
    max_products: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Crawl de todos los adaptadores conocidos + upsert al CRM.

    `force=True` ignora el flag de habilitación (para el trigger interno), pero
    sigue exigiendo CRM externo: sin CRM no hay dónde guardar.
    """
    if not force and not settings.competition_crawl_enabled:
        return {"skipped": True, "reason": "disabled"}
    if not crm_http.crm_enabled():
        return {"skipped": True, "reason": "crm_disabled"}

    limit = max_products if max_products is not None else settings.competition_max_products
    summary: dict[str, Any] = {"competidores": {}, "errores": [], "upserted_total": 0}
    crawl_started = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
        follow_redirects=True,
    ) as client:

        async def fetch(url: str) -> httpx.Response:
            """Una petición educada, con reintento ante rate limit.

            Shopify devolvía **429 en la PRIMERA página** de magia.pe y de
            sorprendelima, y el adapter se rendía ahí: dos de los tres
            competidores en cero. Desde una IP residencial los mismos endpoints
            dan 200, así que el límite es de la IP del contenedor, no de nuestro
            volumen — pedirlo una sola vez y abandonar era garantizar el cero.

            Un 429 no es un "no": es un "ahora no". La respuesta correcta —y la
            educada— es esperar lo que pida el servidor y volver, igual que ya
            hace `_chat_completion_unprotected` con OpenAI. Si tras los
            reintentos sigue negando, se devuelve el 429 y el adapter levanta
            `CrawlBlocked` con el motivo, que es lo que hay que leer.

            La espera va acotada a propósito: esto corre dentro del tick del
            watchdog, y un `Retry-After: 3600` no puede dejar colgado el resto.
            """
            espera = settings.competition_request_delay_seconds
            resp = None
            for intento in range(1, _FETCH_MAX_ATTEMPTS + 1):
                await asyncio.sleep(espera)
                resp = await client.get(url)
                if resp.status_code not in _FETCH_RETRY_STATUS:
                    return resp
                if intento == _FETCH_MAX_ATTEMPTS:
                    break
                espera = _retry_after(resp, intento)
                log.info(
                    "[competencia] %s → HTTP %s; reintento %s/%s en %.1fs",
                    url, resp.status_code, intento, _FETCH_MAX_ATTEMPTS, espera,
                )
            return resp  # type: ignore[return-value]

        for slug, adapter in ADAPTERS.items():
            try:
                products = await adapter(fetch, limit)
                upserted = await _upsert_batch(slug, products, crawl_started)
                summary["competidores"][slug] = {
                    "scraped": len(products),
                    "upserted": upserted,
                }
                summary["upserted_total"] += upserted
                # El competidor va en el NOMBRE, como `tool.{name}`:
                # `record_operation` no acepta etiquetas. Pasarlas como `tags=`
                # levantaba un TypeError — y como la misma llamada estaba dentro
                # del `except`, el manejador reventaba también y se llevaba por
                # delante todo el crawl. Un 500 en el trigger manual y un
                # `log.warning` invisible en el tick del watchdog.
                record_operation(f"competition.crawl.{slug}", "ok")
            except Exception as err:
                log.warning("[competencia] crawl %s falló: %s", slug, err)
                summary["errores"].append({"slug": slug, "error": str(err)[:200]})
                record_operation(f"competition.crawl.{slug}", "error")

    if force and _crawl_had_progress(summary):
        await _marcar_hecho()
    return summary


def _crawl_had_progress(summary: dict[str, Any]) -> bool:
    if int(summary.get("upserted_total") or 0) > 0:
        return True
    for info in (summary.get("competidores") or {}).values():
        if int(info.get("scraped") or 0) > 0 or int(info.get("upserted") or 0) > 0:
            return True
    return False


async def _upsert_batch(
    slug: str,
    products: list[ScrapedProduct],
    crawl_started: str,
) -> int:
    if not products:
        await crm_http.upsert_competition_products(
            slug,
            [],
            crawl_started=crawl_started,
            mark_missing_inactive=True,
        )
        return 0

    # 1) Persistir YA el catálogo (sin matching). Si el match tarda o falla,
    # Competencia deja de verse vacía — el hueco se calcula después.
    plain_rows = [
        {
            "clave_externa": p.clave_externa,
            "nombre": p.nombre,
            "url": p.url,
            "precio_sol": p.precio_sol,
            "precio_tachado_sol": p.precio_tachado_sol,
            "match_id_producto": None,
            "match_score": None,
            "match_nombre": None,
            "es_hueco": False,
        }
        for p in products
    ]
    total = await _push_rows(slug, plain_rows, crawl_started, mark_missing_inactive=True)

    # 2) Matching contra Qdrant y re-upsert con scores. Best-effort.
    matched_rows: list[dict[str, Any]] = []
    for product in products:
        try:
            match = await competition_match.match_product(product.nombre)
        except Exception as err:
            log.warning("[competencia] match %s/%s: %s", slug, product.clave_externa, err)
            continue
        matched_rows.append(
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
    if matched_rows:
        await _push_rows(
            slug, matched_rows, crawl_started, mark_missing_inactive=False
        )
    return total


async def _push_rows(
    slug: str,
    rows: list[dict[str, Any]],
    crawl_started: str,
    *,
    mark_missing_inactive: bool,
) -> int:
    batch_size = 40
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        last = mark_missing_inactive and (i + batch_size >= len(rows))
        n = await crm_http.upsert_competition_products(
            slug,
            chunk,
            crawl_started=crawl_started,
            mark_missing_inactive=last,
        )
        total += int(n or 0)
    return total


async def _en_cooldown() -> bool:
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
    try:
        await crm_http.put_setting(COOLDOWN_KEY, str(time.time()))
    except Exception as err:
        log.warning("[competencia] no pude marcar cooldown: %s", err)
