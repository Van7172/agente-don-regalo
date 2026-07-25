"""Contrato cruzado entre los schemas PHP y los campos que consume Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from app.guardrails.parameters import MCP_ARGUMENT_SCHEMAS


ROOT = Path(__file__).resolve().parents[1]
CONSUMER_CONTRACT = ROOT / "tests" / "fixtures" / "mcp" / "consumer_contract.json"
PROVIDER_SNAPSHOT = (
    ROOT / "tests" / "fixtures" / "mcp" / "provider_contract_snapshot.json"
)


def _server_root() -> Path:
    configured = os.getenv("DONREGALO_SERVER_ROOT")
    if configured:
        return Path(configured).resolve()
    return (ROOT.parent / "donregalo").resolve()


def _php_binary() -> Path | None:
    configured = os.getenv("PHP_BINARY")
    candidates = [
        Path(configured) if configured else None,
        Path(r"C:\xampp\php\php.exe"),
    ]
    return next((path for path in candidates if path and path.is_file()), None)


def _published_tools() -> dict[str, dict]:
    server = _server_root()
    php = _php_binary()
    autoload = server / "clienteApiApp" / "src" / "autoload.php"
    if php is None or not autoload.is_file():
        pytest.skip("repo donregalo/PHP no disponible para validar el contrato cruzado")

    script = (
        f"require {json.dumps(str(autoload))};"
        "echo json_encode("
        "\\DonRegalo\\ClienteApi\\Mcp\\Tools::definiciones(),"
        " JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);"
    )
    completed = subprocess.run(
        [str(php), "-r", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    definitions = json.loads(completed.stdout)
    return {definition["name"]: definition for definition in definitions}


def test_output_schemas_publican_todo_lo_que_consume_python():
    expected = json.loads(CONSUMER_CONTRACT.read_text(encoding="utf-8"))
    published = _published_tools()

    missing_tools = sorted(set(expected) - set(published))
    assert not missing_tools, f"tools MCP no publicadas: {missing_tools}"

    for name, requirement in expected.items():
        schema = published[name]["outputSchema"]
        container = requirement["container"]
        if container:
            assert container in schema.get("required", []), (
                f"{name}: el contenedor {container!r} no es required"
            )
            schema = schema["properties"][container]["items"]

        required = set(schema.get("required", []))
        missing = sorted(set(requirement["required"]) - required)
        assert not missing, f"{name}: outputSchema no exige {missing}"
        assert schema.get("additionalProperties") is False, (
            f"{name}: el objeto consumido debe cerrar additionalProperties"
        )


def test_snapshot_contract_is_mandatory():
    """Contrato offline obligatorio: nunca depende de un checkout PHP opcional."""
    consumer = json.loads(CONSUMER_CONTRACT.read_text(encoding="utf-8"))
    provider = json.loads(PROVIDER_SNAPSHOT.read_text(encoding="utf-8"))

    assert set(consumer) == set(provider)
    assert set(provider) == set(MCP_ARGUMENT_SCHEMAS)

    for name, snapshot in provider.items():
        input_schema = MCP_ARGUMENT_SCHEMAS[name]
        assert input_schema.get("type") == "object"
        assert input_schema.get("additionalProperties") is False

        agent_keys = set((input_schema.get("properties") or {}).keys())
        provider_keys = set(snapshot["input_keys"])
        assert agent_keys <= provider_keys, (
            f"{name}: el agente podría enviar campos no publicados: "
            f"{sorted(agent_keys - provider_keys)}"
        )
        assert set(input_schema.get("required") or []) == set(
            snapshot["input_required"]
        )

        expected_output = consumer[name]
        assert snapshot["output_container"] == expected_output["container"]
        assert set(snapshot["output_required"]) == set(expected_output["required"])
