"""Prueba sintética de solo lectura del flujo integrado contra catálogo real.

No envía WhatsApp ni persiste estado. No necesita LangGraph. Las escrituras de
demanda se bloquean en este proceso. Usa únicamente los mensajes públicos abajo.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crm import http_client
from app.harness import catalog_flow, catalog_rollout, master
from app.harness.contracts import Turn
from app.harness.state import ConversationState
from app.guardrails.response import check_reply
from app.services import agent, demand

CASES = [
    ("criollo-precio", "Desayuno criollo que precio tiene", {}, [235], []),
    ("cumple-mujer", "desayuno criollo para cumpleaños para mujer", {}, [235], [349, 745]),
    ("nombre-completo", "desayuno criollo love", {}, [235], []),
    ("cumple-love", "desayuno criollo love para cumpleaños", {}, [235], []),
    ("fecha", "quiero desayuno criollo para mañana", {}, [235], []),
    ("kitty", "peluche kitty sunshine para mi esposa", {}, [1279], [235]),
    ("loquito", "peluche oso loquito de amor", {}, [290], [235]),
    ("blancas", "rosas blancas", {}, [], []),
    ("presupuesto", "desayuno criollo", {"budget_amount": 100, "budget_currency": "PEN"}, [], []),
    ("mas", "más opciones", {"catalog_query": "desayuno criollo", "shown_product_ids": [235]}, [], [235]),
    ("dinosaurio", "peluche dinosaurio para mi esposa", {}, [], [235]),
    ("inexistente", "peluche xilofono de titanio", {}, [], [235]),
]


def blocked(*_a, **_k):
    raise AssertionError("Efecto externo bloqueado en smoke de catálogo")


async def main():
    rows = []
    with patch.object(http_client, "crm_enabled", return_value=False), patch.object(demand, "record_miss", return_value=None), \
         patch.object(catalog_rollout, "selected", return_value=True), patch.object(agent, "_say", blocked), \
         patch.object(master, "save_state", blocked), patch.object(master, "create_temporal_order", blocked), \
         patch.object(master, "perform_handoff", blocked), patch.object(master, "notify_team", blocked):
        for name, text, state_args, expected, forbidden in CASES:
            state = ConversationState.from_dict(state_args)
            turn = Turn(text=text, messages=[{"role": "user", "content": text}])
            assert catalog_flow.eligible(turn, state), name
            started = time.monotonic()
            result = await master._handle("catalog_search", turn, state, wa_id="synthetic-no-send")
            issues = []
            if not set(expected) <= set(result.product_ids):
                issues.append("missing_expected_product")
            if set(forbidden) & set(result.product_ids):
                issues.append("incompatible_product")
            if result.catalog_strategy not in {"deterministic", "legacy_fallback"}:
                issues.append("did_not_use_new_flow")
            if name in {"dinosaurio", "inexistente"} and result.artifacts and not result.catalog_approximate:
                issues.append("unmarked_alternative")
            issues.extend(str(v) for v in check_reply(result.user_facing, state=state, artifacts=result.artifacts,
                          user_text=text, tools_used=result.tools_used, agent="catalog", sourced_prices=result.sourced_prices))
            row = {"case": name, "ids": result.product_ids, "strategy": result.catalog_strategy,
                   "approximate": result.catalog_approximate, "tools": result.tools_used,
                   "elapsed_ms": round((time.monotonic() - started) * 1000), "issues": issues}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
    path = Path("artifacts/catalog-canary-live-smoke.json")
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"synthetic": True, "crm_writes": False, "whatsapp_sends": False, "cases": rows}, indent=2) + "\n", encoding="utf-8")
    return int(any(row["issues"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
