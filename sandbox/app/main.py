"""
Punto de entrada del sandbox: WhatsApp Cloud API + CRM + agente.
Correr: uvicorn app.main:app --host 0.0.0.0 --port 8100
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.channels.whatsapp.webhook import router as whatsapp_router
from app.crm.api import router as crm_router
from app.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    log.info("[BOOT] sandbox DB lista")
    yield


app = FastAPI(title="Sandbox Agente Don Regalo", lifespan=lifespan)
app.include_router(whatsapp_router)
app.include_router(crm_router)

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "stack": "sandbox-whatsapp-crm"}


@app.get("/")
async def panel():
    index = WEB_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"message": "Panel no encontrado. Coloca web/index.html"}
