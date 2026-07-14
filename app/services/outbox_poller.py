"""Drena outbox pending del CRM aunque el push PHP→agente falle."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.config import settings
from app.crm import http_client as crm_http
from app.services.outbox_drain import drain_pending_outbox

log = logging.getLogger(__name__)

# Más frecuente que el watchdog: el asesor espera el mensaje ya.
OUTBOX_TICK_SEC = 12.0

_task: Optional[asyncio.Task] = None


async def _loop() -> None:
    log.info("[OUTBOX] drenaje iniciado (tick=%ss)", OUTBOX_TICK_SEC)
    while True:
        try:
            n = await drain_pending_outbox()
            if n:
                log.info("[OUTBOX] drenados=%s", n)
        except Exception as err:
            log.warning("[OUTBOX] tick error: %s", err)
        await asyncio.sleep(OUTBOX_TICK_SEC)


def start_outbox_drain() -> None:
    global _task
    if settings.crm_mode != "external" or not crm_http.crm_enabled():
        log.info("[OUTBOX] drenaje omitido (CRM no external)")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())


def stop_outbox_drain() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
