"""Validación estricta de parámetros de herramientas y del transporte MCP."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EMAIL_RE = re.compile(
    r"^[\w.!#$%&'*+/=?^`{|}~-]{1,64}@"
    r"(?:[a-zA-Z0-9-]{1,63}\.)+[a-zA-Z]{2,24}$"
)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ORDER_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class ParameterValidation:
    valid: bool
    errors: tuple[str, ...] = ()


class ParameterValidationError(ValueError):
    """La llamada no cumple el contrato cerrado de su herramienta."""

    def __init__(self, tool: str, errors: tuple[str, ...]):
        self.tool = tool
        self.errors = errors
        super().__init__(f"parámetros inválidos para {tool}: {'; '.join(errors)}")


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


MCP_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "donregalo_navegacion_catalogo": _object(
        {"incluir_campanas": {"type": "boolean"}}
    ),
    "donregalo_buscar_productos": _object(
        {
            "q": {"type": "string", "minLength": 1, "maxLength": 200},
            "categoria": {"type": "string", "format": "slug", "maxLength": 100},
            "filtro": {"type": "string", "format": "slug", "maxLength": 100},
            "landing": {"type": "string", "format": "slug", "maxLength": 120},
            "orden": {"type": "string", "enum": ["asc", "desc"]},
            "ocasion": {"type": "integer", "minimum": 1, "maximum": 7},
            "limite": {"type": "integer", "minimum": 1, "maximum": 30},
            "incluir_funebre": {"type": "boolean"},
        }
    ),
    "donregalo_detalle_producto": _object(
        {"id": {"type": "integer", "minimum": 1}},
        required=("id",),
    ),
    "donregalo_productos_destacados": _object(
        {"limite": {"type": "integer", "minimum": 1, "maximum": 30}}
    ),
    "donregalo_productos_ofertas": _object(
        {"limite": {"type": "integer", "minimum": 1, "maximum": 30}}
    ),
    "donregalo_metodos_pago": _object({}),
    "donregalo_rastrear_pedido": _object(
        {
            "email": {
                "type": "string",
                "format": "email",
                "minLength": 3,
                "maxLength": 254,
            },
            "codigo": {
                "type": "string",
                "format": "order_code",
                "minLength": 2,
                "maxLength": 64,
            },
        },
        required=("email", "codigo"),
    ),
    "donregalo_validar_activos": _object(
        {
            "ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "maxItems": 100,
                "uniqueItems": True,
            }
        },
        required=("ids",),
    ),
}


def validate_mcp_arguments(tool: str, arguments: object) -> dict:
    """Valida allowlist, claves, tipos, límites y formatos antes de abrir la red."""
    schema = MCP_ARGUMENT_SCHEMAS.get(tool)
    if schema is None:
        raise ParameterValidationError(tool or "unknown", ("tool MCP no permitida",))
    result = validate_arguments(arguments, schema)
    if not result.valid:
        raise ParameterValidationError(tool, result.errors)
    return dict(arguments)


def validate_arguments(arguments: object, schema: dict[str, Any]) -> ParameterValidation:
    errors: list[str] = []
    _validate(arguments, schema, "$", errors)
    return ParameterValidation(not errors, tuple(errors))


def _validate(value: object, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: debe ser objeto")
            return
        properties = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in value:
                errors.append(f"{path}.{key}: requerido")
        if schema.get("additionalProperties", False) is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: campo no permitido")
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}", errors)
        return

    if expected == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: debe ser arreglo")
            return
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{path}: muy pocos elementos")
        if len(value) > int(schema.get("maxItems", 10_000)):
            errors.append(f"{path}: demasiados elementos")
        if schema.get("uniqueItems") and len({_stable(item) for item in value}) != len(value):
            errors.append(f"{path}: contiene duplicados")
        for index, item in enumerate(value):
            _validate(item, schema.get("items") or {}, f"{path}[{index}]", errors)
        return

    if expected == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: debe ser texto")
            return
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: texto demasiado corto")
        if len(value) > int(schema.get("maxLength", 500)):
            errors.append(f"{path}: texto demasiado largo")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: valor no permitido")
        format_name = schema.get("format")
        if format_name == "email" and not _EMAIL_RE.fullmatch(value):
            errors.append(f"{path}: correo inválido")
        elif format_name == "slug" and not _SLUG_RE.fullmatch(value):
            errors.append(f"{path}: slug inválido")
        elif format_name == "order_code" and not _ORDER_CODE_RE.fullmatch(value):
            errors.append(f"{path}: código inválido")
        return

    if expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: debe ser entero")
            return
        _validate_number(value, schema, path, errors)
        return

    if expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: debe ser número")
            return
        _validate_number(float(value), schema, path, errors)
        return

    if expected == "boolean" and not isinstance(value, bool):
        errors.append(f"{path}: debe ser booleano")


def _validate_number(
    value: float | int,
    schema: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: menor al mínimo")
    if "maximum" in schema and value > schema["maximum"]:
        errors.append(f"{path}: mayor al máximo")


def _stable(value: object) -> str:
    if isinstance(value, (str, int, float, bool, type(None))):
        return repr(value)
    return repr(value)
