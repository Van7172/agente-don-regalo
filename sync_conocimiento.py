"""
Backfill de la base de conocimiento del equipo (Nivel B).

Recorre las conversaciones RESUELTAS de Chatwoot y captura el conocimiento que
aportaron los vendedores humanos, indexándolo en Qdrant (`respuestas_equipo`).

Úsalo una vez para arrancar con el histórico; las nuevas conversaciones se
capturan solas al resolverse (ver _handle_conversation_event en main.py).

Uso:
  python sync_conocimiento.py            # procesa hasta MAX_CONV conversaciones
  python sync_conocimiento.py 500        # procesa hasta 500
"""
import os
import sys
import asyncio

import httpx
from dotenv import load_dotenv

load_dotenv()

from app.services import knowledge  # usa la misma lógica de captura que el webhook

CHATWOOT_URL        = os.getenv("CHATWOOT_URL", "").rstrip("/")
CHATWOOT_API_TOKEN  = os.getenv("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "")


async def _listar_resueltas(max_conv: int) -> list[int]:
    """Devuelve ids de conversaciones con estado 'resolved' (paginado)."""
    ids: list[int] = []
    page = 1
    base = f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations"
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        while len(ids) < max_conv:
            r = await client.get(
                base,
                headers={"api_access_token": CHATWOOT_API_TOKEN},
                params={"status": "resolved", "page": page},
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            convs = data.get("payload", []) if isinstance(data, dict) else []
            if not convs:
                break
            ids.extend(c["id"] for c in convs if c.get("id"))
            print(f"  página {page}: {len(convs)} conversaciones")
            page += 1
    return ids[:max_conv]


async def main(max_conv: int) -> int:
    if not CHATWOOT_URL or not CHATWOOT_API_TOKEN:
        print("ERROR: faltan CHATWOOT_URL o CHATWOOT_API_TOKEN.")
        return 1

    print("Listando conversaciones resueltas...")
    ids = await _listar_resueltas(max_conv)
    print(f"Total a procesar: {len(ids)}")

    total_items = 0
    for i, cid in enumerate(ids, 1):
        try:
            n = await knowledge.capturar_de_conversacion(cid)
            total_items += n
            estado = f"{n} items" if n else "—"
            print(f"  [{i}/{len(ids)}] conversación {cid}: {estado}")
        except Exception as e:
            print(f"  [{i}/{len(ids)}] conversación {cid}: ERROR {e}")

    print(f"[OK] Backfill completo. {total_items} items de conocimiento indexados.")
    return 0


if __name__ == "__main__":
    limite = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("MAX_CONV", "300"))
    sys.exit(asyncio.run(main(limite)))
