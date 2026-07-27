#!/usr/bin/env python
"""Valida sintaxis y contratos estáticos del CRM PHP."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRM = ROOT / "crm"


def _binary(name: str, fallback: Path | None = None) -> str:
    configured = os.getenv(f"{name.upper()}_BINARY", "").strip()
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which(name)
    if found:
        return found
    if fallback and fallback.is_file():
        return str(fallback)
    raise RuntimeError(f"No se encontró el ejecutable requerido: {name}")


def main() -> int:
    try:
        php = _binary("php", Path("C:/xampp/php/php.exe"))
        node = _binary("node")
    except RuntimeError as error:
        print(f"[crm-check] {error}", file=sys.stderr)
        return 2

    commands: list[tuple[str, ...]] = []
    commands.extend((php, "-l", str(path)) for path in sorted(CRM.rglob("*.php")))
    commands.extend(
        (node, "--check", str(path))
        for path in sorted((CRM / "public" / "assets").glob("*.js"))
    )
    commands.extend(
        (php, str(path)) for path in sorted((CRM / "tests").glob("*.php"))
    )
    commands.extend(
        (node, str(path)) for path in sorted((CRM / "tests").glob("*.js"))
    )

    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(
                f"[crm-check] BLOQUEADO: {' '.join(command)}",
                file=sys.stderr,
            )
            return int(completed.returncode) or 1
    print("[crm-check] sintaxis y contratos CRM: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
