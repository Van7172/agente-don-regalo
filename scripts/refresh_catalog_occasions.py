"""Repara metadatos de ocasión del índice desde fichas vigentes, sin reembebido.

Ejemplo: python scripts/refresh_catalog_occasions.py --ids 235 [--apply]
Sin --apply únicamente muestra el cambio propuesto. Nunca modifica la tienda.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings
from app.services.product_embedding_index import infer_occasions, _occasions
from app.tools import catalog, mcp_client
from app.tools.search import get_qdrant


async def run(ids: list[int], apply: bool) -> None:
    qc = get_qdrant()
    if qc is None:
        raise RuntimeError("Qdrant no configurado")
    detail_fn = mcp_client.detalle_producto if settings.donregalo_use_mcp else catalog.detalle_producto
    async with httpx.AsyncClient(timeout=20.0) as client:
        for pid in ids:
            existing = await asyncio.to_thread(qc.retrieve, collection_name=settings.qdrant_collection, ids=[pid], with_payload=True)
            if not existing:
                raise RuntimeError(f"Producto {pid} ausente del índice; requiere indexado completo")
            response = await detail_fn(client, {"id_producto": pid})
            product = response.get("data")
            if not isinstance(product, dict) or product.get("id_producto") != pid:
                raise RuntimeError(f"Ficha vigente no verificable para {pid}")
            _, explicit = _occasions(product)
            inferred = [key for key in infer_occasions(product) if key not in explicit]
            updates = {"ocasiones_ids": list(dict.fromkeys([*explicit, *inferred])),
                       "ocasiones_catalogo_ids": explicit, "ocasiones_inferidas_ids": inferred}
            before = (existing[0].payload or {}).get("ocasiones_ids") or []
            print(json.dumps({"id": pid, "before": before, "after": updates, "applied": apply}, ensure_ascii=False))
            if apply:
                await asyncio.to_thread(qc.set_payload, collection_name=settings.qdrant_collection, payload=updates, points=[pid], wait=True)
                verified = await asyncio.to_thread(qc.retrieve, collection_name=settings.qdrant_collection, ids=[pid], with_payload=True)
                if not verified or any((verified[0].payload or {}).get(k) != v for k, v in updates.items()):
                    raise RuntimeError(f"Lectura posterior no coincide para {pid}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", type=int, nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    asyncio.run(run(list(dict.fromkeys(options.ids)), options.apply))
