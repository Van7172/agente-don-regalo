from __future__ import annotations

import subprocess

from scripts.quality_gate import Stage, run_gate, stages


def test_gate_incluye_espejo_contrato_tests_y_evals():
    assert [stage.name for stage in stages("python")] == [
        "mirror",
        "mcp_contract",
        "tests",
        "evals",
    ]


def test_gate_se_detiene_en_la_primera_regresion():
    executed = []

    def fake_runner(command, **_kwargs):
        executed.append(command)
        code = 7 if command[-1] == "falla" else 0
        return subprocess.CompletedProcess(command, code)

    code, results = run_gate(
        [
            Stage("uno", ("gate", "pasa")),
            Stage("dos", ("gate", "falla")),
            Stage("tres", ("gate", "no-debe-ejecutarse")),
        ],
        runner=fake_runner,
    )

    assert code == 7
    assert [result.outcome for result in results] == ["passed", "failed"]
    assert len(executed) == 2
