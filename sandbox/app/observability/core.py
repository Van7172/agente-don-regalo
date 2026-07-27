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
from dataclasses import dataclass, field
from typing import Any, Iterator

log = logging.getLogger("app.audit")

_TRACE_ID: ContextVar[str] = ContextVar("donregalo_trace_id", default="-")
_NAME_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")
_SAFE_FIELDS = frozenset(
    {
        "agent",
        "backend",
        "cached_tokens",
        "completion_tokens",
        "circuit",
        "conversation_id",
        "data_category",
        "depth",
        "duplicate_count",
        "error_type",
        "from_state",
        "intent",
        "latency_ms",
        "llm_calls",
        "message_type",
        "prompt_tokens",
        "prompt_version",
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


# Cortes del histograma de latencia, en milisegundos. Son los de una API web
# normal, elegidos para que el interesante quede en medio y no en los extremos:
# un turno del agente vive entre 1 y 5 s, así que 2500 y 5000 son los cortes que
# de verdad separan "va bien" de "el cliente está esperando demasiado".
_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    50.0, 100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0,
)


@dataclass
class _Metric:
    count: int = 0
    # Observaciones QUE TRAEN duración. No es lo mismo que `count`: hay
    # operaciones que se registran sin medir tiempo (`inbound.conversation_lock`
    # busy, `guardrail.input` blocked). Dividir la suma entre `count` daba una
    # media falsamente baja — repartía el tiempo entre llamadas que nunca se
    # cronometraron.
    duration_count: int = 0
    duration_ms_sum: float = 0.0
    duration_ms_max: float = 0.0
    # Un contador por corte, no acumulado (se acumula al renderizar). El último
    # recoge todo lo que pasa del mayor corte: es el `+Inf` de Prometheus.
    buckets: list[int] = field(default_factory=lambda: [0] * (len(_LATENCY_BUCKETS_MS) + 1))


_lock = threading.Lock()
_metrics: dict[tuple[str, str], _Metric] = {}
_audit_counts: Counter[tuple[str, str]] = Counter()
# Gauges: valores que suben y bajan (profundidad de la DLQ, de la cola). Un
# contador no sirve para esto — la pregunta es "cuánto hay AHORA", no "cuántos
# hubo en total".
_gauges: dict[tuple[str, str], float] = {}
# Tokens y coste del LLM. A escala es la línea de gasto que más crece y hoy era
# ciega: sin esto, un prompt que engorda solo se nota en la factura del mes.
_tokens: Counter[tuple[str, str]] = Counter()
_cost_usd: dict[str, float] = {}
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
            metric.duration_count += 1
            metric.duration_ms_sum += duration
            metric.duration_ms_max = max(metric.duration_ms_max, duration)
            metric.buckets[_bucket_index(duration)] += 1


def _bucket_index(duration_ms: float) -> int:
    for index, edge in enumerate(_LATENCY_BUCKETS_MS):
        if duration_ms <= edge:
            return index
    return len(_LATENCY_BUCKETS_MS)  # +Inf


# Acumulador de tokens del turno EN CURSO. Es un ContextVar por la misma razón
# que el trace-id: con varios turnos en vuelo a la vez, un contador global no
# sabría de quién es cada token. Cada tarea de asyncio ve el suyo.
_TURN_TOKENS: ContextVar[dict[str, int] | None] = ContextVar(
    "donregalo_turn_tokens", default=None
)


@contextmanager
def collect_turn_usage() -> Iterator[dict[str, int]]:
    """Suma lo que gaste el LLM dentro del bloque. Un turno puede llamarlo varias
    veces (rondas de tools), y lo que importa es el total del turno."""
    bucket: dict[str, int] = {"prompt": 0, "completion": 0, "cached": 0, "calls": 0}
    token = _TURN_TOKENS.set(bucket)
    try:
        yield bucket
    finally:
        _TURN_TOKENS.reset(token)


# Qué agente está hablando con el modelo AHORA. Igual que el trace-id: pasarlo
# como parámetro obligaría a cruzarlo por media docena de firmas (y a que cada
# test que hace stub del cliente HTTP lo replique), y con turnos concurrentes un
# global sin contexto atribuiría el gasto al agente equivocado.
_AGENT: ContextVar[str] = ContextVar("donregalo_agent", default="specialist")


@contextmanager
def agent_context(name: str) -> Iterator[str]:
    bound = _clean_name(name or "specialist")
    token = _AGENT.set(bound)
    try:
        yield bound
    finally:
        _AGENT.reset(token)


def current_agent() -> str:
    return _AGENT.get()


def current_turn_usage() -> dict[str, int] | None:
    """Lo que lleva gastado el turno en curso, o None fuera de un turno."""
    return _TURN_TOKENS.get()


def record_gauge(name: str, value: float, *, scope: str = "global") -> None:
    """Un valor que sube y baja. Se sobrescribe, no se acumula.

    `record_operation` cuenta sucesos; esto mide un nivel. La profundidad de la
    DLQ es el caso que lo pedía: "cuántos mensajes envenenados hay ahora mismo"
    no se puede reconstruir a partir de cuántos cayeron alguna vez.
    """
    key = (_clean_name(name), _clean_name(scope))
    with _lock:
        _gauges[key] = float(value)


def record_tokens(
    *,
    agent: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Consumo del LLM por agente. Nunca ve el contenido, solo cuánto ocupó.

    `cached_tokens` es la parte del prompt que OpenAI sirvió de su caché: es la
    métrica que dice si el CORE —que va idéntico en TODOS los agentes de cara al
    cliente— está aprovechando el caché o se está pagando entero en cada turno.
    """
    label = _clean_name(agent)
    bucket = _TURN_TOKENS.get()
    if bucket is not None:
        bucket["prompt"] += int(prompt_tokens)
        bucket["completion"] += int(completion_tokens)
        bucket["cached"] += int(cached_tokens)
        bucket["calls"] += 1
    with _lock:
        if prompt_tokens:
            _tokens[(label, "prompt")] += int(prompt_tokens)
        if completion_tokens:
            _tokens[(label, "completion")] += int(completion_tokens)
        if cached_tokens:
            _tokens[(label, "cached")] += int(cached_tokens)
        if cost_usd:
            _cost_usd[label] = _cost_usd.get(label, 0.0) + float(cost_usd)


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


def _quantile_from_buckets(metric: _Metric, quantile: float) -> float:
    """p95/p99 a partir de los cortes, como hace `histogram_quantile`.

    Existe porque el equipo mira el panel del CRM, no Grafana: dejar los buckets
    solo en `/metrics` sería medir la latencia para nadie. Interpola dentro del
    corte donde cae el objetivo, así que es una **estimación** acotada por los
    bordes del bucket — no un valor exacto, y con pocos cortes se nota.

    Si el objetivo cae en el `+Inf` se devuelve el máximo observado: decir
    "infinito" no ayuda a nadie, y el máximo es el peor caso real que sí medimos.
    """
    total = metric.duration_count
    if total <= 0:
        return 0.0
    objetivo = quantile * total
    acumulado = 0
    borde_inferior = 0.0
    for index, cantidad in enumerate(metric.buckets):
        anterior = acumulado
        acumulado += cantidad
        if acumulado < objetivo or cantidad == 0:
            if index < len(_LATENCY_BUCKETS_MS):
                borde_inferior = _LATENCY_BUCKETS_MS[index]
            continue
        if index >= len(_LATENCY_BUCKETS_MS):
            return round(metric.duration_ms_max, 3)
        borde_superior = _LATENCY_BUCKETS_MS[index]
        fraccion = (objetivo - anterior) / cantidad
        estimado = borde_inferior + (borde_superior - borde_inferior) * fraccion
        # Acotado al peor caso REAL. La interpolación no conoce el máximo y puede
        # pasarse del borde superior del bucket: con 95 muestras de 200 ms y 5 de
        # 8 s salía un p99 de 9 s, mayor que nada que haya ocurrido nunca. En un
        # panel eso se lee como un bug, y con razón.
        return round(min(estimado, metric.duration_ms_max), 3)
    return round(metric.duration_ms_max, 3)


def metrics_snapshot() -> dict[str, Any]:
    with _lock:
        operations = {
            f"{operation}:{outcome}": {
                "count": metric.count,
                # Cuántas de esas se cronometraron. El panel divide la suma entre
                # ESTO, no entre `count`: si no, una operación que a veces se
                # registra sin duración mostraba una media más baja de la real.
                "duration_count": metric.duration_count,
                "duration_ms_sum": round(metric.duration_ms_sum, 3),
                "duration_ms_max": round(metric.duration_ms_max, 3),
                # El `max` esconde la cola: un p95 malo con un máximo normal es
                # invisible, y es justo el caso del cliente que espera de más.
                "duration_ms_p95": _quantile_from_buckets(metric, 0.95),
                "duration_ms_p99": _quantile_from_buckets(metric, 0.99),
            }
            for (operation, outcome), metric in sorted(_metrics.items())
        }
        audit_total = sum(_audit_counts.values())
        gauges = {
            f"{name}:{scope}": value for (name, scope), value in sorted(_gauges.items())
        }
        tokens = {
            f"{agent}:{kind}": total for (agent, kind), total in sorted(_tokens.items())
        }
        cost = dict(sorted(_cost_usd.items()))
    return {
        "trace_context": True,
        "audit_events": audit_total,
        "operation_series": operations,
        "gauges": gauges,
        "llm_tokens": tokens,
        "llm_cost_usd": {agent: round(value, 6) for agent, value in cost.items()},
    }


def render_prometheus() -> str:
    """Formato Prometheus sin dependencia adicional ni etiquetas sensibles."""
    lines = [
        "# HELP donregalo_operations_total Operaciones observadas por resultado.",
        "# TYPE donregalo_operations_total counter",
        "# HELP donregalo_operation_duration_ms Duración de operaciones en ms.",
        # Histograma, no summary: un summary no se puede agregar entre réplicas
        # (los cuantiles ya vienen calculados y no se promedian), y los buckets
        # sí — se suman. Es la diferencia entre tener p95 del servicio o p95 de
        # un pod al azar.
        "# TYPE donregalo_operation_duration_ms histogram",
        "# HELP donregalo_audit_events_total Eventos estructurados de auditoría.",
        "# TYPE donregalo_audit_events_total counter",
        "# HELP donregalo_llm_tokens_total Tokens del LLM por agente y tipo.",
        "# TYPE donregalo_llm_tokens_total counter",
        "# HELP donregalo_llm_cost_usd_total Coste estimado del LLM en USD.",
        "# TYPE donregalo_llm_cost_usd_total counter",
    ]
    with _lock:
        metric_items = list(sorted(_metrics.items()))
        audit_items = list(sorted(_audit_counts.items()))
        gauge_items = list(sorted(_gauges.items()))
        token_items = list(sorted(_tokens.items()))
        cost_items = list(sorted(_cost_usd.items()))

    for (operation, outcome), metric in metric_items:
        labels = f'operation="{_escape(operation)}",outcome="{_escape(outcome)}"'
        lines.append(f"donregalo_operations_total{{{labels}}} {metric.count}")
        # Los buckets van acumulados y en orden, que es lo que exige el formato:
        # `le="100"` cuenta TODO lo que tardó 100 ms o menos.
        acumulado = 0
        for index, edge in enumerate(_LATENCY_BUCKETS_MS):
            acumulado += metric.buckets[index]
            lines.append(
                f'donregalo_operation_duration_ms_bucket{{{labels},le="{edge:g}"}} '
                f"{acumulado}"
            )
        acumulado += metric.buckets[-1]
        lines.append(
            f'donregalo_operation_duration_ms_bucket{{{labels},le="+Inf"}} {acumulado}'
        )
        lines.append(
            f"donregalo_operation_duration_ms_sum{{{labels}}} "
            f"{metric.duration_ms_sum:.3f}"
        )
        # `_count` tiene que cuadrar con el bucket `+Inf` o Prometheus considera
        # el histograma inconsistente. Antes salía `metric.count`, que incluye
        # las llamadas sin duración.
        lines.append(
            f"donregalo_operation_duration_ms_count{{{labels}}} {metric.duration_count}"
        )
        # Fuera del histograma: el peor caso real, que un bucket no puede dar.
        lines.append(
            f"donregalo_operation_duration_ms_max{{{labels}}} "
            f"{metric.duration_ms_max:.3f}"
        )
    for (event, outcome), count in audit_items:
        labels = f'event="{_escape(event)}",outcome="{_escape(outcome)}"'
        lines.append(f"donregalo_audit_events_total{{{labels}}} {count}")

    # Cada gauge sale con su propio nombre de métrica (`donregalo_dlq_depth`) y
    # no como etiqueta de uno genérico: una alerta se escribe contra un nombre,
    # y `donregalo_gauge{gauge="dlq_depth"}` obliga a filtrar en cada consulta.
    for (name, scope), value in gauge_items:
        lines.append(f"# TYPE donregalo_{name} gauge")
        lines.append(f'donregalo_{name}{{scope="{_escape(scope)}"}} {value:g}')

    for (agent, kind), total in token_items:
        labels = f'agent="{_escape(agent)}",type="{_escape(kind)}"'
        lines.append(f"donregalo_llm_tokens_total{{{labels}}} {total}")
    for agent, value in cost_items:
        lines.append(
            f'donregalo_llm_cost_usd_total{{agent="{_escape(agent)}"}} {value:.6f}'
        )
    return "\n".join(lines) + "\n"


def reset_observability() -> None:
    """Aísla las métricas globales entre pruebas."""
    with _lock:
        _metrics.clear()
        _audit_counts.clear()
        _gauges.clear()
        _tokens.clear()
        _cost_usd.clear()


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
