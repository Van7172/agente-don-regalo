"""Mecanismos de resiliencia compartidos por los adaptadores externos."""

from app.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    circuit_breaker,
    circuit_breakers_snapshot,
    render_circuit_breaker_prometheus,
    reset_circuit_breakers,
)

__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "circuit_breaker",
    "circuit_breakers_snapshot",
    "render_circuit_breaker_prometheus",
    "reset_circuit_breakers",
]
