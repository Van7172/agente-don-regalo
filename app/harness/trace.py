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

from app.observability import (
    audit_event,
    current_trace_id,
    record_operation,
)

log = logging.getLogger(__name__)


@dataclass
class Trace:
    conversation_id: int | None = None
    intent: str = ""
    agent: str = ""
    confidence: float = 1.0
    router: str = ""       # rules | llm | fallback
    # Huella de las instrucciones con las que se atendió el turno. Los prompts
    # son constantes Python: sin esto, un cambio de redacción y un cambio de
    # comportamiento eran indistinguibles al leer los logs. Con ella se puede
    # partir cualquier métrica por versión y ver si algo se degradó justo cuando
    # alguien reescribió un playbook.
    prompt_version: str = ""
    checkout_step: str = ""
    user_text: str = ""
    tools: list[str] = field(default_factory=list)
    product_ids: list[int] = field(default_factory=list)
    escalated: bool = False
    handoff_reason: str = ""
    violations: list[str] = field(default_factory=list)
    state_patch: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    # Lo que costó ESTE turno. Con la métrica por agente se sabe qué prompt
    # engordó; con esto se sabe qué conversación se disparó — que es la pregunta
    # cuando alguien mira la factura y busca el turno de 40.000 tokens.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    llm_calls: int = 0
    _started: float = field(default_factory=time.monotonic, repr=False)

    def with_usage(self, usage: dict[str, int] | None) -> "Trace":
        if not usage:
            return self
        self.prompt_tokens = int(usage.get("prompt", 0))
        self.completion_tokens = int(usage.get("completion", 0))
        self.cached_tokens = int(usage.get("cached", 0))
        self.llm_calls = int(usage.get("calls", 0))
        return self

    def done(self) -> "Trace":
        self.latency_ms = int((time.monotonic() - self._started) * 1000)
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_started", None)
        return data

    def emit(self) -> None:
        """Una línea segura por turno, sin mensajes ni datos del checkout."""
        payload = self.to_dict()
        user_text = str(payload.pop("user_text", "") or "")
        state_patch = payload.pop("state_patch", {}) or {}
        handoff_reason = str(payload.pop("handoff_reason", "") or "")
        payload["trace_id"] = current_trace_id()
        payload["input_chars"] = len(user_text)
        payload["state_patch_keys"] = sorted(str(key) for key in state_patch)
        payload["handoff_reason_present"] = bool(handoff_reason)

        outcome = (
            "guardrail_blocked"
            if self.violations
            else "handoff"
            if self.escalated
            else "ok"
        )
        record_operation(
            "harness.turn",
            outcome,
            duration_ms=self.latency_ms,
        )
        audit_event(
            "harness.turn",
            outcome,
            conversation_id=self.conversation_id,
            intent=self.intent,
            agent=self.agent,
            router=self.router,
            prompt_version=self.prompt_version,
            tool_count=len(self.tools),
            product_count=len(self.product_ids),
            violation_count=len(self.violations),
            latency_ms=self.latency_ms,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cached_tokens=self.cached_tokens,
            llm_calls=self.llm_calls,
        )
        level = logging.WARNING if self.violations else logging.INFO
        log.log(level, "[trace] %s", json.dumps(payload, ensure_ascii=False))
