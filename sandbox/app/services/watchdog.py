"""
Watchdog del agente (portado del kit).

Niveles:
1. Mute detector — conversaciones AI sin respuesta
2. Saldo OpenAI / OpenRouter bajo
3. Spike de mensajes de emergencia
4. Parte diario con IA (sugerencias; nunca auto-aplica)

Best-effort: nunca debe tumbar el agente.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from app.config import settings
from app.crm import http_client as crm_http
from app.harness.sale import sale_key
from app.harness.sale import summary as sale_summary
from app.services.messenger import send_message

log = logging.getLogger(__name__)

MUTE_MIN_SEC = 180
MUTE_MAX_SEC = 2 * 60 * 60
BALANCE_MIN_USD = 2.0
REALERT_SEC = 30 * 60
DAILY_EVERY_SEC = 20 * 60 * 60
FALLBACK_WINDOW_SEC = 15 * 60
FALLBACK_THRESHOLD = 3
# Una venta cerrada que nadie atiende en 15 min es un lead enfriándose.
SALE_IDLE_SEC = 15 * 60
EMERGENCY_MARKERS = (
    "un asesor de nuestro equipo continúa contigo",
    "te conecto con un asesor",
)

_task: Optional[asyncio.Task] = None
_fallback_hits: list[float] = []


def record_fallback_event() -> None:
    """Llamar cuando el agente emite mensaje de emergencia / handoff forzado."""
    now = time.time()
    _fallback_hits.append(now)
    cutoff = now - FALLBACK_WINDOW_SEC
    while _fallback_hits and _fallback_hits[0] < cutoff:
        _fallback_hits.pop(0)


async def _en_cooldown(clave: str) -> bool:
    if not crm_http.crm_enabled():
        return False
    last = await crm_http.get_setting(f"wd_{clave}")
    if not last:
        return False
    try:
        return int(time.time()) - int(last) < REALERT_SEC
    except ValueError:
        return False


async def _marcar(clave: str) -> None:
    if crm_http.crm_enabled():
        await crm_http.put_setting(f"wd_{clave}", str(int(time.time())))


async def _send_alert(text: str) -> bool:
    phone = settings.alert_whatsapp
    if not phone:
        log.warning("[watchdog] (sin ALERT_WHATSAPP) %s", text[:120])
        return False
    try:
        await send_message(phone, text)
        log.info("[watchdog] aviso enviado a %s", phone)
        return True
    except Exception as err:
        log.warning("[watchdog] no pude enviar aviso: %s", err)
        return False


async def check_mute() -> None:
    if not crm_http.crm_enabled():
        return
    paused = await crm_http.get_setting("paused")
    if paused == "1":
        return
    pendientes = await crm_http.get_unanswered(MUTE_MIN_SEC, MUTE_MAX_SEC)
    if not pendientes or await _en_cooldown("mute"):
        return
    quien = "\n".join(
        f"- {c.get('name') or c.get('phone')}" for c in pendientes[:5]
    )
    extra = f"\n(y {len(pendientes) - 5} más)" if len(pendientes) > 5 else ""
    ok = await _send_alert(
        f"⚠️ El agente lleva sin responder a {len(pendientes)} lead(s):\n"
        f"{quien}{extra}\n\n"
        "Míralo en el CRM. Suele ser: saldo OpenAI, error del modelo o Meta caído."
    )
    if ok:
        await _marcar("mute")


async def check_balance() -> None:
    if await _en_cooldown("balance"):
        return
    # OpenRouter si hay key; si no, solo log (OpenAI no expone créditos simples)
    or_key = getattr(settings, "openrouter_api_key", "") or ""
    # permitir vía env directo
    import os

    or_key = os.getenv("OPENROUTER_API_KEY", or_key)
    if not or_key:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {or_key}"},
            )
            if r.status_code != 200:
                return
            data = (r.json() or {}).get("data") or {}
            total = data.get("total_credits")
            usage = data.get("total_usage")
            if not isinstance(total, (int, float)) or not isinstance(usage, (int, float)):
                return
            restante = float(total) - float(usage)
            if restante < BALANCE_MIN_USD:
                ok = await _send_alert(
                    f"💳 Saldo OpenRouter bajo: ~${restante:.2f}.\n"
                    "Recarga para que el agente siga respondiendo."
                )
                if ok:
                    await _marcar("balance")
    except Exception:
        return


async def check_fallback_spike() -> None:
    now = time.time()
    cutoff = now - FALLBACK_WINDOW_SEC
    while _fallback_hits and _fallback_hits[0] < cutoff:
        _fallback_hits.pop(0)
    if len(_fallback_hits) < FALLBACK_THRESHOLD or await _en_cooldown("fallback"):
        return
    ok = await _send_alert(
        f"🚨 Spike de mensajes de emergencia: {len(_fallback_hits)} en "
        f"{FALLBACK_WINDOW_SEC // 60} min.\nRevisa logs del sandbox y OpenAI."
    )
    if ok:
        await _marcar("fallback")


async def daily_audit() -> None:
    if not crm_http.crm_enabled() or await _en_cooldown("daily"):
        # daily usa ventana distinta; reutilizamos setting wd_daily con DAILY_EVERY_SEC
        pass
    last = await crm_http.get_setting("wd_daily") if crm_http.crm_enabled() else None
    if last:
        try:
            if int(time.time()) - int(last) < DAILY_EVERY_SEC:
                return
        except ValueError:
            pass
    if not settings.openai_api_key or not settings.alert_whatsapp:
        return
    try:
        # Resumen liviano sin volcar todo el inbox
        unanswered = (
            await crm_http.get_unanswered(MUTE_MIN_SEC, MUTE_MAX_SEC)
            if crm_http.crm_enabled()
            else []
        )
        prompt = (
            "Eres un auditor del agente WhatsApp de Don Regalo. "
            f"Hay {len(unanswered)} conversaciones potencialmente mudas ahora. "
            "Escribe un parte diario corto en español (máx 1200 caracteres) con: "
            "1) estado operativo, 2) riesgos, 3) 2 sugerencias de mejora del guion "
            "(NUNCA digas que se auto-aplican)."
        )
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if r.status_code != 200:
                return
            text = r.json()["choices"][0]["message"]["content"]
        ok = await _send_alert(f"📋 Parte diario del agente\n\n{text}")
        if ok and crm_http.crm_enabled():
            await crm_http.put_setting("wd_daily", str(int(time.time())))
    except Exception as err:
        log.warning("[watchdog] daily audit falló: %s", err)


async def check_human_abandoned() -> None:
    """Alerta si hay chats HUMAN con cliente esperando (el releaser debería reactivar)."""
    if not crm_http.crm_enabled() or await _en_cooldown("human_idle"):
        return
    # Reutiliza unanswered del CRM; si no hay datos, no molestar.
    try:
        pendientes = await crm_http.get_unanswered(15 * 60, 6 * 3600)
    except Exception:
        return
    # Filtrar solo los que el CRM marque en modo humano si el campo existe.
    humans = [
        c for c in (pendientes or [])
        if str(c.get("mode") or c.get("mode_conversation") or "").upper() == "HUMAN"
        or c.get("human_support")
    ]
    if not humans:
        return
    quien = "\n".join(f"- {c.get('name') or c.get('phone')}" for c in humans[:5])
    ok = await _send_alert(
        f"👤 Hay {len(humans)} chat(s) en modo humano sin respuesta reciente:\n"
        f"{quien}\n\n"
        "Si el asesor ya terminó, pulsa «Devolver a Don Regalo» o espera el auto-retorno."
    )
    if ok:
        await _marcar("human_idle")


async def check_unattended_sales() -> None:
    """Ventas cerradas que nadie ha ido a cobrar.

    Es la alerta que más dinero vale: el bot ya cerró el pedido (producto,
    distrito, fecha, horario) y el cliente está esperando para pagar. Si el chat
    verde lleva rato sin que ningún asesor entre, el lead se enfría solo.
    """
    if not crm_http.crm_enabled():
        return

    try:
        pendientes = await crm_http.get_unanswered(SALE_IDLE_SEC, 24 * 3600)
    except Exception as err:
        log.warning("[watchdog] no pude listar pendientes: %s", err)
        return

    for conv in pendientes or []:
        conv_id = conv.get("id") or conv.get("id_conversation")
        if not conv_id:
            continue
        try:
            raw = await crm_http.get_setting(sale_key(int(conv_id)))
        except Exception:
            continue
        if not raw:
            continue  # no hay venta cerrada en este chat

        # Un aviso por venta. Si el asesor no entra, el recordatorio vuelve a los
        # REALERT_SEC, no cada tick.
        if await _en_cooldown(f"sale_{conv_id}"):
            continue

        try:
            venta = json.loads(raw)
        except (TypeError, ValueError):
            continue

        quien = conv.get("name") or conv.get("phone") or f"#{conv_id}"
        ok = await _send_alert(
            f"⏳ Venta cerrada SIN ATENDER hace más de {SALE_IDLE_SEC // 60} min\n"
            f"Cliente: {quien}\n\n{sale_summary(venta, int(conv_id))}"
        )
        if ok:
            await _marcar(f"sale_{conv_id}")


async def _tick() -> None:
    try:
        await check_mute()
        await check_unattended_sales()
        await check_human_abandoned()
        await check_balance()
        await check_fallback_spike()
        await daily_audit()
    except Exception as err:
        log.warning("[watchdog] tick error: %s", err)


async def _loop() -> None:
    log.info("[watchdog] iniciado (tick=%ss)", settings.watchdog_tick_seconds)
    while True:
        await _tick()
        await asyncio.sleep(settings.watchdog_tick_seconds)


def start_watchdog() -> None:
    global _task
    if not settings.watchdog_enabled:
        log.info("[watchdog] deshabilitado")
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())


def stop_watchdog() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
