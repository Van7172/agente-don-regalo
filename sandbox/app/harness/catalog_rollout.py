"""Selección estable, métricas y desactivación inmediata del catálogo nuevo."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections import Counter
from pathlib import Path

from app.config import settings
from app.observability import metrics_snapshot

_lock = threading.Lock()
_cohorts: dict[str, Counter] = {}


def observe(strategy: str, *, violations: int, chat_calls: int, prompt_tokens: int,
            completion_tokens: int, cached_tokens: int, tool_calls: int) -> None:
    if strategy not in {"legacy", "legacy_fallback", "deterministic"}:
        return
    with _lock:
        totals = _cohorts.setdefault(strategy, Counter())
        totals.update(turns=1, guardrail_turns=int(violations > 0), chat_calls=chat_calls,
                      prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                      cached_tokens=cached_tokens, tool_calls=tool_calls)


def disable_path() -> Path:
    return Path(os.getenv("CATALOG_FLOW_DISABLE_FILE") or str(Path(tempfile.gettempdir()) / "donregalo-catalog-flow.disabled"))


def bucket(conversation_id: int) -> int:
    key = f"catalog-v1:{settings.default_tenant_slug}:{conversation_id}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100


def enabled() -> bool:
    return bool(settings.catalog_flow_enabled and settings.catalog_flow_percent > 0 and not disable_path().exists())


def selected(conversation_id: int | None) -> bool:
    return enabled() and conversation_id is not None and bucket(conversation_id) < settings.catalog_flow_percent


def disable() -> None:
    # El marcador sirve a todos los workers del mismo contenedor y sobrevive
    # a su reinicio de proceso. No contiene datos ni acepta una ruta del cliente.
    disable_path().touch(mode=0o600, exist_ok=True)


def status() -> dict:
    with _lock:
        cohorts = {key: dict(value) for key, value in _cohorts.items()}
    return {"enabled": enabled(), "configured_enabled": settings.catalog_flow_enabled,
            "percent": settings.catalog_flow_percent, "disabled_runtime": disable_path().exists(),
            "selection": "stable_conversation_sha256_v1",
            "cohorts": cohorts, "metrics_lifetime": "since_process_start",
            "operations": {k: v for k, v in metrics_snapshot()["operation_series"].items() if k.startswith("catalog.")}}
