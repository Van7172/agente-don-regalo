"""Cola inbound durable basada en Redis Streams.

Redis conserva los trabajos entre reinicios. Los consumer groups reparten la
carga entre réplicas y ``XAUTOCLAIM`` recupera entregas abandonadas. Cada
contacto se procesa bajo un lease renovable para serializar su conversación.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, AsyncIterator

from app.channels.whatsapp.parser import InboundMessage
from app.observability import audit_event, record_operation, trace_context

log = logging.getLogger(__name__)

_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""

_RENEW_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_COMPLETE_ENTRY_SCRIPT = """
local acked = redis.call('xack', KEYS[1], ARGV[1], ARGV[2])
redis.call('xdel', KEYS[1], ARGV[2])
redis.call('hdel', KEYS[2], ARGV[2])
return acked
"""


class ConversationLockTimeout(RuntimeError):
    """La conversación sigue siendo procesada por otra réplica."""


class RedisInboundQueue:
    backend = "redis_streams"

    def __init__(
        self,
        *,
        client: Any,
        handler: Any,
        stream: str,
        group: str,
        dlq_stream: str,
        maxsize: int,
        workers: int,
        block_ms: int,
        claim_idle_ms: int,
        reclaim_seconds: float,
        dedupe_ttl_seconds: int,
        lock_ttl_seconds: float,
        lock_wait_seconds: float,
        max_retries: int,
        retry_base_seconds: float,
        consumer: str | None = None,
    ) -> None:
        self._redis = client
        self._handler = handler
        self._stream = stream
        self._group = group
        self._dlq_stream = dlq_stream
        self._maxsize = max(1, maxsize)
        self._worker_count = max(1, workers)
        self._block_ms = max(100, block_ms)
        self._claim_idle_ms = max(1_000, claim_idle_ms)
        self._reclaim_seconds = max(0.1, reclaim_seconds)
        self._dedupe_ttl = max(60, dedupe_ttl_seconds)
        self._lock_ttl_ms = max(1_000, int(lock_ttl_seconds * 1_000))
        self._lock_wait = max(0.1, lock_wait_seconds)
        self._max_retries = max(1, max_retries)
        self._retry_base = max(0.0, retry_base_seconds)
        instance = consumer or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        self._consumer = instance
        prefix = stream.rsplit(":", 1)[0] if ":" in stream else stream
        self._dedupe_prefix = f"{prefix}:dedupe:"
        self._lock_prefix = f"{prefix}:lock:"
        self._retry_key = f"{prefix}:retries"
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._active = 0
        self._accepted = 0
        self._processed = 0
        self._failed = 0
        self._duplicates = 0
        self._rejected = 0
        self._recovered = 0
        self._dead_lettered = 0

    async def start(self) -> None:
        if self._running:
            return
        await self._redis.ping()
        try:
            await self._redis.xgroup_create(
                self._stream,
                self._group,
                id="0",
                mkstream=True,
            )
        except Exception as error:
            if "BUSYGROUP" not in str(error):
                raise
        self._running = True
        self._tasks = [
            asyncio.create_task(
                self._run_worker(index),
                name=f"redis-inbound-worker-{index + 1}",
            )
            for index in range(self._worker_count)
        ]
        self._tasks.append(
            asyncio.create_task(
                self._run_reclaimer(),
                name="redis-inbound-reclaimer",
            )
        )

    async def submit(
        self,
        msg: InboundMessage,
        *,
        trace_id: str,
    ) -> Any:
        # Importación tardía para evitar un ciclo con el módulo fachada.
        from app.services.inbound_queue import QueueSubmission

        if not self._running:
            self._rejected += 1
            record_operation("inbound.submit", "unavailable")
            return QueueSubmission("unavailable")
        try:
            if int(await self._redis.xlen(self._stream)) >= self._maxsize:
                self._rejected += 1
                record_operation("inbound.submit", "full")
                return QueueSubmission("full")

            message_id = (msg.wa_message_id or "").strip()
            dedupe_key = self._dedupe_key(message_id) if message_id else ""
            if dedupe_key:
                inserted = await self._redis.set(
                    dedupe_key,
                    "1",
                    ex=self._dedupe_ttl,
                    nx=True,
                )
                if not inserted:
                    self._duplicates += 1
                    record_operation("inbound.submit", "duplicate")
                    return QueueSubmission("duplicate")

            payload = asdict(msg)
            # `raw` repite el webhook completo y puede contener metadatos que el
            # worker Redis no necesita. Referral y citas ya tienen campos propios.
            payload["raw"] = {}
            fields = {
                "payload": json.dumps(payload, ensure_ascii=False),
                "trace_id": trace_id,
                "accepted_at": str(time.time()),
            }
            try:
                await self._redis.xadd(self._stream, fields)
            except Exception:
                if dedupe_key:
                    await self._redis.delete(dedupe_key)
                raise
        except Exception:
            self._rejected += 1
            record_operation("inbound.submit", "unavailable")
            log.exception("[REDIS-QUEUE] no se pudo aceptar el mensaje")
            return QueueSubmission("unavailable")

        self._accepted += 1
        record_operation("inbound.submit", "accepted")
        return QueueSubmission("accepted")

    async def join(self, *, timeout: float = 30.0) -> None:
        deadline = asyncio.get_running_loop().time() + max(0.1, timeout)
        while asyncio.get_running_loop().time() < deadline:
            if int(await self._redis.xlen(self._stream)) == 0 and self._active == 0:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("Redis Stream no se vació antes del timeout")

    async def stop(self, *, timeout: float = 20.0) -> None:
        if not self._running:
            return
        self._running = False
        try:
            await self.join(timeout=timeout)
        except TimeoutError:
            log.warning("[REDIS-QUEUE] apagado con trabajos pendientes")
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            await self._redis.aclose()

    def stats(self) -> dict[str, int | bool | str]:
        return {
            "backend": self.backend,
            "running": self._running,
            "depth": max(0, self._accepted - self._processed - self._dead_lettered),
            "depth_scope": "instance_estimate",
            "maxsize": self._maxsize,
            "workers": max(0, len(self._tasks) - (1 if self._tasks else 0)),
            "pending": self._active,
            "accepted": self._accepted,
            "processed": self._processed,
            "failed": self._failed,
            "duplicates": self._duplicates,
            "rejected": self._rejected,
            "recovered": self._recovered,
            "dead_lettered": self._dead_lettered,
        }

    async def operational_stats(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            **self.stats(),
            "durable": True,
            "stream": self._stream,
            "consumer_group": self._group,
        }
        try:
            pending = await self._redis.xpending(self._stream, self._group)
            pending_count = _mapping_value(pending, "pending", 0)
            groups = await self._redis.xinfo_groups(self._stream)
            group_info = next(
                (
                    item
                    for item in groups
                    if _text(_mapping_value(item, "name", "")) == self._group
                ),
                {},
            )
            consumer_lag = int(_mapping_value(group_info, "lag", 0) or 0)
            in_flight = int(pending_count or 0)
            snapshot.update(
                {
                    "stream_length": int(await self._redis.xlen(self._stream)),
                    "global_pending": in_flight + consumer_lag,
                    "in_flight": in_flight,
                    "consumer_count": int(
                        _mapping_value(group_info, "consumers", 0) or 0
                    ),
                    "consumer_lag": consumer_lag,
                    "dead_letter": int(
                        await self._redis.xlen(self._dlq_stream)
                    ),
                    "telemetry_status": "ok",
                }
            )
        except Exception as error:
            snapshot.update(
                {
                    "telemetry_status": "error",
                    "telemetry_error": type(error).__name__,
                }
            )
            record_operation("inbound.redis_telemetry", "error")
        return snapshot

    async def _run_worker(self, index: int) -> None:
        while self._running:
            try:
                batches = await self._redis.xreadgroup(
                    self._group,
                    self._consumer,
                    {self._stream: ">"},
                    count=1,
                    block=self._block_ms,
                )
                for _stream, messages in batches or []:
                    for entry_id, fields in messages:
                        await self._process(entry_id, fields, index)
            except asyncio.CancelledError:
                raise
            except Exception:
                record_operation("inbound.redis_read", "error")
                log.exception("[REDIS-QUEUE] fallo leyendo consumer group")
                await asyncio.sleep(1)

    async def _run_reclaimer(self) -> None:
        cursor: Any = "0-0"
        while self._running:
            try:
                await asyncio.sleep(self._reclaim_seconds)
                claimed = await self._redis.xautoclaim(
                    self._stream,
                    self._group,
                    self._consumer,
                    min_idle_time=self._claim_idle_ms,
                    start_id=cursor,
                    count=10,
                )
                cursor, messages = claimed[0], claimed[1]
                for entry_id, fields in messages:
                    self._recovered += 1
                    record_operation("inbound.recovery", "claimed")
                    await self._process(entry_id, fields, self._worker_count)
                if not messages:
                    cursor = "0-0"
            except asyncio.CancelledError:
                raise
            except Exception:
                record_operation("inbound.recovery", "error")
                log.exception("[REDIS-QUEUE] fallo recuperando pendientes")

    async def _process(self, entry_id: Any, fields: dict[Any, Any], index: int) -> None:
        decoded = {_text(key): _text(value) for key, value in fields.items()}
        try:
            payload = json.loads(decoded["payload"])
            msg = InboundMessage(**payload)
        except Exception as error:
            await self._dead_letter(entry_id, decoded, error, attempts=0)
            return

        trace_id = decoded.get("trace_id") or "-"
        accepted_at = float(decoded.get("accepted_at") or time.time())
        self._active += 1
        try:
            with trace_context(trace_id):
                record_operation(
                    "inbound.queue_wait",
                    "ok",
                    duration_ms=max(0.0, (time.time() - accepted_at) * 1_000),
                )
                async with self._conversation_lock(msg.wa_id):
                    await self._process_with_retries(entry_id, decoded, msg, index)
        except ConversationLockTimeout:
            record_operation("inbound.conversation_lock", "busy")
            # Permanece en PEL; el reclaimer volverá a intentarlo.
        finally:
            self._active = max(0, self._active - 1)

    async def _process_with_retries(
        self,
        entry_id: Any,
        fields: dict[str, str],
        msg: InboundMessage,
        index: int,
    ) -> None:
        attempts = int(await self._redis.hget(self._retry_key, entry_id) or 0)
        while attempts < self._max_retries:
            started = time.monotonic()
            try:
                await self._handler(msg)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                attempts = int(await self._redis.hincrby(self._retry_key, entry_id, 1))
                self._failed += 1
                record_operation("inbound.worker", "retry")
                audit_event(
                    "inbound.worker",
                    "retry",
                    worker=index + 1,
                    message_type=msg.message_type,
                    error_type=type(error).__name__,
                )
                if attempts >= self._max_retries:
                    await self._dead_letter(entry_id, fields, error, attempts)
                    return
                await asyncio.sleep(self._retry_base * (2 ** (attempts - 1)))
                continue

            await self._complete_entry(entry_id)
            self._processed += 1
            duration_ms = (time.monotonic() - started) * 1_000
            record_operation("inbound.worker", "ok", duration_ms=duration_ms)
            audit_event(
                "inbound.worker",
                "ok",
                worker=index + 1,
                message_type=msg.message_type,
                latency_ms=duration_ms,
            )
            return

    async def _dead_letter(
        self,
        entry_id: Any,
        fields: dict[str, str],
        error: Exception,
        attempts: int,
    ) -> None:
        await self._redis.xadd(
            self._dlq_stream,
            {
                **fields,
                "source_entry_id": _text(entry_id),
                "attempts": str(attempts),
                "error_type": type(error).__name__,
                "failed_at": str(time.time()),
            },
        )
        await self._complete_entry(entry_id)
        self._dead_lettered += 1
        record_operation("inbound.worker", "dead_letter")
        audit_event(
            "inbound.worker",
            "dead_letter",
            error_type=type(error).__name__,
        )

    @asynccontextmanager
    async def _conversation_lock(self, wa_id: str) -> AsyncIterator[None]:
        identity = wa_id or "unknown"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        key = f"{self._lock_prefix}{digest}"
        token = uuid.uuid4().hex
        deadline = asyncio.get_running_loop().time() + self._lock_wait
        while self._running:
            acquired = await self._redis.set(
                key,
                token,
                nx=True,
                px=self._lock_ttl_ms,
            )
            if acquired:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise ConversationLockTimeout(identity)
            await asyncio.sleep(0.05)
        else:
            raise ConversationLockTimeout(identity)

        renewer = asyncio.create_task(self._renew_lock(key, token))
        try:
            yield
        finally:
            renewer.cancel()
            await asyncio.gather(renewer, return_exceptions=True)
            await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)

    async def _renew_lock(self, key: str, token: str) -> None:
        interval = max(0.25, self._lock_ttl_ms / 3_000)
        while True:
            await asyncio.sleep(interval)
            renewed = await self._redis.eval(
                _RENEW_LOCK_SCRIPT,
                1,
                key,
                token,
                self._lock_ttl_ms,
            )
            if not renewed:
                log.error("[REDIS-QUEUE] se perdió el lease de conversación")
                return

    def _dedupe_key(self, message_id: str) -> str:
        digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
        return f"{self._dedupe_prefix}{digest}"

    async def _complete_entry(self, entry_id: Any) -> None:
        """ACK, borrado del stream y contador de reintentos en una transacción."""
        await self._redis.eval(
            _COMPLETE_ENTRY_SCRIPT,
            2,
            self._stream,
            self._retry_key,
            self._group,
            entry_id,
        )


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _mapping_value(mapping: Any, key: str, default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    if key in mapping:
        return mapping[key]
    encoded = key.encode()
    return mapping.get(encoded, default)
