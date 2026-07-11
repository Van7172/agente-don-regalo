"""
Punto de entrada del sandbox: WhatsApp Cloud API + CRM + agente + watchdog.
Correr: uvicorn app.main:app --host 0.0.0.0 --port 8100
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
        log.info("[BOOT] sandbox DB local lista")
    else:
        log.info("[BOOT] CRM externo: %s", settings.crm_base_url)
    start_watchdog()
    yield
    stop_watchdog()


app = FastAPI(title="Sandbox Agente Don Regalo", lifespan=lifespan)
app.include_router(whatsapp_router)
app.include_router(crm_router)
app.include_router(internal_router)

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stack": "sandbox-whatsapp-crm",
        "crm_mode": settings.crm_mode,
        "crm_base_url": settings.crm_base_url if settings.crm_mode == "external" else None,
        "watchdog": settings.watchdog_enabled,
    }


@app.get("/")
async def panel():
    index = WEB_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "Panel legacy sandbox. Usa crm/ en :3100 (Opción C)."}
