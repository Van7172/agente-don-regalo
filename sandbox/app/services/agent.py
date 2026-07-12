"""
Loop agéntico adaptado al sandbox: envía por WhatsApp Cloud API y handoff vía CRM.
Soporta ejecución de tools en paralelo (excepto handoff/memoria).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crm import repository as repo
from app.services.messenger import notify_team, send_message, set_typing
from app.tools import HUMAN_HANDOFF_TOOL, MEMORY_TOOL, TOOLS, execute_tool

log = logging.getLogger(__name__)

HANDOFF_DONE = "__handoff_done__"

_HANDOFF_WAIT_MSG = (
    "¡Claro! Te conecto con un asesor de nuestro equipo 🙏 "
    "Dame un momento, en seguida continúan contigo."
)

_FILLER_BY_TOOL: dict[str, list[str]] = {
    "buscar_semantico": ["¡Genial! Déjame buscarte las mejores opciones 🎁", "¡Claro! Ya te busco algo perfecto 😍"],
    "buscar_productos": ["Un momento 😊"],
    "productos_similares": ["¡Buena elección! Te muestro otras opciones parecidas 😊"],
    "catalogo_categoria": ["¡Perfecto! Déjame mostrarte lo que tenemos 🎁"],
    "productos_por_ocasion": ["¡Qué lindo detalle! Déjame buscar algo ideal 🎁"],
    "productos_destacados": ["¡Con gusto! Déjame mostrarte lo más pedido ⭐"],
    "productos_oferta": ["¡Me encanta! Déjame buscar nuestras mejores ofertas 🔥"],
    "detalle_producto": ["Un momento, te traigo la info completa 😊"],
    "distritos_cobertura": ["Un momento 😊"],
    "metodos_pago": ["Déjame contarte las formas de pago 💳"],
    "rastrear_pedido": ["Déjame revisar el estado de tu pedido 📦"],
    "buscar_conocimiento_equipo": ["Déjame verificar eso para darte la mejor respuesta 😊"],
}

_filler_conversations: set[int] = set()


def _filler_for_tools(tool_calls: list) -> str | None:
    for call in tool_calls:
        fn = call.get("function", {}).get("name", "")
        opciones = _FILLER_BY_TOOL.get(fn)
        if opciones:
            return random.choice(opciones)
    return None


async def run_agent(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
) -> str | None:
    all_tools = list(TOOLS)
    if contact_id or use_external_crm:
        all_tools.append(MEMORY_TOOL)
    if conversation_id is not None:
        all_tools.append(HUMAN_HANDOFF_TOOL)

    filler_sent = conversation_id in _filler_conversations if conversation_id else True
    early_filler_task: asyncio.Task | None = None

    async def _send_early_filler() -> None:
        """Aviso rápido si el 1.er round de LLM tarda (tools / OpenAI)."""
        nonlocal filler_sent
        try:
            await asyncio.sleep(0.7)
            if filler_sent or conversation_id is None:
                return
            await send_message(wa_id, "Un momento, ya te ayudo 😊")
            await set_typing(conversation_id, True)
            filler_sent = True
            _filler_conversations.add(conversation_id)
            if len(_filler_conversations) > 5000:
                _filler_conversations.clear()
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("[FILLER] early failed: %s", e)

    try:
        if not filler_sent and conversation_id is not None:
            early_filler_task = asyncio.create_task(_send_early_filler())

        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(settings.max_tool_rounds):
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.openai_model,
                        "messages": messages,
                        "tools": all_tools,
                        "tool_choice": "auto",
                        "parallel_tool_calls": True,
                    },
                )
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    if early_filler_task and not early_filler_task.done():
                        early_filler_task.cancel()
                    return msg.get("content")

                messages.append(msg)

                if not filler_sent and conversation_id is not None:
                    filler = _filler_for_tools(tool_calls)
                    if filler:
                        if early_filler_task and not early_filler_task.done():
                            early_filler_task.cancel()
                        await send_message(wa_id, filler)
                        await set_typing(conversation_id, True)
                        filler_sent = True
                        _filler_conversations.add(conversation_id)
                        if len(_filler_conversations) > 5000:
                            _filler_conversations.clear()

                # Separar tools especiales vs paralelizables
                special = []
                parallel = []
                for call in tool_calls:
                    fn = call["function"]["name"]
                    if fn in ("escalar_a_humano", "guardar_datos_cliente"):
                        special.append(call)
                    else:
                        parallel.append(call)

                for call in special:
                    fn = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    log.info("[TOOL] %s args=%s", fn, args)

                    if fn == "escalar_a_humano":
                        motivo = args.get("motivo") or "no especificado"
                        log.info("[HANDOFF] conversation=%s motivo=%s", conversation_id, motivo)
                        await send_message(wa_id, _HANDOFF_WAIT_MSG)
                        if use_external_crm and conversation_id:
                            from app.crm import http_client as crm_http

                            await crm_http.set_mode(conversation_id, "HUMAN")
                        elif session and conversation_id:
                            await repo.set_human_support(session, conversation_id, True)
                            await session.commit()
                        await notify_team(
                            f"Atencion humana solicitada (conversacion {conversation_id}). Motivo: {motivo}."
                        )
                        return HANDOFF_DONE

                    if fn == "guardar_datos_cliente":
                        if use_external_crm and wa_id:
                            from app.crm import http_client as crm_http

                            patch = {
                                "name": args.get("nombre") or args.get("name"),
                                "email": args.get("email"),
                                "objetivo": args.get("objetivo") or args.get("preferencias"),
                                "situacion": args.get("situacion") or args.get("ocasion"),
                                "temperatura": args.get("temperatura"),
                                "resumen": args.get("resumen") or args.get("notas"),
                            }
                            await crm_http.put_memory(wa_id, {k: v for k, v in patch.items() if v})
                            result = json.dumps({"ok": True, "guardado": patch})
                        elif session and contact_id:
                            result = await repo.save_contact_attributes(session, contact_id, args)
                            await session.commit()
                        else:
                            result = json.dumps({"ok": False, "motivo": "sin session"})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": result,
                        })

                if parallel:
                    async def _run_one(call):
                        fn = call["function"]["name"]
                        try:
                            args = json.loads(call["function"].get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        log.info("[TOOL] %s args=%s", fn, args)
                        result = await execute_tool(fn, args)
                        return call["id"], result

                    results = await asyncio.gather(*[_run_one(c) for c in parallel])
                    for tool_call_id, result in results:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result,
                        })

            log.warning("Se alcanzó MAX_TOOL_ROUNDS sin respuesta final")
            return None
    except Exception as e:
        log.error("Error en el bucle del agente: %s", e)
        return None
    finally:
        if early_filler_task and not early_filler_task.done():
            early_filler_task.cancel()
