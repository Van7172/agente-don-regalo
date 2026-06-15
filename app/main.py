"""
Punto de entrada de la aplicación: crea la app FastAPI y registra los routers.

Correr con:
  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging

from fastapi import FastAPI

from app.api import webhook, evolution

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = FastAPI(title="Agente Don Regalo")

app.include_router(webhook.router)
app.include_router(evolution.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
