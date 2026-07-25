"""Circuit breaker asíncrono para dependencias externas.

Estados:
  cerrado -> abierto tras N fallos consecutivos
  abierto -> semiabierto al vencer la pausa
  semiabierto -> cerrado con una prueba sana, o abierto si vuelve a fallar
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

import httpx

from app.config import settings
from app.observability import audit_event, record_operation

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """La dependencia se omite mientras su circuito está abierto."""

    def __init__(self, circuit: str, retry_after: float) -> None:
        self.circuit = circuit
        self.retry_after = max(0.0, retry_after)
        super().__init__(
            f"circuito {circuit!r} abierto; reintento en {self.retry_after:.1f}s"
        )


def _is_countable_failure(error: Exception) -> bool:
    """Solo los fallos atribuibles a la dependencia deterioran el circuito."""
    if isinstance(error, CircuitOpenError):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status >= 500 or status in {408, 429}
    return True


@dataclass(frozen=True)
class CircuitSnapshot:
    state: str
    consecutive_failures: int
    failure_threshold: int
    recovery_seconds: float
    retry_after_seconds: float


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        failure_predicate: Callable[[Exception], bool] = _is_countable_failure,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_seconds = max(0.0, float(recovery_seconds))
        self._clock = clock
        self._failure_predicate = failure_predicate
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = threading.Lock()

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        self._before_call()
        try:
            result = await operation()
        except asyncio.CancelledError:
            self._cancel_probe()
            raise
        except Exception as error:
            self._after_failure(error)
            raise
        self._after_success()
        return result

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            retry_after = self._retry_after_locked(self._clock())
            return CircuitSnapshot(
                state=self._state.value,
                consecutive_failures=self._failures,
                failure_threshold=self.failure_threshold,
                recovery_seconds=self.recovery_seconds,
                retry_after_seconds=round(retry_after, 3),
            )

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def _before_call(self) -> None:
        transition: tuple[CircuitState, CircuitState] | None = None
        now = self._clock()
        with self._lock:
            if self._state is CircuitState.OPEN:
                retry_after = self._retry_after_locked(now)
                if retry_after > 0:
                    self._rejected(retry_after)
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = False
                transition = (CircuitState.OPEN, CircuitState.HALF_OPEN)

            if self._state is CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    self._rejected(0.0)
                self._probe_in_flight = True

        if transition:
            self._emit_transition(*transition)

    def _after_success(self) -> None:
        transition: tuple[CircuitState, CircuitState] | None = None
        with self._lock:
            previous = self._state
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False
            self._state = CircuitState.CLOSED
            if previous is not CircuitState.CLOSED:
                transition = (previous, CircuitState.CLOSED)
        record_operation(f"circuit.{self.name}", "success")
        if transition:
            self._emit_transition(*transition)

    def _after_failure(self, error: Exception) -> None:
        if not self._failure_predicate(error):
            self._after_success()
            return

        transition: tuple[CircuitState, CircuitState] | None = None
        with self._lock:
            previous = self._state
            self._failures += 1
            self._probe_in_flight = False
            if (
                previous is CircuitState.HALF_OPEN
                or self._failures >= self.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()
                if previous is not CircuitState.OPEN:
                    transition = (previous, CircuitState.OPEN)
        record_operation(f"circuit.{self.name}", "failure")
        if transition:
            self._emit_transition(*transition, error_type=type(error).__name__)

    def _cancel_probe(self) -> None:
        with self._lock:
            self._probe_in_flight = False

    def _retry_after_locked(self, now: float) -> float:
        if self._state is not CircuitState.OPEN or self._opened_at is None:
            return 0.0
        return max(0.0, self.recovery_seconds - (now - self._opened_at))

    def _rejected(self, retry_after: float) -> None:
        record_operation(f"circuit.{self.name}", "rejected")
        audit_event(
            "circuit.rejected",
            "rejected",
            circuit=self.name,
            retry_after_seconds=retry_after,
        )
        raise CircuitOpenError(self.name, retry_after)

    def _emit_transition(
        self,
        previous: CircuitState,
        current: CircuitState,
        *,
        error_type: str | None = None,
    ) -> None:
        record_operation(f"circuit.{self.name}", current.value)
        audit_event(
            "circuit.transition",
            current.value,
            circuit=self.name,
            from_state=previous.value,
            to_state=current.value,
            error_type=error_type,
        )


_registry_lock = threading.Lock()
_registry: dict[str, CircuitBreaker] = {}
_KNOWN_CIRCUITS = (
    "catalog.rest",
    "crm",
    "mcp",
    "openai.embeddings",
    "openai.router",
    "openai.specialist",
    "qdrant",
)


def circuit_breaker(name: str) -> CircuitBreaker:
    with _registry_lock:
        breaker = _registry.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=settings.circuit_breaker_failure_threshold,
                recovery_seconds=settings.circuit_breaker_recovery_seconds,
            )
            _registry[name] = breaker
        return breaker


def circuit_breakers_snapshot() -> dict[str, dict[str, Any]]:
    for name in _KNOWN_CIRCUITS:
        circuit_breaker(name)
    with _registry_lock:
        items = list(sorted(_registry.items()))
    return {
        name: {
            "state": snapshot.state,
            "consecutive_failures": snapshot.consecutive_failures,
            "failure_threshold": snapshot.failure_threshold,
            "recovery_seconds": snapshot.recovery_seconds,
            "retry_after_seconds": snapshot.retry_after_seconds,
        }
        for name, breaker in items
        for snapshot in [breaker.snapshot()]
    }


def render_circuit_breaker_prometheus() -> str:
    lines = [
        "# HELP donregalo_circuit_breaker_state Estado: 0 cerrado, 1 semiabierto, 2 abierto.",
        "# TYPE donregalo_circuit_breaker_state gauge",
        "# HELP donregalo_circuit_breaker_consecutive_failures Fallos consecutivos.",
        "# TYPE donregalo_circuit_breaker_consecutive_failures gauge",
    ]
    state_value = {"closed": 0, "half_open": 1, "open": 2}
    for name, snapshot in circuit_breakers_snapshot().items():
        safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
        labels = f'circuit="{safe_name}"'
        lines.append(
            f"donregalo_circuit_breaker_state{{{labels}}} "
            f"{state_value[snapshot['state']]}"
        )
        lines.append(
            f"donregalo_circuit_breaker_consecutive_failures{{{labels}}} "
            f"{snapshot['consecutive_failures']}"
        )
    return "\n".join(lines) + "\n"


def reset_circuit_breakers() -> None:
    """Descarta el registro; útil para aislamiento de pruebas."""
    with _registry_lock:
        _registry.clear()
