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

from app.guardrails import (
    UNTRUSTED_CONTENT_REMOVED,
    detect_prompt_injection,
    minimize_historical_messages,
    protect_json_for_model,
    protect_profile,
    sanitize_tool_result,
)
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
        want = case.get("expect_intent")
        # `expect_not_intent`: hay mensajes de los que solo sabemos dónde NO
        # deben acabar. La pregunta de Rocío no era de cobertura; qué es
        # exactamente lo decide el clasificador LLM, y fijarle una etiqueta aquí
        # sería congelar una respuesta peor que la que ya da.
        prohibido = case.get("expect_not_intent")
        if want is not None:
            passed, detail = got == want, f"esperaba {want}, obtuvo {got}"
        else:
            passed = got != prohibido
            detail = f"no debía enrutar a {prohibido}, obtuvo {got}"
        out.append(Result(case_id=case["id"], kind="routing", passed=passed, detail=detail))
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


def _adversarial_case(case: dict[str, Any]) -> tuple[bool, str]:
    """Un caso del corpus adversarial. Devuelve (pasó, detalle)."""
    kind = case.get("kind")

    if kind == "injection":
        result = detect_prompt_injection(case["text"])
        esperado = bool(case.get("expect_blocked"))
        if result.blocked is not esperado:
            return False, (
                f"esperaba {'bloqueo' if esperado else 'paso libre'}, "
                f"obtuvo {'bloqueo' if result.blocked else 'paso libre'} "
                f"(score {result.score}, reglas {list(result.rules) or 'ninguna'})"
            )
        # Además de bloquear, importa POR QUÉ: si el motivo cambia, la defensa
        # se movió de sitio y conviene enterarse aquí y no en producción.
        faltan = set(case.get("expect_rules") or []) - set(result.rules)
        if faltan:
            return False, f"no disparó {sorted(faltan)}; disparó {list(result.rules)}"
        return True, "ok"

    if kind == "tool_result":
        limpio, quitados = sanitize_tool_result(case["result"])
        esperado = bool(case.get("expect_sanitized"))
        saneado = quitados > 0 and UNTRUSTED_CONTENT_REMOVED in limpio
        if saneado is not esperado:
            return False, (
                f"esperaba {'saneo' if esperado else 'texto intacto'}, "
                f"obtuvo {'saneo' if saneado else 'texto intacto'}"
            )
        return True, "ok"

    if kind == "tool_privacy":
        protegido = protect_json_for_model(case["result"])
        minimo = int(case.get("expect_redacted_min") or 0)
        if protegido.redacted_count < minimo:
            return False, f"redactó {protegido.redacted_count}, esperaba ≥{minimo}"
        return _sin_rastro(protegido.value, case.get("expect_absent"))

    if kind == "history":
        protegidos, total = minimize_historical_messages(list(case["messages"]))
        minimo = int(case.get("expect_redacted_min") or 0)
        if total < minimo:
            return False, f"redactó {total}, esperaba ≥{minimo}"
        # El último mensaje del cliente se conserva a propósito (su finalidad es
        # este turno), así que solo se revisa lo que queda del historial.
        historial = "\n".join(str(m.get("content") or "") for m in protegidos[:-1])
        return _sin_rastro(historial, case.get("expect_absent"))

    if kind == "profile":
        protegido, _ = protect_profile(dict(case["profile"]))
        for key in case.get("expect_absent_keys") or []:
            if key in protegido:
                return False, f"'{key}' llegó al system prompt"
        for key in case.get("expect_present_keys") or []:
            if key not in protegido:
                return False, f"'{key}' se perdió y sí era útil"
        return True, "ok"

    if kind == "reply":
        violations = check_reply(
            case["reply"],
            state=_state(case.get("state")),
            artifacts=_artifacts(case.get("artifacts")),
            user_text=str(case.get("user_text") or ""),
        )
        got = sorted(v.rule for v in violations)
        want = sorted(case.get("expect_violations") or [])
        if got != want:
            return False, f"esperaba {want or 'ninguna violación'}, obtuvo {got or 'ninguna'}"
        return True, "ok"

    return False, f"kind desconocido: {kind!r}"


def _sin_rastro(texto: str, prohibidos: list[str] | None) -> tuple[bool, str]:
    for aguja in prohibidos or []:
        if aguja in texto:
            return False, f"{aguja!r} sobrevivió a la redacción"
    return True, "ok"


