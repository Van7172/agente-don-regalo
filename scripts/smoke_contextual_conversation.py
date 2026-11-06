"""Synthetic full harness replay with real model and read-only coverage.

State, Maps metadata and handoff are isolated test adapters. No original chat,
customer coordinates, writes to CRM, WhatsApp, notifications or orders.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.crm import http_client
from app.harness import master, coverage
from app.harness.contracts import EscalateReason
from app.harness.state import ConversationState
from app.harness.trace import Trace
from app.services import agent, demand, messenger


def blocked(*args, **kwargs):
    raise AssertionError("External mutation blocked by synthetic replay")


async def main():
    states, traces, handoffs, rows = {}, [], [], []
    async def load(cid, **kwargs):
        return ConversationState.from_dict(states.get(cid))
    async def save(cid, state, **kwargs):
        states[cid] = state.to_dict()
    async def maps(*args):
        return ""  # coordinate-only Maps response; don't fetch synthetic pin
    async def handoff(**kwargs):
        handoffs.append({"is_payment": kwargs.get("is_payment"), "conversation_id": kwargs.get("conversation_id")})
        return EscalateReason(motivo=kwargs.get("motivo", "synthetic"))
    def trace(value):
        traces.append({"router":value.router,"action":value.context_action,"tools":value.tools,"violations":value.violations})
    scenarios = [
        (991000101, "airport", ["Hacen deliver", "Callao", "Aeropuerto Jorge Chávez", "https://maps.google.com/?q=-12,-77", "Sí, ese mismo punto"]),
        (991000102, "essential", ["Callao", "Lo reciben en el estudio Tres Puntos", "Con el vigilante del lado izquierdo", "Mañana a las 7 en punto", "Si pasa de esa hora ya no habrá nadie"]),
        (991000103, "flexible", ["Callao", "A las 7 en punto", "No, puede llegar más tarde", "De 7 a 9 está bien"]),
    ]
    with patch.object(settings, "contextual_delivery_enabled", True), patch.object(settings, "contextual_delivery_percent", 100), \
         patch.object(master, "load_state", load), patch.object(master, "save_state", save), \
         patch.object(http_client, "crm_enabled", return_value=False), patch.object(http_client, "_request", blocked), \
         patch.object(coverage, "resolve_maps_district", maps), patch.object(master, "perform_handoff", handoff), \
         patch.object(master, "create_temporal_order", blocked), patch.object(master, "notify_team", blocked), \
         patch.object(agent, "_say", blocked), patch.object(messenger, "send_message", blocked), \
         patch.object(messenger, "send_image", blocked), patch.object(messenger, "notify_team", blocked), \
         patch.object(demand, "record_miss", return_value=None), patch.object(Trace, "emit", trace):
        for cid, name, texts in scenarios:
            messages = []
            for text in texts:
                messages.append({"role":"user","content":text})
                start = time.monotonic()
                reply = await master.run_master(messages, wa_id="synthetic-context-no-send", conversation_id=cid)
                row = {"scenario":name,"user":text,"reply":reply,"elapsed_ms":round((time.monotonic()-start)*1000),
                       "trace":traces[-1], "state":{key:states[cid].get(key) for key in (
                           "district","delivery_reference","delivery_point","maps_location_pending","maps_location_confirmed",
                           "shipping_fee_sol","date","requested_delivery_time","time_slot","delivery_time_pending","delivery_time_requires_team")}}
                rows.append(row)
                print(json.dumps(row,ensure_ascii=True),flush=True)
                if reply:
                    messages.append({"role":"assistant","content":reply})
    issues = []
    airport = states[991000101]
    if airport["district"] != "Callao" or not airport["maps_location_confirmed"] or not airport["shipping_fee_sol"]:
        issues.append("airport_destination_not_preserved_and_verified")
    essential = states[991000102]
    if not essential["delivery_time_requires_team"] or essential["time_slot"]:
        issues.append("essential_hour_not_handed_off")
    if handoffs != [{"is_payment":False,"conversation_id":991000102}]:
        issues.append("unexpected_handoffs")
    flexible = states[991000103]
    if flexible["time_slot"] != "07:00 AM a 09:00 AM" or flexible["delivery_time_pending"]:
        issues.append("flexible_window_not_accepted")
    if any(row["trace"]["violations"] for row in rows):
        issues.append("output_guard_violation")
    if any(row["trace"]["router"] != "contextual" for row in rows):
        issues.append("unexpected_legacy_routing")
    path = Path("artifacts/contextual-delivery-conversation-smoke.json")
    path.write_text(json.dumps({"synthetic":True,"crm_writes":False,"customer_sends":False,
                               "rows":rows,"handoffs_simulated":handoffs,"issues":issues},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"turns":len(rows),"issues":issues}),flush=True)
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
