"""
Punto de entrada del agente: WhatsApp Cloud API + CRM + LLM + watchdog.
Correr: uvicorn app.main:app --host 0.0.0.0 --port 8000
Producción (Docker/EasyPanel): puerto ${PORT:-80}
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api_internal import router as internal_router
from app.channels.whatsapp.webhook import router as whatsapp_router
from app.config import settings
from app.crm.api import router as crm_router
from app.db import init_db
from app.services.outbox_poller import start_outbox_drain, stop_outbox_drain
from app.services.watchdog import start_watchdog, stop_watchdog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
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
    start_watchdog()
    start_outbox_drain()
    yield
    stop_outbox_drain()
    stop_watchdog()


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
    }


@app.get("/")
async def panel():
    index = WEB_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {
        "message": "Agente Don Regalo. Panel de asesores: crm-php en el hosting del cliente.",
    }
