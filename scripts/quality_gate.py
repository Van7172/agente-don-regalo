#!/usr/bin/env python
"""Gate único y fail-closed para CI y builds de producción."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Stage:
    name: str
    command: tuple[str, ...]


@dataclass
class StageResult:
    name: str
    outcome: str
    duration_seconds: float
    exit_code: int


def stages(python: str | None = None) -> tuple[Stage, ...]:
    executable = python or sys.executable
    return (
        Stage("mirror", (executable, "scripts/check_mirror.py")),
        Stage(
            "mcp_contract",
            (
                executable,
                "-m",
                "pytest",
                "tests/test_mcp_contract.py",
                "-q",
                "-k",
                "snapshot_contract_is_mandatory",
            ),
        ),
        Stage("tests", (executable, "-m", "pytest", "tests/", "-q")),
        Stage("evals", (executable, "-m", "evals.runner")),
    )


def run_gate(
    selected: Sequence[Stage],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[int, list[StageResult]]:
    results: list[StageResult] = []
    for stage in selected:
        print(f"\n[quality-gate] {stage.name}", flush=True)
        started = time.monotonic()
        completed = runner(stage.command, cwd=ROOT, check=False)
        duration = round(time.monotonic() - started, 3)
        exit_code = int(completed.returncode)
        outcome = "passed" if exit_code == 0 else "failed"
        results.append(StageResult(stage.name, outcome, duration, exit_code))
        if exit_code != 0:
            print(
                f"\n[quality-gate] BLOQUEADO en {stage.name} "
                f"(exit={exit_code})",
                file=sys.stderr,
            )
            return exit_code or 1, results
    print("\n[quality-gate] APROBADO: artefacto elegible para producción")
    return 0, results


def write_report(path: Path, exit_code: int, results: list[StageResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "outcome": "passed" if exit_code == 0 else "failed",
        "stages": [asdict(item) for item in results],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=(
            Path(os.environ["QUALITY_GATE_REPORT"])
            if os.getenv("QUALITY_GATE_REPORT")
            else None
        ),
        help="Escribe un reporte JSON incluso cuando el gate falla.",
    )
    args = parser.parse_args()
    exit_code, results = run_gate(stages())
    if args.report:
        write_report(args.report, exit_code, results)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