def run_adversarial() -> list[Result]:
    """Ataques contra las reglas de seguridad del CORE.

    El CORE le PIDE al modelo que no revele el prompt, que no obedezca al
    contenido no confiable y que jamás dé datos de otro cliente. Eso es una
    instrucción, no una garantía: un commit compuso una vez el system message sin
    el bloque de RESTRICCIONES y el bot corrió sin ninguna de esas reglas. Aquí se
    ejercitan las defensas DETERMINISTAS equivalentes, que sí pueden fallar en CI.
    """
    out: list[Result] = []
    for case in _load("adversarial"):
        passed, detail = _adversarial_case(case)
        out.append(
            Result(
                case_id=case["id"],
                kind=f"adv:{case.get('kind', '?')}",
                passed=passed,
                detail=detail,
            )
        )
    return out


def run_all() -> list[Result]:
    """Solo lo determinista: sin OpenAI, sin API. Es lo que corre en CI."""
    return [*run_routing(), *run_replies(), *run_handoff(), *run_adversarial()]


async def run_routing_llm() -> list[Result]:
    """Los mensajes que las reglas NO saben clasificar. Llama a OpenAI de verdad.

    Mide justo el hueco que antes se tragaba el catálogo, así que es el único
    conjunto donde el clasificador LLM se está evaluando a sí mismo.
    """
    from app.harness.router import classify, classify_rules

    out: list[Result] = []
    for case in _load("routing_llm"):
        state = _state(case.get("state"))

        rules = classify_rules(case["text"], state)
        if rules.source != "fallback":
            # Si las reglas ya lo resuelven, este caso ya no pertenece aquí.
            out.append(
                Result(
                    case_id=case["id"],
                    kind="routing_llm",
                    passed=rules.intent == case["expect_intent"],
                    detail=f"lo resolvieron las reglas ({rules.intent})",
                )
            )
            continue

        got = await classify(case["text"], state)
        out.append(
            Result(
                case_id=case["id"],
                kind="routing_llm",
                passed=got.intent == case["expect_intent"],
                detail=(
                    f"esperaba {case['expect_intent']}, obtuvo {got.intent} "
                    f"(conf {got.confidence:.2f}, {got.source})"
                ),
            )
        )
    return out


# Tasa de acierto mínima del clasificador LLM. No es 100% a propósito: estos son
# justo los mensajes que las reglas NO saben clasificar, así que fallar alguno es
# normal. Lo que hay que detectar es la CAÍDA — cambiar de modelo o de proveedor
# y no enterarse de que empezó a enrutar peor.
LLM_PASS_THRESHOLD = 0.85


def _report(results: list[Result]) -> int:
    fallos = [r for r in results if not r.passed]
    for r in results:
        marca = "ok  " if r.passed else "FALLA"
        print(f"  {marca} [{r.kind}] {r.case_id}" + ("" if r.passed else f" — {r.detail}"))
    print(f"\n{len(results) - len(fallos)}/{len(results)} casos en verde")
    return 1 if fallos else 0


def _report_llm(results: list[Result], threshold: float) -> int:
    """El corpus LLM se juzga por tasa, no por "todos verdes".

    Un caso suelto que falle no es una regresión: son mensajes ambiguos y el
    modelo no es determinista. Exigir el 100% haría que el job se pusiera rojo
    por ruido, y un job que se pone rojo por ruido se acaba ignorando — que es
    peor que no tenerlo.
    """
    if not results:
        print("Sin casos LLM que evaluar.")
        return 0

    aciertos = sum(1 for r in results if r.passed)
    tasa = aciertos / len(results)
    for r in results:
        marca = "ok  " if r.passed else "FALLA"
        print(f"  {marca} [{r.kind}] {r.case_id}" + ("" if r.passed else f" — {r.detail}"))

    # Una línea legible por máquina: es la serie temporal que hay que guardar
    # para poder ver la tendencia entre ejecuciones.
    print(
        f"\nLLM_EVAL_RESULT rate={tasa:.4f} passed={aciertos} total={len(results)} "
        f"threshold={threshold:.2f}"
    )
    if tasa < threshold:
        print(
            f"\nFALLA: la tasa de acierto ({tasa:.1%}) cayó por debajo del "
            f"umbral ({threshold:.0%}). Revisa si cambió el modelo o el prompt "
            f"del router."
        )
        return 1
    print(f"{tasa:.1%} de acierto, por encima del umbral ({threshold:.0%})")
    return 0


def main() -> int:
    import os
    import sys

    if "--llm" in sys.argv:
        import asyncio

        threshold = float(
            os.getenv("LLM_EVAL_THRESHOLD", str(LLM_PASS_THRESHOLD))
        )
        return _report_llm(asyncio.run(run_routing_llm()), threshold)
    return _report(run_all())


if __name__ == "__main__":
    raise SystemExit(main())
