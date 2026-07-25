"""
Punto de entrada del agente: WhatsApp Cloud API + CRM + LLM + watchdog.
Correr: uvicorn app.main:app --host 0.0.0.0 --port 8000
Producción (Docker/EasyPanel): puerto ${PORT:-80}
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api_internal import router as internal_router
from app.channels.whatsapp.webhook import router as whatsapp_router
from app.config import settings
from app.crm.api import router as crm_router
from app.db import init_db
from app.observability import install_logging_context, render_prometheus
from app.resilience import (
    circuit_breakers_snapshot,
    render_circuit_breaker_prometheus,
)
from app.services.inbound_queue import (
    inbound_queue_stats,
    start_inbound_queue,
    stop_inbound_queue,
)
from app.services.outbox_poller import start_outbox_drain, stop_outbox_drain
from app.services.product_embedding_worker import (
    start_embedding_worker,
    stop_embedding_worker,
)
from app.services.watchdog import start_watchdog, stop_watchdog

install_logging_context()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s trace=%(trace_id)s %(message)s",
)
log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.crm_mode != "external":
        await init_db()
        log.info("[BOOT] DB local lista (CRM_MODE=local)")
    else:
        log.info("[BOOT] CRM externo: %s", settings.crm_base_url)
        token = (settings.crm_internal_token or "").strip()
        if not token or token in (
            "dev-crm-token-change-me",
            "cambia-este-token-seguro",
            "el-mismo-que-en-config.php",
        ):
            log.error(
                "[BOOT] CRM_INTERNAL_TOKEN inválido o de ejemplo — "
                "debe coincidir EXACTO con crm_internal_token en config.php del CRM PHP"
            )
        if settings.whatsapp_dry_run:
            log.error(
                "[BOOT] WHATSAPP_DRY_RUN=1 — los mensajes del asesor se verán en el CRM "
                "pero NO llegarán a WhatsApp. Pon WHATSAPP_DRY_RUN=0 en EasyPanel."
            )
    if settings.donregalo_use_mcp:
        log.info("[BOOT] MCP Don Regalo activo: %s", settings.donregalo_mcp_url)
    else:
        log.warning(
            "[BOOT] MCP Don Regalo inactivo; las lecturas usarán HTTP directo. "
            "Revisa DONREGALO_USE_MCP=1 y DONREGALO_MCP_TOKEN."
        )
    await start_inbound_queue()
    start_watchdog()
    start_outbox_drain()
    start_embedding_worker()
    try:
        yield
    finally:
        await stop_inbound_queue()
        stop_outbox_drain()
        stop_watchdog()
        stop_embedding_worker()


app = FastAPI(title="Agente Don Regalo", lifespan=lifespan)
app.include_router(whatsapp_router)
app.include_router(crm_router)
app.include_router(internal_router)

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stack": "whatsapp-cloud-crm",
        "crm_mode": settings.crm_mode,
        "crm_base_url": settings.crm_base_url if settings.crm_mode == "external" else None,
        "watchdog": settings.watchdog_enabled,
        "whatsapp_configured": bool(settings.whatsapp_token and settings.whatsapp_phone_number_id),
        "whatsapp_dry_run": settings.whatsapp_dry_run,
        "openai_configured": bool(settings.openai_api_key),
        "openai_model": settings.openai_model,
        "donregalo_mcp_enabled": settings.donregalo_use_mcp,
        "donregalo_mcp_configured": bool(settings.donregalo_mcp_token),
        "inbound_queue": inbound_queue_stats(),
        "distributed_coordination": {
            "enabled": settings.inbound_queue_backend == "redis",
            "conversation_lock": (
                "redis_lease"
                if settings.inbound_queue_backend == "redis"
                else "process_local"
            ),
            "state_backend": (
                "crm"
                if settings.crm_mode == "external"
                else "process_local"
            ),
        },
        "observability": {
            "trace_context": True,
            "audit": "structured_logs",
            "metrics": "/metrics",
        },
        "circuit_breakers": circuit_breakers_snapshot(),
    }


@app.get("/metrics", include_in_schema=False)
async def metrics(x_agent_token: str | None = Header(default=None)):
    expected = settings.agent_internal_token
    if expected and x_agent_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return Response(
        content=render_prometheus() + render_circuit_breaker_prometheus(),
        media_type="text/plain; version=0.0.4",
    )


@app.get("/")
async def panel():
    index = WEB_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "message": "Agente Don Regalo. Panel de asesores: crm/ en el hosting del cliente.",
    }
