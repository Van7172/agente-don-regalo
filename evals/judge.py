"""Juez LLM: la calidad que las invariantes no ven.

Las invariantes son estructurales —precio respaldado, sin duplicados, la URL en
su línea, ningún marcador interno—. Todas se pueden cumplir a la perfección en
una respuesta que igualmente **no resuelve nada**, o que suena a formulario. Y
eso es justo lo que hace bueno (o malo) a un agente de atención.

Este juez no sustituye a las invariantes: caza lo que ellas no pueden ver.
Deliberadamente NO va en el gate de CI —llama a OpenAI y su veredicto no es
determinista—; se corre a mano o de forma programada, y lo que se vigila es la
**tendencia**, no un caso suelto.

    python -m evals.judge          # informe legible
    python -m evals.judge --json   # una línea por caso, para acumular la serie
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import httpx
import yaml

CORPUS = pathlib.Path(__file__).parent / "corpus" / "quality.yaml"

# Rúbrica corta a propósito. Un juez con quince criterios da notas que nadie
# sabe interpretar y que se mueven por ruido; con tres, un cambio significa algo.
RUBRIC = """Eres un evaluador de calidad de un agente de atención por WhatsApp de
una tienda de regalos con delivery en Lima (Don Regalo).

Evalúa ÚNICAMENTE la última respuesta del agente, con estos tres criterios:

1. resolvio: ¿hace avanzar al cliente hacia lo que pidió? Un "¿en qué te puedo
   ayudar?" ante una pregunta concreta NO resuelve. Derivar a un humano cuando
   el motivo lo justifica (pagos, reclamos, descuentos) SÍ resuelve.
2. tono: ¿cálido, cercano y breve, como un vendedor peruano amable? Un muro de
   texto, un tono robótico o pedir varios datos a la vez restan.
3. sin_inventar: ¿evita afirmar precios, stock, plazos o promesas que no se
   sostengan en lo que ya se dijo en la conversación?

Responde SOLO un JSON:
{"resolvio": 0-2, "tono": 0-2, "sin_inventar": 0-2, "motivo": "máx 15 palabras"}

0 = mal, 1 = aceptable, 2 = bien."""


@dataclass
class Verdict:
    case_id: str
    resolvio: int = 0
    tono: int = 0
    sin_inventar: int = 0
    motivo: str = ""
    error: str = ""

    @property
    def score(self) -> float:
        """0.0–1.0. Los tres criterios pesan igual: no hay uno que compre a otro."""
        return (self.resolvio + self.tono + self.sin_inventar) / 6.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "resolvio": self.resolvio,
            "tono": self.tono,
            "sin_inventar": self.sin_inventar,
            "score": round(self.score, 3),
            "motivo": self.motivo,
            "error": self.error,
        }


@dataclass
class JudgeReport:
    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def scored(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.error]

    @property
    def average(self) -> float:
        evaluados = self.scored
        if not evaluados:
            return 0.0
        return sum(v.score for v in evaluados) / len(evaluados)


def load_cases() -> list[dict[str, Any]]:
    if not CORPUS.is_file():
        return []
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8")) or []


def _conversation_text(case: dict[str, Any]) -> str:
    lineas = []
    for turno in case.get("conversacion") or []:
        quien = "Cliente" if turno.get("de") == "cliente" else "Agente"
        lineas.append(f"{quien}: {turno.get('texto', '')}")
    lineas.append(f"Agente (RESPUESTA A EVALUAR): {case.get('respuesta', '')}")
    return "\n".join(lineas)


def parse_verdict(case_id: str, raw: str) -> Verdict:
    """Lee el JSON del juez sin fiarse de que venga bien.

    Un juez que devuelve basura no puede tumbar la evaluación entera: se anota
    como error y el resto del corpus se sigue puntuando.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return Verdict(case_id=case_id, error="respuesta del juez no es JSON")
    if not isinstance(data, dict):
        return Verdict(case_id=case_id, error="respuesta del juez no es un objeto")

    def nota(clave: str) -> int:
        valor = data.get(clave)
        try:
            return max(0, min(2, int(valor)))
        except (TypeError, ValueError):
            return 0

    return Verdict(
        case_id=case_id,
        resolvio=nota("resolvio"),
        tono=nota("tono"),
        sin_inventar=nota("sin_inventar"),
        motivo=str(data.get("motivo") or "")[:120],
    )


async def judge_case(client: httpx.AsyncClient, case: dict[str, Any]) -> Verdict:
    from app.config import settings

    case_id = str(case.get("id") or "sin-id")
    try:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.router_model,
                "messages": [
                    {"role": "system", "content": RUBRIC},
                    {"role": "user", "content": _conversation_text(case)},
                ],
                "response_format": {"type": "json_object"},
                # El juez tiene que ser lo más reproducible posible: si además de
                # no ser determinista fuera creativo, la serie no valdría nada.
                "temperature": 0,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        contenido = response.json()["choices"][0]["message"]["content"]
    except Exception as error:
        return Verdict(case_id=case_id, error=type(error).__name__)
    return parse_verdict(case_id, contenido)


async def run_judge() -> JudgeReport:
    casos = load_cases()
    if not casos:
        return JudgeReport()
    async with httpx.AsyncClient() as client:
        veredictos = [await judge_case(client, caso) for caso in casos]
    return JudgeReport(verdicts=veredictos)


def main() -> int:
    import asyncio
    import sys

    from app.config import settings

    if not settings.openai_api_key:
        print("Sin OPENAI_API_KEY: el juez necesita el modelo. Se omite.")
        return 0

    umbral = float(os.getenv("JUDGE_THRESHOLD", "0.75"))
    report = asyncio.run(run_judge())

    if not report.verdicts:
        print("Corpus de calidad vacío (evals/corpus/quality.yaml).")
        return 0

    if "--json" in sys.argv:
        for verdict in report.verdicts:
            print(json.dumps(verdict.to_dict(), ensure_ascii=False))
    else:
        for verdict in report.verdicts:
            if verdict.error:
                print(f"  ERROR [{verdict.case_id}] {verdict.error}")
                continue
            print(
                f"  {verdict.score:.2f} [{verdict.case_id}] "
                f"resuelve={verdict.resolvio} tono={verdict.tono} "
                f"sin_inventar={verdict.sin_inventar} — {verdict.motivo}"
            )

    fallidos = len(report.verdicts) - len(report.scored)
    print(
        f"\nJUDGE_RESULT average={report.average:.4f} "
        f"scored={len(report.scored)} errors={fallidos} threshold={umbral:.2f}"
    )
    if report.scored and report.average < umbral:
        print(
            f"\nFALLA: la calidad media ({report.average:.0%}) cayó por debajo "
            f"del umbral ({umbral:.0%})."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
