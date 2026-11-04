#!/usr/bin/env python
"""Valida sintaxis y contratos estáticos del CRM PHP.

    python scripts/check_crm.py                    # crm/, lo que va a producción
    python scripts/check_crm.py --dir sandbox/crm  # el borrador
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="crm", help="carpeta del CRM, relativa a la raíz")
    crm = ROOT / parser.parse_args().dir
    if not (crm / "src" / "Repository.php").is_file():
        print(f"[crm-check] {crm} no es una carpeta de CRM", file=sys.stderr)
        return 2

    try:
        php = _binary("php", Path("C:/xampp/php/php.exe"))
        node = _binary("node")
    except RuntimeError as error:
        print(f"[crm-check] {error}", file=sys.stderr)
        return 2

    commands: list[tuple[str, ...]] = []
    commands.extend((php, "-l", str(path)) for path in sorted(crm.rglob("*.php")))
    commands.extend(
        (node, "--check", str(path))
        for path in sorted((crm / "public" / "assets").glob("*.js"))
    )
    commands.extend(
        (php, str(path)) for path in sorted((crm / "tests").glob("*.php"))
    )
    commands.extend(
        (node, str(path)) for path in sorted((crm / "tests").glob("*.js"))
    )

    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            print(
                f"[crm-check] BLOQUEADO: {' '.join(command)}",
                file=sys.stderr,
            )
            return int(completed.returncode) or 1
    print(f"[crm-check] sintaxis y contratos de {crm.relative_to(ROOT).as_posix()}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
