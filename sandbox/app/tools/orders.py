"""Cliente HTTP de pedidos de donregalo.pe.

Aísla la llamada a `POST /pedidos/temporales` (API.md → Pedidos). No es una tool
del LLM: el cierre es determinista y el orquestador la invoca por código a partir
del estado de la conversación. Aquí no se calcula dinero ni se normaliza nada —
eso lo hace `harness/orders.py`; este módulo solo habla HTTP.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


async def crear_pedido_temporal(client: httpx.AsyncClient, body: dict) -> dict:
    """Crea un pedido temporal en el panel. Devuelve el JSON de la API.

    Lanza `httpx.HTTPStatusError` en 4xx/5xx (incluye el 422 de validación) para
    que quien llame decida — el flujo de cierre lo trata como best-effort y nunca
    tumba el handoff al asesor.
    """
    r = await client.post(
        f"{settings.donregalo_api_base}/pedidos/temporales",
        json=body,
    )
    r.raise_for_status()
    return r.json()
