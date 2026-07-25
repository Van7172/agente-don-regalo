"""Contratos de durabilidad, reintentos y exclusión por conversación."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest

from app.channels.whatsapp.parser import InboundMessage
from app.services.redis_inbound_queue import RedisInboundQueue


class FakeRedis:
    def __init__(self) -> None:
        self.stream: list[tuple[str, dict]] = []
        self.dlq: list[dict] = []
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, int]] = {}
        self.acked: list[str] = []
        self.claimed = False

    async def xlen(self, stream):
        return len(self.dlq) if str(stream).endswith(":dlq") else len(self.stream)

    async def set(self, key, value, *, nx=False, **_kwargs):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def xadd(self, stream, fields):
        if stream.endswith(":dlq"):
            self.dlq.append(dict(fields))
            return "dlq-1"
        entry_id = f"{len(self.stream) + 1}-0"
        self.stream.append((entry_id, dict(fields)))
        return entry_id

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(str(field))

    async def hincrby(self, key, field, amount):
        bucket = self.hashes.setdefault(key, {})
        bucket[str(field)] = bucket.get(str(field), 0) + amount
        return bucket[str(field)]

    async def hdel(self, key, field):
        return int(self.hashes.get(key, {}).pop(str(field), None) is not None)

    async def xack(self, _stream, _group, entry_id):
        self.acked.append(str(entry_id))
        return 1

    async def xdel(self, _stream, entry_id):
        before = len(self.stream)
        self.stream = [row for row in self.stream if row[0] != str(entry_id)]
        return before - len(self.stream)

    async def xautoclaim(self, *_args, **_kwargs):
        if self.claimed or not self.stream:
            return ["0-0", [], []]
        self.claimed = True
        return ["0-0", [self.stream[0]], []]

    async def xpending(self, _stream, _group):
        return {"pending": len(self.stream)}

    async def xinfo_groups(self, _stream):
        return [{"name": "agents", "consumers": 2, "lag": 4}]

    async def eval(self, script, _keys, key, token, *args):
        if "xack" in script:
            group, entry_id = args
            self.acked.append(str(entry_id))
            self.stream = [
                row for row in self.stream if row[0] != str(entry_id)
            ]
            self.hashes.get(token, {}).pop(str(entry_id), None)
            return 1
        if self.values.get(key) != token:
            return 0
        if "del" in script:
            self.values.pop(key, None)
        return 1


def _message(message_id: str = "wamid.1") -> InboundMessage:
    return InboundMessage(
        wa_id="51999999999",
        contact_name="Prueba",
        wa_message_id=message_id,
        message_type="text",
        text="Hola",
    )


def _queue(redis: FakeRedis, handler, *, retries: int = 3) -> RedisInboundQueue:
    queue = RedisInboundQueue(
        client=redis,
        handler=handler,
        stream="donregalo:inbound",
        group="agents",
        dlq_stream="donregalo:inbound:dlq",
        maxsize=20,
        workers=1,
        block_ms=100,
        claim_idle_ms=1000,
        reclaim_seconds=1,
        dedupe_ttl_seconds=3600,
        lock_ttl_seconds=30,
        lock_wait_seconds=1,
        max_retries=retries,
        retry_base_seconds=0,
        consumer="test-consumer",
    )
    queue._running = True
    return queue


@pytest.mark.asyncio
async def test_submit_es_durable_y_deduplica_entre_replicas():
    redis = FakeRedis()

    async def handler(_msg):
        return {}

    first = _queue(redis, handler)
    second = _queue(redis, handler)

    assert (await first.submit(_message(), trace_id="trace-1")).status == "accepted"
    assert (await second.submit(_message(), trace_id="trace-1")).status == "duplicate"
    assert len(redis.stream) == 1
    payload = json.loads(redis.stream[0][1]["payload"])
    assert payload["raw"] == {}


@pytest.mark.asyncio
async def test_ack_solo_ocurre_despues_de_completar_el_handler():
    redis = FakeRedis()
    release = asyncio.Event()

    async def handler(_msg):
        await release.wait()

    queue = _queue(redis, handler)
    msg = _message()
    fields = {
        "payload": json.dumps(asdict(msg)),
        "trace_id": "trace-1",
        "accepted_at": "1",
    }
    redis.stream.append(("1-0", fields))

    processing = asyncio.create_task(queue._process("1-0", fields, 0))
    await asyncio.sleep(0)
    assert redis.acked == []

    release.set()
    await processing
    assert redis.acked == ["1-0"]
    assert redis.stream == []


@pytest.mark.asyncio
async def test_reintenta_y_envia_a_dlq_al_agotar_intentos():
    redis = FakeRedis()

    async def handler(_msg):
        raise RuntimeError("fallo controlado")

    queue = _queue(redis, handler, retries=2)
    msg = _message()
    fields = {
        "payload": json.dumps(asdict(msg)),
        "trace_id": "trace-1",
        "accepted_at": "1",
    }
    redis.stream.append(("1-0", fields))

    await queue._process("1-0", fields, 0)

    assert redis.acked == ["1-0"]
    assert redis.stream == []
    assert redis.dlq[0]["attempts"] == "2"
    assert redis.dlq[0]["error_type"] == "RuntimeError"
    assert queue.stats()["dead_lettered"] == 1


@pytest.mark.asyncio
async def test_lock_serializa_la_misma_conversacion():
    redis = FakeRedis()

    async def handler(_msg):
        return {}

    queue = _queue(redis, handler)
    entered: list[int] = []
    first_inside = asyncio.Event()
    release = asyncio.Event()

    async def contender(number: int):
        async with queue._conversation_lock("51999999999"):
            entered.append(number)
            if number == 1:
                first_inside.set()
                await release.wait()

    first = asyncio.create_task(contender(1))
    await first_inside.wait()
    second = asyncio.create_task(contender(2))
    await asyncio.sleep(0.05)
    assert entered == [1]

    release.set()
    await asyncio.gather(first, second)
    assert entered == [1, 2]


@pytest.mark.asyncio
async def test_reclaimer_recupera_trabajo_abandonado():
    redis = FakeRedis()
    handled = asyncio.Event()

    async def handler(_msg):
        handled.set()

    queue = _queue(redis, handler)
    queue._reclaim_seconds = 0.01
    msg = _message("wamid.abandoned")
    fields = {
        "payload": json.dumps(asdict(msg)),
        "trace_id": "trace-recovered",
        "accepted_at": "1",
    }
    redis.stream.append(("1-0", fields))

    reclaimer = asyncio.create_task(queue._run_reclaimer())
    await asyncio.wait_for(handled.wait(), timeout=1)
    queue._running = False
    reclaimer.cancel()
    await asyncio.gather(reclaimer, return_exceptions=True)

    assert redis.acked == ["1-0"]
    assert queue.stats()["recovered"] == 1


@pytest.mark.asyncio
async def test_snapshot_operacional_consulta_estado_global_de_redis():
    redis = FakeRedis()

    async def handler(_msg):
        return {}

    queue = _queue(redis, handler)
    redis.stream.append(("1-0", {"payload": "{}"}))
    redis.dlq.extend([{}, {}])

    snapshot = await queue.operational_stats()

    assert snapshot["durable"] is True
    assert snapshot["global_pending"] == 5
    assert snapshot["in_flight"] == 1
    assert snapshot["consumer_count"] == 2
    assert snapshot["consumer_lag"] == 4
    assert snapshot["dead_letter"] == 2
    assert snapshot["telemetry_status"] == "ok"
