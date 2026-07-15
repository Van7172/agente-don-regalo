"""
Loop agéntico: ejecuta un especialista del harness contra OpenAI, envía por
WhatsApp Cloud API y hace el handoff vía CRM.

Devuelve un `AgentResult`, no un `str`: el orquestador necesita saber qué
productos citó el especialista para poder reducir el estado. Las reglas de
negocio (cuándo un handoff procede) viven en `harness/policies.py`, no aquí.
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
from app.harness.contracts import AgentResult, EscalateReason, Product, extract_products
from app.harness.policies import (
    handoff_policy,
    is_payment_reason,
    is_small_talk,
    should_discard_handoff,
)
from app.services.messenger import notify_team, send_message, set_typing
from app.tools import HUMAN_HANDOFF_TOOL, MEMORY_TOOL, TOOLS, execute_tool

log = logging.getLogger(__name__)

HANDOFF_DONE = "__handoff_done__"

# Alias privados: los tests de regresión de handoff/charla trivial apuntan aquí.
_is_small_talk = is_small_talk
_should_discard_handoff = should_discard_handoff

# Un 429 de OpenAI suele ser un pico de rate limit que se resuelve en segundos.
# Sin reintento, el bucle devolvía None y el chat quedaba aparcado en HUMAN
# para siempre por un fallo pasajero.
_LLM_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_LLM_MAX_ATTEMPTS = 4
_LLM_BACKOFF_CAP = 8.0


async def _chat_completion(client: httpx.AsyncClient, payload: dict) -> dict:
    """POST a OpenAI reintentando errores pasajeros. Lanza si no hay forma."""
    delay = 1.0

    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if r.status_code == 200:
            return r.json()

        body = r.text[:300]

        # Sin saldo no se arregla esperando: escala ya en vez de hacer aguardar al cliente.
        if "insufficient_quota" in body:
            log.error("[LLM] cuota de OpenAI agotada: %s", body)
            r.raise_for_status()

        if r.status_code not in _LLM_RETRY_STATUS or attempt == _LLM_MAX_ATTEMPTS:
            log.error("[LLM] %s definitivo (intento %s): %s", r.status_code, attempt, body)
            r.raise_for_status()

        retry_after = r.headers.get("retry-after")
        try:
            wait = float(retry_after) if retry_after else delay
        except ValueError:
            wait = delay
        wait = min(wait, _LLM_BACKOFF_CAP)

        log.warning(
            "[LLM] %s (intento %s/%s); reintento en %.1fs: %s",
            r.status_code, attempt, _LLM_MAX_ATTEMPTS, wait, body,
        )
        await asyncio.sleep(wait)
        delay = min(delay * 2, _LLM_BACKOFF_CAP)

    raise RuntimeError("unreachable")  # pragma: no cover

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


async def _say(wa_id: str, text: str, persist) -> str | None:
    """Envía por WhatsApp y deja constancia en el CRM.

    Lo que se envía sin persistir el asesor NO lo ve: el hilo del inbox queda
    con huecos respecto a lo que el cliente tiene en su WhatsApp.
    """
    wa_mid = await send_message(wa_id, text)
    if persist is not None:
        try:
            await persist(content=text, wa_message_id=wa_mid, media_url=None)
        except Exception as err:
            log.warning("[PERSIST] no se pudo guardar en el CRM: %s", err)
    return wa_mid


async def perform_handoff(
    *,
    wa_id: str,
    conversation_id: int | None,
    motivo: str,
    use_external_crm: bool = False,
    session: AsyncSession | None = None,
    persist=None,
) -> EscalateReason:
    """Cede el control a un humano DE VERDAD, no solo de palabra.

    El bot llegó a decir "te paso con un asesor, un momento" sin ejecutar nada: la
    conversación seguía en modo IA y el bot seguía respondiendo. La derivación no
    es una frase, es un cambio de estado: aviso de espera al cliente, la
    conversación pasa a HUMAN en el CRM (deja de contestar el bot) y se avisa al
    equipo. Se ejecuta en código para no depender de que el modelo llame la tool.
    """
    await _say(wa_id, _HANDOFF_WAIT_MSG, persist)
    if use_external_crm and conversation_id:
        from app.crm import http_client as crm_http

        await crm_http.set_mode(conversation_id, "HUMAN")
    elif session and conversation_id:
        await repo.set_human_support(session, conversation_id, True)
        await session.commit()
    await notify_team(
        f"Atencion humana solicitada (conversacion {conversation_id}). Motivo: {motivo}."
    )
    # El releaser exime los handoff de pago del retorno HUMAN→AI: un asesor
    # cobrando puede tardar horas en contestar.
    is_payment = is_payment_reason(motivo)
    if conversation_id is not None:
        try:
            from app.harness.state import load_state, save_state

            st = await load_state(conversation_id, wa_id=wa_id or "")
            st.handoff_reason = motivo or st.handoff_reason
            if is_payment:
                st.checkout_step = "payment"
            await save_state(conversation_id, st, wa_id=wa_id or "")
        except Exception as err:
            log.warning("[harness] no se guardó handoff_reason: %s", err)
    return EscalateReason(motivo=motivo, is_payment=is_payment)


async def run_agent(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
    tools_override: list | None = None,
    include_handoff: bool = True,
    include_memory: bool = True,
) -> str | None:
    """Fachada de compatibilidad: solo el texto. Úsala fuera del harness."""
    result = await run_specialist(
        messages,
        wa_id=wa_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        session=session,
        use_external_crm=use_external_crm,
        persist=persist,
        tools_override=tools_override,
        include_handoff=include_handoff,
        include_memory=include_memory,
    )
    if result.escalate is not None:
        return HANDOFF_DONE
    return result.user_facing


async def run_specialist(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
    tools_override: list | None = None,
    include_handoff: bool = True,
    include_memory: bool = True,
) -> AgentResult:
    """Ejecuta un especialista y devuelve lo que dijo Y lo que aprendió.

    Los `artifacts` salen de los resultados de las tools, no de la prosa de la
    respuesta: son la única fuente fiable de los ids de producto que el
    orquestador necesita para no repetirlos después.
    """
    artifacts: list[Product] = []
    seen_ids: set[int] = set()
    tools_used: list[str] = []

    def _absorb(raw_result: str) -> None:
        try:
            payload = json.loads(raw_result)
        except (TypeError, ValueError):
            return
        for product in extract_products(payload):
            if product.id_producto in seen_ids:
                continue
            seen_ids.add(product.id_producto)
            artifacts.append(product)

    if tools_override is not None:
        all_tools = list(tools_override)
    else:
        all_tools = list(TOOLS)
        if include_memory and (contact_id or use_external_crm):
            all_tools.append(MEMORY_TOOL)
        if include_handoff and conversation_id is not None:
            all_tools.append(HUMAN_HANDOFF_TOOL)

    filler_sent = conversation_id in _filler_conversations if conversation_id else True
    early_filler_task: asyncio.Task | None = None
    # Ni saludos ni cortesía merecen un "Un momento, ya te ayudo": no hay nada que buscar.
    skip_early_filler = _is_small_talk(messages)

    async def _send_early_filler() -> None:
        """Aviso rápido si el 1.er round de LLM tarda (tools / OpenAI)."""
        nonlocal filler_sent
        try:
            await asyncio.sleep(0.7)
            if filler_sent or conversation_id is None:
                return
            await _say(wa_id, "Un momento, ya te ayudo 😊", persist)
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
        # Saludos simples: ir directo a la respuesta (sin "Un momento...").
        if not filler_sent and conversation_id is not None and not skip_early_filler:
            early_filler_task = asyncio.create_task(_send_early_filler())

        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(settings.max_tool_rounds):
                payload: dict = {
                    "model": settings.openai_model,
                    "messages": messages,
                }
                if all_tools:
                    payload["tools"] = all_tools
                    payload["tool_choice"] = "auto"
                    payload["parallel_tool_calls"] = True
                # Sin tools NO se manda `tool_choice`: OpenAI rechaza con 400
                # ("tool_choice is only allowed when tools are specified") y el
                # agente devolvía None → el bot se quedaba mudo. Un agente sin
                # tools (concierge) solo tiene que redactar texto, que es justo lo
                # que hace omitir el campo.
                data = await _chat_completion(client, payload)
                msg = data["choices"][0]["message"]
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    if early_filler_task and not early_filler_task.done():
                        early_filler_task.cancel()
                    return AgentResult(
                        user_facing=msg.get("content"),
                        artifacts=artifacts,
                        tools_used=tools_used,
                    )

                messages.append(msg)

                if not filler_sent and conversation_id is not None:
                    filler = _filler_for_tools(tool_calls)
                    if filler:
                        if early_filler_task and not early_filler_task.done():
                            early_filler_task.cancel()
                        await _say(wa_id, filler, persist)
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

                        # Red de seguridad: el modelo a veces escala ventas sanas
                        # ("regalos corporativos", "2 y 3") o charla trivial.
                        decision = handoff_policy(messages)
                        if not decision.allow:
                            log.info(
                                "[HANDOFF] descartado conversation=%s motivo_modelo=%s motivo_guard=%s",
                                conversation_id,
                                motivo,
                                decision.reason[:80],
                            )
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps({
                                    "ok": False,
                                    "motivo": decision.reason,
                                }, ensure_ascii=False),
                            })
                            continue

                        log.info("[HANDOFF] conversation=%s motivo=%s", conversation_id, motivo)
                        escalate = await perform_handoff(
                            wa_id=wa_id,
                            conversation_id=conversation_id,
                            motivo=motivo,
                            use_external_crm=use_external_crm,
                            session=session,
                            persist=persist,
                        )
                        return AgentResult(
                            user_facing=None,
                            artifacts=artifacts,
                            tools_used=[*tools_used, "escalar_a_humano"],
                            escalate=escalate,
                        )

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
                        tools_used.append(fn)
                        result = await execute_tool(fn, args)
                        return call["id"], result

                    results = await asyncio.gather(*[_run_one(c) for c in parallel])
                    for tool_call_id, result in results:
                        _absorb(result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result,
                        })

            # Último intento: pedir respuesta final SIN tools (evita None → fallback).
            log.warning("Se alcanzó MAX_TOOL_ROUNDS; pidiendo respuesta final sin tools")
            data = await _chat_completion(
                client,
                {
                    "model": settings.openai_model,
                    "messages": messages + [{
                        "role": "system",
                        "content": (
                            "Debes responder YA al cliente con un mensaje útil y corto. "
                            "No llames más herramientas. Si faltan datos, pregunta uno solo."
                        ),
                    }],
                    "tool_choice": "none",
                },
            )
            if early_filler_task and not early_filler_task.done():
                early_filler_task.cancel()
            return AgentResult(
                user_facing=data["choices"][0]["message"].get("content"),
                artifacts=artifacts,
                tools_used=tools_used,
            )
    except Exception as e:
        log.error("Error en el bucle del agente: %s", e)
        return AgentResult(user_facing=None, artifacts=artifacts, tools_used=tools_used)
    finally:
        if early_filler_task and not early_filler_task.done():
            early_filler_task.cancel()
