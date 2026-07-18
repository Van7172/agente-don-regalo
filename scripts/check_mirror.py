#!/usr/bin/env python
"""Verifica que `sandbox/` siga siendo un espejo exacto de la raíz.

`sandbox/app`, `sandbox/tests` y `sandbox/evals` son una copia de `app/`, `tests/`
y `evals/`. La fuente de verdad es la raíz. Mantener a mano dos copias idénticas
es frágil y ya divergió una vez: nada avisaba, y el `sandbox` se quedó ejecutando
una versión vieja del harness.

Esto convierte esa divergencia silenciosa en un fallo visible. Se corre en CI y
también a mano:

    python scripts/check_mirror.py          # ¿está sincronizado?
    python scripts/check_mirror.py --fix    # sincronízalo

Salida 0 = espejo OK. Salida 1 = divergen (imprime qué).
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ESPEJOS = ("app", "tests", "evals")
SANDBOX = RAIZ / "sandbox"

# `__pycache__` es generado: que difiera no significa nada.
IGNORAR = {"__pycache__", ".pytest_cache"}


def _archivos(base: Path) -> dict[str, Path]:
    """Ruta relativa → ruta real, saltando lo generado."""
    out: dict[str, Path] = {}
    for ruta in base.rglob("*"):
        if not ruta.is_file():
            continue
        if any(parte in IGNORAR for parte in ruta.parts):
            continue
        if ruta.suffix in (".pyc", ".pyo"):
            continue
        out[str(ruta.relative_to(base)).replace("\\", "/")] = ruta
    return out


def comparar() -> list[str]:
    """Diferencias entre la raíz y el espejo, en lenguaje humano."""
    problemas: list[str] = []

    for nombre in ESPEJOS:
        origen, copia = RAIZ / nombre, SANDBOX / nombre

        if not origen.is_dir():
            problemas.append(f"falta el directorio de origen: {nombre}/")
            continue
        if not copia.is_dir():
            problemas.append(f"falta el espejo: sandbox/{nombre}/")
            continue

        en_raiz, en_espejo = _archivos(origen), _archivos(copia)

        for rel in sorted(set(en_raiz) - set(en_espejo)):
            problemas.append(f"solo en la raíz, falta en el espejo: {nombre}/{rel}")
        for rel in sorted(set(en_espejo) - set(en_raiz)):
            problemas.append(f"sobra en el espejo (borrado en la raíz): sandbox/{nombre}/{rel}")
        for rel in sorted(set(en_raiz) & set(en_espejo)):
            # shallow=False: comparar contenido, no tamaño+mtime. Un `cp` deja
            # mtimes distintos y una comparación superficial daría falsos positivos.
            if not filecmp.cmp(en_raiz[rel], en_espejo[rel], shallow=False):
                problemas.append(f"contenido distinto: {nombre}/{rel}")

    return problemas


def sincronizar() -> None:
    SANDBOX.mkdir(exist_ok=True)
    for nombre in ESPEJOS:
        destino = SANDBOX / nombre
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(
            RAIZ / nombre,
            destino,
            ignore=shutil.ignore_patterns(*IGNORAR, "*.pyc", "*.pyo"),
        )
        print(f"  sincronizado sandbox/{nombre}/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix", action="store_true", help="copia la raíz sobre el espejo"
    )
    args = parser.parse_args()

    if args.fix:
        print("Sincronizando el espejo desde la raíz…")
        sincronizar()

    problemas = comparar()
    if problemas:
        print("\nEl espejo sandbox/ NO está sincronizado:\n", file=sys.stderr)
        for p in problemas:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nArréglalo con:  python scripts/check_mirror.py --fix\n",
            file=sys.stderr,
        )
        return 1

    print("espejo OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
