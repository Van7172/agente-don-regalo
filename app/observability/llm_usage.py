"""Consumo y coste del LLM por agente.

A escala, OpenAI es la línea de gasto que más crece y era la única ciega: el
historial está acotado (12 h / 15 mensajes), pero nadie medía cuántos tokens
gastaba de verdad cada agente. Un prompt que engorda —una capa nueva en el CORE,
un playbook que crece— solo se notaba en la factura, un mes tarde y sin saber
quién lo causó.

Vive aparte de `core.py` porque necesita `settings` (el tarifario) y el núcleo de
observabilidad no debe depender de la configuración de la app.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.observability.core import record_tokens

log = logging.getLogger(__name__)


def usage_from_response(response: Any) -> dict[str, int]:
    """Extrae los contadores de `usage` de una respuesta de OpenAI.

    Devuelve ceros ante cualquier forma inesperada: medir el gasto no puede
    romper un turno que ya se respondió bien. Es contabilidad, no servicio.
    """
    if not isinstance(response, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}

    detalle = usage.get("prompt_tokens_details")
    cached = 0
    if isinstance(detalle, dict):
        cached = _entero(detalle.get("cached_tokens"))

    return {
        "prompt_tokens": _entero(usage.get("prompt_tokens")),
        "completion_tokens": _entero(usage.get("completion_tokens")),
        "cached_tokens": cached,
    }


def estimate_cost_usd(model: str, usage: dict[str, int]) -> float:
    """Coste según el tarifario configurado. Sin tarifario, 0.

    A propósito no hay precios por defecto en el código: un tarifario hardcodeado
    se queda viejo en silencio y produce una cifra equivocada, que es peor que no
    dar ninguna. Los tokens (exactos, no caducan) se cuentan siempre; el dinero
    solo si alguien declaró `LLM_PRICES`.

    Los tokens servidos desde la caché de OpenAI se cobran aparte y más baratos:
    si el tarifario trae `cached_in`, se descuentan del prompt y se cobran a su
    tarifa. Sin ese dato se cobran como prompt normal, que es la estimación
    conservadora — nunca prometemos un ahorro que no sabemos si existe.
    """
    rates = settings.llm_prices.get(model or "")
    if not rates:
        return 0.0

    prompt = max(0, usage.get("prompt_tokens", 0))
    cached = max(0, min(usage.get("cached_tokens", 0), prompt))
    completion = max(0, usage.get("completion_tokens", 0))

    precio_in = float(rates.get("in", 0.0))
    precio_out = float(rates.get("out", 0.0))
    precio_cached = rates.get("cached_in")

    if precio_cached is None:
        frescos, cobro_cache = prompt, 0.0
    else:
        frescos = prompt - cached
        cobro_cache = cached / 1000.0 * float(precio_cached)

    return (
        frescos / 1000.0 * precio_in
        + cobro_cache
        + completion / 1000.0 * precio_out
    )


def record_llm_usage(agent: str, model: str, response: Any) -> dict[str, int]:
    """Registra tokens y coste de una llamada. Nunca lanza."""
    try:
        usage = usage_from_response(response)
        if not any(usage.values()):
            return usage
        record_tokens(
            agent=agent or "unknown",
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            cached_tokens=usage["cached_tokens"],
            cost_usd=estimate_cost_usd(model, usage),
        )
        return usage
    except Exception as error:  # pragma: no cover - contabilidad best-effort
        log.debug("[llm-usage] no se pudo contabilizar: %s", type(error).__name__)
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}


def _entero(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
