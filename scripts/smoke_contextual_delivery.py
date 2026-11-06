"""Synthetic live-model eval of decisions; no customer data, CRM or sends.

Runs only the interpreter. Acceptance checks semantic actions and extracted
facts, never exact prose. A strict clarification is acceptable for ambiguous
confirmations. No campaign, order, payment or conversation is mutated.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.harness import contextual as c
from app.harness.contracts import Turn
from app.harness.state import ConversationState

CASES = [
    ("reference-unlisted", "Donde trabaja ella, el taller El Roble", {"district":"Callao"},
     "¿Dónde lo recibirían?", {"reference"}, {"reference":"El Roble"}),
    ("reference-studio", "Lo reciben en el estudio Tres Puntos", {"district":"Callao"},
     "¿Cuál es la referencia?", {"reference"}, {}),
    ("airport", "Aeropuerto Jorge Chávez", {"district":"Callao"},
     "¿Qué regalo quieres enviar?", {"reference"}, {"reference":"Aeropuerto Jorge Chávez"}),
    ("point-unlisted", "Con el vigilante del lado izquierdo", {"district":"Callao","delivery_reference":"El Roble","delivery_point_pending":True},
     "¿En qué punto lo reciben?", {"point"}, {}),
    ("business-name", "Hotel Miraflores", {"district":"Callao"},
     "¿Dónde lo reciben en Callao?", {"reference"}, {"district":""}),
    ("change-district", "Mejor en Miraflores", {"district":"Callao"},
     "¿Dónde lo reciben en Callao?", {"destination"}, {"district":"Miraflores"}),
    ("confirm-pin", "Ese mismo lugar, correcto", {"maps_location_pending":True,"maps_district_candidate":"Callao"},
     "¿El pin corresponde al punto indicado en Callao?", {"confirm_location"}, {}),
    ("reject-pin", "No, ese no es el sitio", {"maps_location_pending":True,"maps_district_candidate":"Callao"},
     "¿El pin corresponde al punto indicado en Callao?", {"reject_location"}, {}),
    ("ambiguous-yes", "Sí", {"maps_location_pending":True,"maps_district_candidate":"Callao","delivery_time_pending":True,"requested_delivery_time":"07:00"},
     "¿Es en Callao? ¿Es indispensable que llegue a las 7 en punto?", {"clarify"}, {}),
    ("exact-hour-first", "A las 7 en punto", {"district":"Callao"},
     "¿A qué hora lo necesitas?", {"request_time"}, {"requested_time":"07:00"}),
    ("essential-indirect", "Si pasa de esa hora ya no habrá nadie", {"district":"Callao","delivery_time_pending":True,"requested_delivery_time":"07:00"},
     "¿Es indispensable que llegue a las 7 en punto?", {"essential_time"}, {}),
    ("flexible-negated", "No, puede llegar más tarde", {"district":"Callao","delivery_time_pending":True,"requested_delivery_time":"07:00"},
     "¿Es indispensable que llegue a las 7 en punto?", {"flexible_time"}, {}),
    ("flexible-no-need", "No es indispensable, cualquier hora de ese rango está bien", {"district":"Callao","delivery_time_pending":True,"requested_delivery_time":"07:00"},
     "El rango es de 7 a 9. ¿Es indispensable llegar a las 7?", {"flexible_time","accept_window"}, {}),
    ("accept-window", "De 7 a 9 está bien", {"district":"Callao","delivery_time_pending":True,"requested_delivery_time":"07:00"},
     "¿Qué rango te sirve?", {"accept_window"}, {"window":"07:00 AM a 09:00 AM"}),
    ("maps", "https://maps.google.com/?q=-12,-77", {"district":"Callao","delivery_reference":"El Roble"},
     "¿En qué punto recibirían el regalo?", {"location"}, {}),
    ("product-switch", "Ahora muéstrame desayunos para mi mamá", {"district":"Callao","delivery_reference":"El Roble"},
     "¿Qué regalo quieres enviar?", {"other"}, {"intent":"catalog_search"}),
    ("product-question", "¿Ese desayuno trae café?", {"district":"Callao","chosen_product_name":"Desayuno Love"},
     "Te muestro el desayuno elegido.", {"other"}, {"intent":"product_detail"}),
    ("generic-delivery", "Hacen deliver", {}, "Hola, ¿en qué puedo ayudarte?", {"delivery_question"}, {}),
    ("reference-not-district", "La casa comunal del parque", {}, "¿Dónde sería la entrega?", {"reference"}, {"district":""}),
    ("not-a-place", "Podrá hacer la entrega?", {}, "Hola, ¿en qué puedo ayudarte?", {"delivery_question","clarify"}, {"district":"","reference":""}),
]


async def main():
    rows = []
    for name, text, fields, previous, actions, expected in CASES:
        state = ConversationState(**fields)
        turn = Turn(text=text, messages=[{"role":"assistant","content":previous}, {"role":"user","content":text}])
        started = time.monotonic()
        plan = await c.interpret(turn, state)
        issues = []
        if plan is None:
            issues.append("no_valid_interpretation")
        elif plan.action not in actions:
            issues.append("wrong_semantic_action")
        if plan:
            for key, value in expected.items():
                actual = c._norm(getattr(plan, key))
                wanted = c._norm(value)
                matches = wanted in actual if key in {"reference", "point"} and wanted else actual == wanted
                if not matches:
                    issues.append(f"incorrect_{key}")
        row = {"case":name, "plan":asdict(plan) if plan else None,
               "elapsed_ms":round((time.monotonic()-started)*1000), "issues":issues}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    path = Path("artifacts/contextual-delivery-live-eval.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"synthetic":True,"model_only":True,"cases":rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(row["issues"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
