"""Runner del corpus de evals.

Ejecuta los casos de `evals/corpus/` contra el harness real (router, políticas e
invariantes) sin llamar a OpenAI ni a la API: todo lo que evalúa es determinista,
así que corre en CI en milisegundos.

Uso:
    python -m evals.runner            # informe legible
    pytest tests/test_evals.py -q     # el mismo corpus, como test
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any

import yaml

from app.harness.contracts import Product
from app.harness.invariants import check_reply
from app.harness.policies import handoff_policy
from app.harness.router import classify_intent
from app.harness.state import ConversationState

CORPUS = pathlib.Path(__file__).parent / "corpus"


@dataclass
class Result:
    case_id: str
    kind: str
    passed: bool
    detail: str = ""


def _load(name: str) -> list[dict[str, Any]]:
    path = CORPUS / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _state(raw: dict[str, Any] | None) -> ConversationState:
    return ConversationState.from_dict(raw or {})


def _artifacts(raw: list[dict[str, Any]] | None) -> list[Product]:
    return [Product(**item) for item in (raw or [])]


def run_routing() -> list[Result]:
    out: list[Result] = []
    for case in _load("routing"):
        state = _state(case.get("state"))
        got = classify_intent(case["text"], state)
        want = case["expect_intent"]
        out.append(
            Result(
                case_id=case["id"],
                kind="routing",
                passed=got == want,
                detail=f"esperaba {want}, obtuvo {got}",
            )
        )
    return out


def run_replies() -> list[Result]:
    out: list[Result] = []
    for case in _load("replies"):
        state = _state(case.get("state"))
        artifacts = _artifacts(case.get("artifacts"))
        violations = check_reply(case["reply"], state=state, artifacts=artifacts)

        got = sorted(v.rule for v in violations)
        want = sorted(case.get("expect_violations") or [])
        out.append(
            Result(
                case_id=case["id"],
                kind="reply",
                passed=got == want,
                detail=f"esperaba {want or 'ninguna violación'}, obtuvo {got or 'ninguna'}",
            )
        )
    return out


def run_handoff() -> list[Result]:
    """Cuándo procede escalar. Cada `False` aquí es un cliente esperando a un
    asesor que no hacía falta."""
    casos = [
        ("cortesia", "Todo en orden hoy", False),
        ("emoji", "👍", False),
        ("venta-corporativa", "Son regalos Corporativos por Fiestas Patrias", False),
        ("elige-opciones", "2 y 3", False),
        ("solo-imagen", "[image]", False),
        ("pide-asesor", "Quiero hablar con un asesor", True),
        ("comprobante", "Ya les pagué, aquí está el comprobante", True),
        ("descuento", "¿Me haces un descuento?", True),
        ("frustracion", "Qué mala atención, no me ayudas", True),
    ]
    out: list[Result] = []
    for case_id, text, should_allow in casos:
        decision = handoff_policy([{"role": "user", "content": text}])
        out.append(
            Result(
                case_id=case_id,
                kind="handoff",
                passed=decision.allow is should_allow,
                detail=(
                    f"esperaba {'escalar' if should_allow else 'NO escalar'}, "
                    f"obtuvo {'escalar' if decision.allow else 'NO escalar'}"
                ),
            )
        )
    return out


def run_all() -> list[Result]:
    return [*run_routing(), *run_replies(), *run_handoff()]


def main() -> int:
    results = run_all()
    fallos = [r for r in results if not r.passed]

    for r in results:
        marca = "ok  " if r.passed else "FALLA"
        print(f"  {marca} [{r.kind}] {r.case_id}" + ("" if r.passed else f" — {r.detail}"))

    print(f"\n{len(results) - len(fallos)}/{len(results)} casos en verde")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
