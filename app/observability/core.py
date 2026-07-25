"""Trazabilidad, métricas y auditoría estructurada sin datos personales.

No se guardan prompts, mensajes, direcciones, teléfonos, nombres, tokens ni
argumentos de herramientas. Los eventos describen decisiones y resultados
operacionales mediante campos de cardinalidad acotada.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

log = logging.getLogger("app.audit")

_TRACE_ID: ContextVar[str] = ContextVar("donregalo_trace_id", default="-")
_NAME_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
_SAFE_FIELDS = frozenset(
    {
        "agent",
        "backend",
        "circuit",
        "conversation_id",
        "data_category",
        "depth",
        "duplicate_count",
        "error_type",
        "from_state",
        "intent",
        "latency_ms",
        "message_type",
        "operation",
        "processed_count",
        "product_count",
        "queue_status",
        "rejected_count",
        "risk_level",
        "risk_score",
        "rule_count",
        "retry_after_seconds",
        "router",
        "status_code",
        "tool",
        "tool_count",
        "to_state",
        "violation_count",
        "worker",
    }
)


@dataclass
class _Metric:
    count: int = 0
    duration_ms_sum: float = 0.0
    duration_ms_max: float = 0.0


_lock = threading.Lock()
_metrics: dict[tuple[str, str], _Metric] = {}
_audit_counts: Counter[tuple[str, str]] = Counter()
_logging_installed = False


def current_trace_id() -> str:
    return _TRACE_ID.get()


def new_trace_id(seed: str | None = None) -> str:
    """ID opaco; con `seed` es estable para redelivery sin exponer el wamid."""
    if seed:
        digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
        return f"wa-{digest[:16]}"
    return uuid.uuid4().hex[:20]


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    bound = _clean_name(trace_id or new_trace_id(), limit=64)
    token = _TRACE_ID.set(bound)
    try:
        yield bound
    finally:
        _TRACE_ID.reset(token)


def install_logging_context() -> None:
    """Añade `trace_id` a todos los LogRecord sin alterar librerías externas."""
    global _logging_installed
    if _logging_installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        record.trace_id = current_trace_id()
        return record

    logging.setLogRecordFactory(factory)
    _logging_installed = True


def record_operation(
    operation: str,
    outcome: str = "ok",
    *,
    duration_ms: float | int | None = None,
) -> None:
    key = (_clean_name(operation), _clean_name(outcome))
    duration = max(0.0, float(duration_ms or 0.0))
    with _lock:
        metric = _metrics.setdefault(key, _Metric())
        metric.count += 1
        if duration_ms is not None:
            metric.duration_ms_sum += duration
            metric.duration_ms_max = max(metric.duration_ms_max, duration)


def audit_event(event: str, outcome: str = "ok", **fields: Any) -> None:
    """Emite una línea JSON segura. Los campos no aprobados se descartan."""
    clean_event = _clean_name(event)
    clean_outcome = _clean_name(outcome)
    payload: dict[str, Any] = {
        "event": clean_event,
        "outcome": clean_outcome,
        "trace_id": current_trace_id(),
        "timestamp": round(time.time(), 3),
    }
    for key, value in fields.items():
        if key not in _SAFE_FIELDS or value is None:
            continue
        payload[key] = _safe_value(value)

    with _lock:
        _audit_counts[(clean_event, clean_outcome)] += 1
    level = logging.INFO if clean_outcome in {"ok", "accepted", "duplicate"} else logging.WARNING
    log.log(level, "[audit] %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def metrics_snapshot() -> dict[str, Any]:
    with _lock:
        operations = {
            f"{operation}:{outcome}": {
                "count": metric.count,
                "duration_ms_sum": round(metric.duration_ms_sum, 3),
                "duration_ms_max": round(metric.duration_ms_max, 3),
            }
            for (operation, outcome), metric in sorted(_metrics.items())
        }
        audit_total = sum(_audit_counts.values())
    return {
        "trace_context": True,
        "audit_events": audit_total,
        "operation_series": operations,
    }


def render_prometheus() -> str:
    """Formato Prometheus sin dependencia adicional ni etiquetas sensibles."""
    lines = [
        "# HELP donregalo_operations_total Operaciones observadas por resultado.",
        "# TYPE donregalo_operations_total counter",
        "# HELP donregalo_operation_duration_ms Duración agregada de operaciones.",
        "# TYPE donregalo_operation_duration_ms summary",
        "# HELP donregalo_audit_events_total Eventos estructurados de auditoría.",
        "# TYPE donregalo_audit_events_total counter",
    ]
    with _lock:
        metric_items = list(sorted(_metrics.items()))
        audit_items = list(sorted(_audit_counts.items()))

    for (operation, outcome), metric in metric_items:
        labels = f'operation="{_escape(operation)}",outcome="{_escape(outcome)}"'
        lines.append(f"donregalo_operations_total{{{labels}}} {metric.count}")
        lines.append(
            f"donregalo_operation_duration_ms_sum{{{labels}}} "
            f"{metric.duration_ms_sum:.3f}"
        )
        lines.append(
            f"donregalo_operation_duration_ms_count{{{labels}}} {metric.count}"
        )
        lines.append(
            f"donregalo_operation_duration_ms_max{{{labels}}} "
            f"{metric.duration_ms_max:.3f}"
        )
    for (event, outcome), count in audit_items:
        labels = f'event="{_escape(event)}",outcome="{_escape(outcome)}"'
        lines.append(f"donregalo_audit_events_total{{{labels}}} {count}")
    return "\n".join(lines) + "\n"


def reset_observability() -> None:
    """Aísla las métricas globales entre pruebas."""
    with _lock:
        _metrics.clear()
        _audit_counts.clear()


def _clean_name(value: object, *, limit: int = 80) -> str:
    cleaned = _NAME_RE.sub("_", str(value or "unknown")).strip("_.:")
    return (cleaned or "unknown")[:limit]


def _safe_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3)
    return _clean_name(value, limit=80)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
