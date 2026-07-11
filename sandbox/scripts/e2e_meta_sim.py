"""
E2E local: simula webhook Meta Cloud API → sandbox → CRM MySQL → respuesta (dry-run).

Uso:
  python scripts/e2e_meta_sim.py
  python scripts/e2e_meta_sim.py --text "Hola, busco un regalo para papá"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AGENT_URL = "http://127.0.0.1:8100"
CRM_URL = "http://127.0.0.1:3100"
CRM_TOKEN = "dev-crm-token-change-me"
WA_ID = "51988877766"


def meta_payload(text: str, wa_id: str = WA_ID) -> dict:
    mid = f"wamid.e2e.{uuid.uuid4().hex[:16]}"
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_E2E",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15550001111",
                        "phone_number_id": "PHONE_E2E",
                    },
                    "contacts": [{
                        "wa_id": wa_id,
                        "profile": {"name": "Cliente E2E"},
                    }],
                    "messages": [{
                        "from": wa_id,
                        "id": mid,
                        "timestamp": str(int(time.time())),
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }, mid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="Hola, quiero un regalo para el Día del Padre")
    parser.add_argument("--agent", default=AGENT_URL)
    parser.add_argument("--crm", default=CRM_URL)
    parser.add_argument("--wait", type=float, default=25.0, help="Segundos a esperar flush+LLM")
    args = parser.parse_args()

    print("== health ==")
    with httpx.Client(timeout=15.0) as client:
        h_agent = client.get(f"{args.agent}/health")
        h_crm = client.get(f"{args.crm}/api/health")
        print("agent:", h_agent.status_code, h_agent.text[:200])
        print("crm:", h_crm.status_code, h_crm.text[:200])
        if h_agent.status_code != 200 or h_crm.status_code != 200:
            print("FAIL: agent o CRM no responden")
            return 1

        agent_info = h_agent.json()
        if agent_info.get("crm_mode") != "external":
            print("WARN: crm_mode != external →", agent_info.get("crm_mode"))

        # verify webhook challenge
        v = client.get(
            f"{args.agent}/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "donregalo-verify-e2e",
                "hub.challenge": "12345",
            },
        )
        print("verify:", v.status_code, v.text)
        if v.status_code != 200 or v.text != "12345":
            print("FAIL: verify token (WHATSAPP_VERIFY_TOKEN debe ser donregalo-verify-e2e)")
            return 1

        payload, mid = meta_payload(args.text)
        print("== POST webhook ==")
        print("wamid:", mid)
        r = client.post(f"{args.agent}/whatsapp/webhook", json=payload)
        print("webhook:", r.status_code, r.text[:400])
        if r.status_code != 200:
            return 1

        print(f"== wait {args.wait}s (buffer + agente) ==")
        time.sleep(args.wait)

        headers = {"X-CRM-Token": CRM_TOKEN}
        convs = client.get(f"{args.crm}/api/conversations", headers=headers)
        data = convs.json().get("data") or []
        match = next((c for c in data if c.get("contact", {}).get("wa_id") == WA_ID), None)
        if not match:
            print("FAIL: no hay conversación CRM para", WA_ID)
            print(json.dumps(data[:3], indent=2, default=str))
            return 1

        cid = match["id"]
        detail = client.get(f"{args.crm}/api/conversations/{cid}", headers=headers).json()
        messages = detail.get("messages") or []
        print(f"conversation_id={cid} messages={len(messages)}")
        for m in messages[-6:]:
            preview = (m.get("content") or "")[:100].encode("ascii", "replace").decode("ascii")
            print(f"  [{m.get('direction')}/{m.get('sender_type')}] {preview}")

        has_in = any(m.get("direction") == "inbound" for m in messages)
        has_out = any(
            m.get("direction") == "outbound" and m.get("sender_type") in ("bot", "agent")
            for m in messages
        )
        if not has_in:
            print("FAIL: falta inbound en CRM")
            return 1
        if not has_out:
            print("FAIL: falta outbound del bot (¿OPENAI_API_KEY? ¿buffer? revisa logs sandbox)")
            return 1

        print("OK E2E: webhook -> CRM inbound -> respuesta outbound")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
