"""Traza de un turno del orquestador.

Hasta ahora un turno dejaba una línea de log con la intención y poco más. Cuando
un cliente se quejaba, no había forma de reconstruir qué agente lo atendió, qué
tools se llamaron ni por qué se descartó (o no) un handoff.

La traza es además lo que hace posible el corpus de evals: un turno ejecutado en
seco produce un `Trace`, y las aserciones se escriben contra él.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Trace:
    conversation_id: int | None = None
    intent: str = ""
    agent: str = ""
    checkout_step: str = ""
    user_text: str = ""
    tools: list[str] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)
    escalated: bool = False
    handoff_reason: str = ""
    violations: list[str] = field(default_factory=list)
    state_patch: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    _started: float = field(default_factory=time.monotonic, repr=False)

    def done(self) -> "Trace":
        self.latency_ms = int((time.monotonic() - self._started) * 1000)
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_started", None)
        return data

    def emit(self) -> None:
        """Una línea por turno, en JSON, para poder agregarla después."""
        payload = self.to_dict()
        payload["user_text"] = payload["user_text"][:120]
        level = logging.WARNING if self.violations else logging.INFO
        log.log(level, "[trace] %s", json.dumps(payload, ensure_ascii=False))
