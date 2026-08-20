"""Endpoints internos agent ↔ CRM (outbox del asesor)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.crm import http_client as crm_http
from app.observability import metrics_snapshot
from app.resilience import circuit_breakers_snapshot
from app.services.inbound_queue import inbound_queue_operational_stats
from app.services.outbox_drain import deliver_outbox

log = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


class ContentDraftBody(BaseModel):
    """Lo que manda el estudio de contenido del CRM.

    `producto` llega tal cual lo devolvió `GET /internal/catalog/search`: ya es
    la forma canónica del adapter, en soles. No se acepta un precio suelto ni un
    nombre a mano — lo que se publica tiene que venir del catálogo.
    """

    producto: dict
    tono: str = "casual"
    formato: str = "9:16"
    instrucciones: str = ""
    incluir_precio: bool = True
    incluir_cta: bool = True
    variantes: int = 3


class OutboxSendBody(BaseModel):
    outbox_id: int | None = None
    wa_id: str
    content: str = ""
    conversation_id: int | None = None
    # Adjunto del asesor: 'image' | 'audio' | 'document' (o 'text' sin adjunto).
    type: str = "text"
    media_path: str | None = None
    filename: str = ""
    # El asesor respondió a un mensaje desde el inbox del CRM.
    reply_to_wa_id: str | None = None
    quoted_text: str | None = None
    # La FOTO del mensaje citado: su texto puede ser solo "[image]".
    quoted_media_url: str | None = None


def _check_token(token: str | None) -> None:
    expected = settings.agent_internal_token
    if expected and token != expected:
        raise HTTPException(401, "Unauthorized")


@router.get("/operations")
async def operations(
    x_agent_token: str | None = Header(default=None),
):
    """Snapshot JSON para el dashboard del CRM; nunca incluye mensajes ni PII."""
    _check_token(x_agent_token)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue": await inbound_queue_operational_stats(),
        "operations": metrics_snapshot(),
        "circuits": circuit_breakers_snapshot(),
        "competition": {
            "enabled": settings.competition_crawl_enabled,
            "crm_enabled": crm_http.crm_enabled(),
            "interval_seconds": settings.competition_crawl_interval_seconds,
        },
    }


@router.post("/competition/crawl")
async def competition_crawl(
    x_agent_token: str | None = Header(default=None),
):
    """Dispara un crawl de competencia ya (ignora el cooldown del watchdog)."""
    _check_token(x_agent_token)
    from app.services import competition_crawl as crawl

    summary = await crawl.run_crawl(force=True)
    log.info("[competencia] crawl manual: %s", summary)
    return {"ok": True, "summary": summary}


@router.get("/catalog/search")
async def catalog_search(
    q: str,
    limit: int = 8,
    x_agent_token: str | None = Header(default=None),
):
    """Buscador de productos para el estudio de contenido del CRM.

    Pasa por `tools/catalog` a propósito, no por la API del catálogo en crudo:
    el adapter es lo único que sabe normalizar las tres formas de producto que
    devuelve el servidor y convertir de dólares a soles. Repetir esa conversión
    en PHP sería garantizar que el precio del post y el precio que cotiza el bot
    acaben divergiendo.
    """
    _check_token(x_agent_token)
    termino = (q or "").strip()
    # Tres caracteres es el mínimo del selector del panel: con menos, cualquier
    # búsqueda devuelve medio catálogo y el asesor elige a ciegas.
    if len(termino) < 3:
        return {"data": [], "total": 0}

    import httpx as _httpx

    from app.tools import catalog

    async with _httpx.AsyncClient(timeout=20.0) as client:
        payload = await catalog.buscar_productos(client, {"q": termino})

    items = payload.get("data") if isinstance(payload, dict) else None
    productos = [p for p in (items or []) if isinstance(p, dict)][: max(1, min(limit, 24))]
    return {"data": productos, "total": len(productos)}


@router.post("/content/draft")
async def content_draft(
    body: ContentDraftBody,
    x_agent_token: str | None = Header(default=None),
):
    """Redacta variantes de post para el producto elegido."""
    _check_token(x_agent_token)

    from app.services.social_copy import generar_copys

    producto = dict(body.producto or {})
    if not producto.get("nombre"):
        raise HTTPException(400, "Falta el producto")

    # El "¿qué contiene?" vive solo en el detalle y es lo único que da material
    # concreto para escribir sin inventar. Es un extra: si el detalle falla, el
    # copy sale igual con lo que trae el listado. Ojo — del detalle se toma la
    # descripción y NADA más: su `imagen_url` está rota en producción (404 en
    # las cuatro variantes) y pisaría la única foto viva, que es la del listado.
    if producto.get("id_producto") and not producto.get("descripcion"):
        try:
            import httpx as _httpx

            from app.tools import catalog

            async with _httpx.AsyncClient(timeout=15.0) as client:
                detalle = await catalog.detalle_producto(
                    client, {"id_producto": producto["id_producto"]}
                )
            data = detalle.get("data") if isinstance(detalle, dict) else None
            if isinstance(data, dict) and data.get("descripcion"):
                producto["descripcion"] = data["descripcion"]
        except Exception as err:  # noqa: BLE001 - el detalle es opcional
            log.info("[contenido] sin detalle para %s: %s", producto.get("id_producto"), err)

    try:
        variantes = await generar_copys(
            producto=producto,
            tono=body.tono,
            formato=body.formato,
            instrucciones=body.instrucciones,
            incluir_precio=body.incluir_precio,
            incluir_cta=body.incluir_cta,
            variantes=body.variantes,
        )
    except Exception as err:
        raise HTTPException(502, f"content draft failed: {err}") from err

    return {
        "producto": producto,
        "formato": body.formato,
        "tono": body.tono,
        "variantes": variantes,
    }


@router.post("/outbox/send")
async def outbox_send(
    body: OutboxSendBody,
    x_agent_token: str | None = Header(default=None),
):
    _check_token(x_agent_token)
    try:
        return await deliver_outbox(
            wa_id=body.wa_id,
            content=body.content,
            conversation_id=body.conversation_id,
            outbox_id=body.outbox_id,
            msg_type=body.type,
            media_path=body.media_path,
            filename=body.filename,
            reply_to_wa_id=body.reply_to_wa_id,
            quoted_text=body.quoted_text,
            quoted_media_url=body.quoted_media_url,
        )
    except Exception as err:
        log.error("[OUTBOX] push falló: %s", err)
        if body.outbox_id and crm_http.crm_enabled():
            # Dejamos pending para el drenaje; solo marcamos failed si ya se intentó metada.
            # Si Meta falló, sí marcamos failed para no reintentar en bucle eterno.
            await crm_http.mark_outbox(body.outbox_id, "failed", str(err)[:500])
        raise HTTPException(502, f"send failed: {err}") from err
