"""Cola acotada para desacoplar el webhook del procesamiento del agente.

El webhook solo valida, parsea y acepta trabajos. Los workers procesan después,
fuera del ciclo HTTP. La cola es intencionalmente local al proceso: mejora el
control de carga y el apagado ordenado sin introducir otra infraestructura. Para
varias réplicas o durabilidad entre reinicios, la misma interfaz puede respaldarse
con Redis/RabbitMQ más adelante.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from app.channels.whatsapp.parser import InboundMessage
from app.config import settings
from app.observability import (
    audit_event,
    new_trace_id,
    record_operation,
    trace_context,
)
from app.services.buffer import enqueue_inbound

log = logging.getLogger(__name__)

SubmissionStatus = Literal["accepted", "duplicate", "full", "unavailable"]
Handler = Callable[[InboundMessage], Awaitable[dict]]


@dataclass(frozen=True)
class QueueSubmission:
    status: SubmissionStatus

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class InboundJob:
    message: InboundMessage
    trace_id: str
    accepted_at: float = field(default_factory=time.monotonic)


class InboundQueue:
    """Cola FIFO con deduplicación mientras el trabajo está pendiente."""

    def __init__(
        self,
        *,
        handler: Handler,
        maxsize: int = 500,
        workers: int = 1,
    ) -> None:
        self._handler = handler
        self._queue: asyncio.Queue[InboundJob] = asyncio.Queue(
            maxsize=max(1, maxsize)
        )
        self._worker_count = max(1, workers)
        self._workers: list[asyncio.Task[None]] = []
        self._pending_ids: set[str] = set()
        self._running = False
        self._accepted = 0
        self._processed = 0
        self._failed = 0
        self._duplicates = 0
        self._rejected = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(
                self._run_worker(index),
                name=f"inbound-worker-{index + 1}",
            )
            for index in range(self._worker_count)
        ]

    def submit(
        self,
        msg: InboundMessage,
        *,
        trace_id: str | None = None,
    ) -> QueueSubmission:
        if not self._running:
            self._rejected += 1
            record_operation("inbound.submit", "unavailable")
            return QueueSubmission("unavailable")

        message_id = (msg.wa_message_id or "").strip()
        if message_id and message_id in self._pending_ids:
            self._duplicates += 1
            record_operation("inbound.submit", "duplicate")
            return QueueSubmission("duplicate")

        try:
            self._queue.put_nowait(
                InboundJob(
                    message=msg,
                    trace_id=trace_id or new_trace_id(message_id or None),
                )
            )
        except asyncio.QueueFull:
            self._rejected += 1
            record_operation("inbound.submit", "full")
            return QueueSubmission("full")

        if message_id:
            self._pending_ids.add(message_id)
        self._accepted += 1
        record_operation("inbound.submit", "accepted")
        return QueueSubmission("accepted")

    async def join(self) -> None:
        await self._queue.join()

    async def stop(self, *, timeout: float = 20.0) -> None:
        if not self._running:
            return
        self._running = False
        try:
            await asyncio.wait_for(self._queue.join(), timeout=max(0.01, timeout))
        except TimeoutError:
            log.warning(
                "[INBOUND-QUEUE] apagado agotó %.1fs; se cancelan %s workers",
                timeout,
                len(self._workers),
            )
        finally:
            for worker in self._workers:
                worker.cancel()
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()

    def stats(self) -> dict[str, int | bool]:
        return {
            "running": self._running,
            "depth": self._queue.qsize(),
            "maxsize": self._queue.maxsize,
            "workers": len(self._workers),
            "pending": len(self._pending_ids),
            "accepted": self._accepted,
            "processed": self._processed,
            "failed": self._failed,
            "duplicates": self._duplicates,
            "rejected": self._rejected,
        }

    async def _run_worker(self, index: int) -> None:
        while True:
            job = await self._queue.get()
            msg = job.message
            message_id = (msg.wa_message_id or "").strip()
            with trace_context(job.trace_id):
                queue_wait_ms = (time.monotonic() - job.accepted_at) * 1000
                record_operation(
                    "inbound.queue_wait",
                    "ok",
                    duration_ms=queue_wait_ms,
                )
                started = time.monotonic()
                try:
                    log.info(
                        "[INBOUND-WORKER] worker=%s type=%s",
                        index + 1,
                        msg.message_type,
                    )
                    await self._handler(msg)
                    self._processed += 1
                    duration_ms = (time.monotonic() - started) * 1000
                    record_operation(
                        "inbound.worker",
                        "ok",
                        duration_ms=duration_ms,
                    )
                    audit_event(
                        "inbound.worker",
                        "ok",
                        worker=index + 1,
                        message_type=msg.message_type,
                        latency_ms=duration_ms,
                    )
                except asyncio.CancelledError:
                    duration_ms = (time.monotonic() - started) * 1000
                    record_operation(
                        "inbound.worker",
                        "cancelled",
                        duration_ms=duration_ms,
                    )
                    audit_event(
                        "inbound.worker",
                        "cancelled",
                        worker=index + 1,
                        message_type=msg.message_type,
                        latency_ms=duration_ms,
                    )
                    raise
                except Exception as error:
                    self._failed += 1
                    duration_ms = (time.monotonic() - started) * 1000
                    record_operation(
                        "inbound.worker",
                        "error",
                        duration_ms=duration_ms,
                    )
                    audit_event(
                        "inbound.worker",
                        "error",
                        worker=index + 1,
                        message_type=msg.message_type,
                        latency_ms=duration_ms,
                        error_type=type(error).__name__,
                    )
                    log.exception("[INBOUND-WORKER] error procesando mensaje")
                finally:
                    if message_id:
                        self._pending_ids.discard(message_id)
                    self._queue.task_done()


_inbound_queue: InboundQueue | None = None


async def start_inbound_queue() -> None:
    global _inbound_queue
    if _inbound_queue is not None:
        return
    queue = InboundQueue(
        handler=enqueue_inbound,
        maxsize=settings.inbound_queue_maxsize,
        workers=settings.inbound_queue_workers,
    )
    await queue.start()
    _inbound_queue = queue
    log.info(
        "[INBOUND-QUEUE] lista workers=%s maxsize=%s",
        settings.inbound_queue_workers,
        settings.inbound_queue_maxsize,
    )


async def stop_inbound_queue() -> None:
    global _inbound_queue
    queue = _inbound_queue
    _inbound_queue = None
    if queue is not None:
        await queue.stop(timeout=settings.inbound_queue_shutdown_seconds)


def submit_inbound(
    msg: InboundMessage,
    *,
    trace_id: str | None = None,
) -> QueueSubmission:
    if _inbound_queue is None:
        record_operation("inbound.submit", "unavailable")
        return QueueSubmission("unavailable")
    return _inbound_queue.submit(msg, trace_id=trace_id)


def inbound_queue_stats() -> dict[str, int | bool]:
    if _inbound_queue is None:
        return {
            "running": False,
            "depth": 0,
            "maxsize": settings.inbound_queue_maxsize,
            "workers": 0,
            "pending": 0,
            "accepted": 0,
            "processed": 0,
            "failed": 0,
            "duplicates": 0,
            "rejected": 0,
        }
    return _inbound_queue.stats()
