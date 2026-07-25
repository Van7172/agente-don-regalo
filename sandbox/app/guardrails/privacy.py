"""Minimización y redacción determinista de datos personales.

El dato que el cliente acaba de proporcionar puede utilizarse durante el turno
para cumplir una finalidad concreta (por ejemplo, rastrear un pedido). No debe
quedar propagado indefinidamente en historial, memoria, resultados de tools ni
telemetría.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]{1,64}@"
    r"(?:[a-zA-Z0-9-]{1,63}\.)+[a-zA-Z]{2,24}(?![\w.-])"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:(?:\+?51)[\s.-]?)?(9(?:[\s.-]?\d){8})(?!\d)"
)
_DNI_RE = re.compile(
    r"(?i)\b(?:dni|documento(?:\s+de\s+identidad)?)\s*[:#-]?\s*(\d{8})\b"
)
_RUC_RE = re.compile(r"(?i)\bruc\s*[:#-]?\s*((?:10|20)\d{9})\b")
_CARD_RE = re.compile(
    r"(?i)\b(?:tarjeta|card|visa|mastercard)\s*[:#-]?\s*"
    r"((?:\d[\s-]?){13,19})\b"
)

_SENSITIVE_KEYS = frozenset(
    {
        "address",
        "correo",
        "direccion",
        "document",
        "documento",
        "dni",
        "email",
        "numero_documento",
        "phone",
        "ruc",
        "telefono",
        "telefono_destinatario",
    }
)
_KEY_LABELS = {
    "address": "[dirección protegida]",
    "correo": "[correo protegido]",
    "direccion": "[dirección protegida]",
    "document": "[documento protegido]",
    "documento": "[documento protegido]",
    "dni": "[documento protegido]",
    "email": "[correo protegido]",
    "numero_documento": "[documento protegido]",
    "phone": "[teléfono protegido]",
    "ruc": "[documento protegido]",
    "telefono": "[teléfono protegido]",
    "telefono_destinatario": "[teléfono protegido]",
}


@dataclass(frozen=True)
class PrivacyResult:
    value: str
    redacted_count: int
    categories: tuple[str, ...] = ()


def redact_personal_data(text: object) -> PrivacyResult:
    """Redacta identificadores directos sin confundir precios o ids de producto."""
    value = str(text or "")
    categories: list[str] = []
    count = 0

    def replace(pattern: re.Pattern[str], label: str, category: str) -> None:
        nonlocal value, count
        value, found = pattern.subn(label, value)
        if found:
            count += found
            categories.extend([category] * found)

    replace(_EMAIL_RE, "[correo protegido]", "email")
    replace(_PHONE_RE, "[teléfono protegido]", "phone")
    replace(_DNI_RE, "DNI [documento protegido]", "document")
    replace(_RUC_RE, "RUC [documento protegido]", "document")
    replace(_CARD_RE, "tarjeta [dato de pago protegido]", "payment")
    return PrivacyResult(value, count, tuple(dict.fromkeys(categories)))


def protect_json_for_model(raw_result: str) -> PrivacyResult:
    """Redacta PII dentro de JSON conservando su estructura y datos comerciales."""
    try:
        payload = json.loads(raw_result)
    except (TypeError, ValueError):
        return redact_personal_data(raw_result)

    count = 0
    categories: list[str] = []

    def walk(value: Any, key: str = "") -> Any:
        nonlocal count
        normalized_key = key.casefold().strip()
        if normalized_key in _SENSITIVE_KEYS and value not in (None, ""):
            count += 1
            category = _category_for_key(normalized_key)
            categories.append(category)
            return _KEY_LABELS[normalized_key]
        if isinstance(value, str):
            result = redact_personal_data(value)
            count += result.redacted_count
            categories.extend(result.categories)
            return result.value
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            return {item_key: walk(item, str(item_key)) for item_key, item in value.items()}
        return value

    protected = walk(payload)
    return PrivacyResult(
        json.dumps(protected, ensure_ascii=False),
        count,
        tuple(dict.fromkeys(categories)),
    )


def minimize_historical_messages(messages: list) -> tuple[list, int]:
    """Redacta PII histórica; conserva el último turno para su finalidad inmediata."""
    last_user = max(
        (index for index, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    protected: list = []
    total = 0
    for index, message in enumerate(messages):
        item = dict(message)
        role = str(item.get("role") or "")
        if role not in {"user", "tool"} or (role == "user" and index == last_user):
            protected.append(item)
            continue

        content = item.get("content")
        if isinstance(content, str):
            result = (
                protect_json_for_model(content)
                if role == "tool"
                else redact_personal_data(content)
            )
            item["content"] = result.value
            total += result.redacted_count
        elif isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    parts.append(part)
                    continue
                result = redact_personal_data(part.get("text") or "")
                parts.append({**part, "text": result.value})
                total += result.redacted_count
            item["content"] = parts
        protected.append(item)
    return protected, total


def protect_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Retira contacto/documentos del perfil que se inyecta al system prompt."""
    protected: dict[str, Any] = {}
    total = 0
    for key, value in profile.items():
        normalized_key = str(key).casefold().strip()
        if value in (None, ""):
            continue
        if normalized_key in _SENSITIVE_KEYS:
            total += 1
            continue
        if isinstance(value, str):
            result = redact_personal_data(value)
            value = result.value
            total += result.redacted_count
        protected[str(key)] = value
    return protected, total


def _category_for_key(key: str) -> str:
    if key in {"email", "correo"}:
        return "email"
    if key in {"phone", "telefono", "telefono_destinatario"}:
        return "phone"
    if key in {"address", "direccion"}:
        return "address"
    return "document"
