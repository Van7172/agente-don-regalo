"""
Loop agéntico: ejecuta un especialista del harness contra OpenAI, envía por
WhatsApp Cloud API y hace el handoff vía CRM.

Devuelve un `AgentResult`, no un `str`: el orquestador necesita saber qué
productos citó el especialista para poder reducir el estado. Las reglas de
protección (cuándo un handoff procede) viven en `app/guardrails`, no aquí.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crm import repository as repo
from app.harness.contracts import AgentResult, EscalateReason, Product, extract_products
from app.guardrails import (
    empty_search_is_not_a_handoff,
    handoff_policy,
    is_payment_reason,
    is_small_talk,
    protect_json_for_model,
    redact_personal_data,
    sanitize_tool_result,
    should_discard_handoff,
    validate_arguments,
)
from app.observability import (
    audit_event,
    current_agent,
    record_llm_usage,
    record_operation,
)
from app.resilience import circuit_breaker
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


def _observe_llm(
    outcome: str,
    started: float,
    attempt: int,
    *,
    error_type: str | None = None,
    label: str = "specialist",
) -> None:
    latency_ms = (time.monotonic() - started) * 1000
    # Serie separada por proveedor: si el respaldo tarda el triple, mezclarlo con
    # el primario escondería tanto su lentitud como el momento en que empezó a
    # usarse.
    record_operation(f"openai.{label}", outcome, duration_ms=latency_ms)
    audit_event(
        "openai.request",
        outcome,
        backend="openai" if label == "specialist" else "fallback",
        operation=label,
        processed_count=attempt,
        latency_ms=latency_ms,
        error_type=error_type,
    )


_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


async def _chat_completion_unprotected(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    url: str = _OPENAI_URL,
    api_key: str = "",
    label: str = "specialist",
) -> dict:
    """POST a OpenAI reintentando errores pasajeros. Lanza si no hay forma.

    `url`/`api_key` existen para el proveedor de respaldo (B5). Los valores por
    defecto son los de siempre, así que quien llame sin ellos —incluidos los
    tests— sigue hablando con OpenAI exactamente igual.
    """
    delay = 1.0
    started = time.monotonic()

    for attempt in range(1, _LLM_MAX_ATTEMPTS + 1):
        try:
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key or settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except Exception as error:
            _observe_llm(
                "error",
                started,
                attempt,
                error_type=type(error).__name__,
                label=label,
            )
            raise
        if r.status_code == 200:
            _observe_llm("ok", started, attempt, label=label)
            data = r.json()
            # Aquí y no en `_chat_completion`: esta es la única función que ve
            # la respuesta cruda con su bloque `usage`, y la capa de arriba la
            # sustituyen los tests. El agente sale del contexto, no de un
            # parámetro, para no cruzarlo por media docena de firmas.
            record_llm_usage(
                current_agent(), str(payload.get("model") or ""), data
            )
            return data

        body = r.text[:300]

        # Sin saldo no se arregla esperando: escala ya en vez de hacer aguardar al cliente.
        if "insufficient_quota" in body:
            _observe_llm("quota", started, attempt, label=label)
            log.error("[LLM] cuota de OpenAI agotada")
            r.raise_for_status()

        if r.status_code not in _LLM_RETRY_STATUS or attempt == _LLM_MAX_ATTEMPTS:
            _observe_llm("error", started, attempt, label=label)
            log.error("[LLM] %s definitivo (intento %s)", r.status_code, attempt)
            r.raise_for_status()

        retry_after = r.headers.get("retry-after")
        try:
            wait = float(retry_after) if retry_after else delay
        except ValueError:
            wait = delay
        wait = min(wait, _LLM_BACKOFF_CAP)

        log.warning(
            "[LLM] %s (intento %s/%s); reintento en %.1fs",
            r.status_code, attempt, _LLM_MAX_ATTEMPTS, wait,
        )
        await asyncio.sleep(wait)
        delay = min(delay * 2, _LLM_BACKOFF_CAP)

    _observe_llm("error", started, _LLM_MAX_ATTEMPTS, label=label)
    raise RuntimeError("unreachable")  # pragma: no cover


def _fallback_configured() -> bool:
    return bool(
        (settings.llm_fallback_base_url or "").strip()
        and (settings.llm_fallback_api_key or "").strip()
    )


async def _chat_completion_fallback(
    client: httpx.AsyncClient, payload: dict
) -> dict | None:
    """Segundo proveedor cuando el primario no responde. `None` si no hay.

    Tiene su PROPIO circuit breaker: si el fallback también está caído, no se
    intenta en cada turno — sumaría segundos de espera a un cliente que ya no va
    a recibir respuesta por esta vía, y lo que toca entonces es degradar rápido.
    """
    if not _fallback_configured():
        return None

    modelo = (settings.llm_fallback_model or "").strip() or str(
        payload.get("model") or ""
    )
    alterno = {**payload, "model": modelo}
    url = f"{settings.llm_fallback_base_url}/chat/completions"

    data = await circuit_breaker("openai.fallback").call(
        lambda: _chat_completion_unprotected(
            client,
            alterno,
            url=url,
            api_key=settings.llm_fallback_api_key,
            label="fallback",
        )
    )
    return data


async def _chat_completion(client: httpx.AsyncClient, payload: dict) -> dict:
    """Una solicitud lógica (incluidos sus reintentos) cuenta como un intento."""
    try:
        return await circuit_breaker("openai.specialist").call(
            lambda: _chat_completion_unprotected(client, payload)
        )
    except Exception as error_primario:
        # Incluye el caso "circuito abierto": ahí el primario ni se intenta, y es
        # justo cuando el secundario más falta hace.
        try:
            alterno = await _chat_completion_fallback(client, payload)
        except Exception as error_alterno:
            log.error(
                "[LLM] el proveedor de respaldo también falló: %s",
                type(error_alterno).__name__,
            )
            raise error_primario from error_alterno
        if alterno is None:
            raise
        log.warning(
            "[LLM] primario caído (%s); respondido por el proveedor de respaldo",
            type(error_primario).__name__,
        )
        record_operation("openai.provider", "fallback")
        audit_event(
            "openai.provider",
            "fallback",
            backend="fallback",
            error_type=type(error_primario).__name__,
        )
        return alterno

_HANDOFF_WAIT_MSG = (
    "¡Claro! Te conecto con un asesor de nuestro equipo 🙏 "
    "Dame un momento, en seguida continúan contigo."
)

# La cesión del chat no salió y el turno no tiene nada más que decir. NO se
# vuelve a nombrar a un asesor: prometer dos veces lo que no llegó la primera es
# peor que no prometerlo. El bot sigue atendiendo, que es lo que puede hacer.
HANDOFF_FAILED_MSG = (
    "Perdona la demora 🙏 Sigo por acá contigo: cuéntame en una línea qué "
    "necesitas y lo vemos al toque."
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


async def _cede_a_humano(
    conversation_id: int | None,
    *,
    use_external_crm: bool,
    session: AsyncSession | None,
) -> bool:
    """Pasa la conversación a HUMAN. `False` si NO se pudo — y entonces no se
    promete nada.

    Sin `conversation_id` tampoco se puede: no hay a qué chat cambiarle el modo.
    Antes ese caso enviaba el aviso igual y seguía como si nada.
    """
    if conversation_id is None:
        return False
    try:
        if use_external_crm:
            from app.crm import http_client as crm_http

            await crm_http.set_mode(conversation_id, "HUMAN")
            return True
        if session is not None:
            await repo.set_human_support(session, conversation_id, True)
            await session.commit()
            return True
    except Exception as err:
        log.error(
            "[HANDOFF] conversation=%s falló el cambio a HUMAN: %s: %s",
            conversation_id,
            type(err).__name__,
            err,
        )
        return False
    return False


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

    Fiestas Patrias 28–29/07/2026: no hay asesores. Se avisa y el bot sigue;
    no se pone HUMAN ni se encola AYUDA.
    """
    is_payment = is_payment_reason(motivo)

    # Acceso por módulo (no `from … import fn`): así los tests pueden
    # monkeypatchear `holidays.staff_offline` sin pelearse con el binding.
    from app.harness import holidays as holidays_mod

    if holidays_mod.staff_offline():
        await _say(
            wa_id,
            holidays_mod.staff_offline_reply(for_payment=is_payment),
            persist,
        )
        log.info(
            "[HANDOFF] feriado sin personal; no se cede chat conv=%s motivo=%s",
            conversation_id,
            (motivo or "")[:120],
        )
        # Sin HUMAN el bot sigue: si el cierre quedó en `payment`, el próximo
        # mensaje volvería a escalar. Marcamos done para no repetir el aviso.
        if is_payment and conversation_id is not None:
            try:
                from app.harness.state import load_state, save_state

                st = await load_state(conversation_id, wa_id=wa_id or "")
                base = st.to_dict()
                st.checkout_step = "done"
                st.handoff_reason = motivo or st.handoff_reason
                await save_state(conversation_id, st, wa_id=wa_id or "", base=base)
            except Exception as err:
                log.warning("[harness] no se cerró checkout en feriado: %s", err)
        return EscalateReason(motivo=motivo, is_payment=is_payment)

    # PRIMERO se cede, DESPUÉS se promete. Al revés —que es como estaba— el
    # `_say` salía siempre y el cambio de modo era condicional y sin `try`: si
    # el CRM no respondía, el cliente se quedaba con "en seguida continúan
    # contigo" y la conversación seguía en IA. En una captura real el bot mandó
    # esa frase y en el mensaje siguiente estaba preguntando otra vez, con el
    # CRM marcando "Don Regalo escuchando". Misma regla que el claim del outbox:
    # se reclama antes de hablar.
    if not await _cede_a_humano(
        conversation_id, use_external_crm=use_external_crm, session=session
    ):
        log.error(
            "[HANDOFF] conversation=%s no se pudo ceder el chat; el bot sigue",
            conversation_id,
        )
        record_operation("handoff", "cede_failed")
        audit_event("handoff", "cede_failed", conversation_id=conversation_id)
        # Se avisa igual: alguien tiene que mirarlo aunque el chat siga en IA,
        # y el asesor siempre puede entrar con "Tomar conversación".
        await notify_team(
            f"Handoff FALLIDO (conversacion {conversation_id}): el chat sigue en "
            f"modo IA. Motivo: {motivo}."
        )
        return EscalateReason(motivo=motivo, is_payment=is_payment, ceded=False)

    await _say(wa_id, _HANDOFF_WAIT_MSG, persist)
    await notify_team(
        f"Atencion humana solicitada (conversacion {conversation_id}). Motivo: {motivo}."
    )
    # El releaser exime los handoff de pago del retorno HUMAN→AI: un asesor
    # cobrando puede tardar horas en contestar.
    if conversation_id is not None:
        try:
            from app.harness.state import load_state, save_state

            st = await load_state(conversation_id, wa_id=wa_id or "")
            # `base` es la foto de lo que acabamos de leer: esto corre A MITAD
            # del turno, y al terminar `master` guarda su propia copia —cargada
            # antes de este handoff—. Escribiendo solo el delta, ese guardado
            # final ya no borra lo de aquí.
            base = st.to_dict()
            st.handoff_reason = motivo or st.handoff_reason
            # Ancla del releaser: mientras el asesor no escriba, esto es lo único
            # que permite medir cuánto lleva el chat en sus manos.
            st.handoff_at = time.time()
            if is_payment:
                st.checkout_step = "payment"
            await save_state(conversation_id, st, wa_id=wa_id or "", base=base)
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
    model: str | None = None,
    max_tool_rounds: int | None = None,
    max_tool_calls: int | None = None,
    parallel_tool_calls: bool = True,
) -> AgentResult:
    """Ejecuta un especialista y devuelve lo que dijo Y lo que aprendió.

    Los `artifacts` salen de los resultados de las tools, no de la prosa de la
    respuesta: son la única fuente fiable de los ids de producto que el
    orquestador necesita para no repetirlos después.
    """
    artifacts: list[Product] = []
    seen_ids: set[int] = set()
    tools_used: list[str] = []
    tool_calls_executed = 0
    model_name = model or settings.openai_model
    round_limit = (
        settings.max_tool_rounds
        if max_tool_rounds is None
        else max(1, max_tool_rounds)
    )

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
    allowed_tool_names = {
        str(tool.get("function", {}).get("name") or "")
        for tool in all_tools
        if isinstance(tool, dict)
    }
    tool_schemas = {
        str(tool.get("function", {}).get("name") or ""): {
            **(tool.get("function", {}).get("parameters") or {"type": "object"}),
            "additionalProperties": False,
        }
        for tool in all_tools
        if isinstance(tool, dict)
    }

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
            for _ in range(round_limit):
                payload: dict = {
                    "model": model_name,
                    "messages": messages,
                }
                budget_available = (
                    max_tool_calls is None
                    or tool_calls_executed < max_tool_calls
                )
                round_tools = all_tools if budget_available else []
                if round_tools:
                    payload["tools"] = round_tools
                    payload["tool_choice"] = "auto"
                    payload["parallel_tool_calls"] = parallel_tool_calls
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

                # Defensa en profundidad: aunque la API solo recibe el toolset del
                # especialista, nunca confiamos en que una llamada devuelta por el
                # modelo esté autorizada. Toda llamada necesita pertenecer a la
                # lista enviada en ESTE round.
                authorized_calls = []
                for call in tool_calls:
                    fn = str((call.get("function") or {}).get("name") or "")
                    if fn in allowed_tool_names:
                        authorized_calls.append(call)
                        continue
                    record_operation("guardrail.tool_authorization", "blocked")
                    audit_event(
                        "guardrail.tool_authorization",
                        "blocked",
                        conversation_id=conversation_id,
                        tool=fn or "unknown",
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "blocked": True,
                                    "error": "herramienta no autorizada para este especialista",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                tool_calls = authorized_calls
                if not tool_calls:
                    continue

                # Los argumentos del modelo son entrada no confiable. El esquema
                # publicado en este mismo round es un contrato cerrado: JSON
                # objeto, campos requeridos, tipos exactos y sin extras.
                validated_args_by_id: dict[str, dict] = {}
                validated_calls = []
                for call in tool_calls:
                    fn = str((call.get("function") or {}).get("name") or "")
                    try:
                        args = json.loads(
                            (call.get("function") or {}).get("arguments") or "{}"
                        )
                    except (json.JSONDecodeError, TypeError):
                        args = None
                    validation = validate_arguments(args, tool_schemas[fn])
                    if validation.valid:
                        validated_args_by_id[str(call.get("id") or "")] = args
                        validated_calls.append(call)
                        continue
                    record_operation("guardrail.tool_parameters", "blocked")
                    audit_event(
                        "guardrail.tool_parameters",
                        "blocked",
                        conversation_id=conversation_id,
                        tool=fn,
                        violation_count=len(validation.errors),
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "blocked": True,
                                    "error": "parámetros de herramienta inválidos",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                tool_calls = validated_calls
                if not tool_calls:
                    continue

                # Presupuesto total del turno, no solo de esta ronda. Para
                # catálogo vale uno: después del primer resultado el siguiente
                # completion ya no recibe schemas de herramientas.
                if max_tool_calls is not None:
                    remaining = max(0, max_tool_calls - tool_calls_executed)
                    allowed_by_budget = tool_calls[:remaining]
                    for call in tool_calls[remaining:]:
                        fn = str((call.get("function") or {}).get("name") or "")
                        record_operation("guardrail.tool_budget", "blocked")
                        audit_event(
                            "guardrail.tool_budget",
                            "blocked",
                            conversation_id=conversation_id,
                            agent=current_agent(),
                            tool=fn or "unknown",
                            reason="max_tool_calls",
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id"),
                                "content": json.dumps(
                                    {
                                        "ok": False,
                                        "blocked": True,
                                        "error": "presupuesto de herramientas agotado",
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    tool_calls = allowed_by_budget
                    if not tool_calls:
                        continue

                tool_calls_executed += len(tool_calls)

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
                    args = validated_args_by_id[str(call.get("id") or "")]
                    log.info("[TOOL] %s arg_keys=%s", fn, sorted(args))

                    if fn == "escalar_a_humano":
                        motivo = args.get("motivo") or "no especificado"

                        # Red de seguridad: el modelo a veces escala ventas sanas
                        # ("regalos corporativos", "2 y 3") o charla trivial.
                        # Y a veces escala por rendición: buscó, no encontró y
                        # cedió el chat por un producto que sí estaba en venta.
                        decision = handoff_policy(messages)
                        if decision.allow:
                            decision = empty_search_is_not_a_handoff(
                                messages,
                                tools_used=tools_used,
                                found_products=bool(artifacts),
                            )
                        if not decision.allow:
                            log.info(
                                "[HANDOFF] descartado conversation=%s",
                                conversation_id,
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

                        log.info("[HANDOFF] conversation=%s ejecutado", conversation_id)
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
                        if isinstance(args.get("nota"), str):
                            privacy = redact_personal_data(args["nota"])
                            args["nota"] = privacy.value
                            if privacy.redacted_count:
                                record_operation("guardrail.personal_data", "redacted")
                                audit_event(
                                    "guardrail.personal_data",
                                    "redacted",
                                    conversation_id=conversation_id,
                                    tool=fn,
                                    operation="memory_write",
                                    violation_count=privacy.redacted_count,
                                )
                        safe_args_json, removed = sanitize_tool_result(
                            json.dumps(args, ensure_ascii=False)
                        )
                        args = json.loads(safe_args_json)
                        if removed:
                            record_operation("guardrail.tool_input", "sanitized")
                            audit_event(
                                "guardrail.tool_input",
                                "sanitized",
                                conversation_id=conversation_id,
                                tool=fn,
                                violation_count=removed,
                            )
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
                        result, removed = sanitize_tool_result(result)
                        if removed:
                            record_operation("guardrail.tool_output", "sanitized")
                            audit_event(
                                "guardrail.tool_output",
                                "sanitized",
                                conversation_id=conversation_id,
                                tool=fn,
                                violation_count=removed,
                            )
                        privacy = protect_json_for_model(result)
                        result = privacy.value
                        if privacy.redacted_count:
                            record_operation("guardrail.personal_data", "redacted")
                            audit_event(
                                "guardrail.personal_data",
                                "redacted",
                                conversation_id=conversation_id,
                                tool=fn,
                                operation="tool_output",
                                violation_count=privacy.redacted_count,
                            )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": result,
                        })

                if parallel:
                    async def _run_one(call):
                        fn = call["function"]["name"]
                        args = validated_args_by_id[str(call.get("id") or "")]
                        log.info("[TOOL] %s arg_keys=%s", fn, sorted(args))
                        tools_used.append(fn)
                        result = await execute_tool(fn, args)
                        result, removed = sanitize_tool_result(result)
                        if removed:
                            record_operation("guardrail.tool_output", "sanitized")
                            audit_event(
                                "guardrail.tool_output",
                                "sanitized",
                                conversation_id=conversation_id,
                                tool=fn,
                                violation_count=removed,
                            )
                        privacy = protect_json_for_model(result)
                        result = privacy.value
                        if privacy.redacted_count:
                            record_operation("guardrail.personal_data", "redacted")
                            audit_event(
                                "guardrail.personal_data",
                                "redacted",
                                conversation_id=conversation_id,
                                tool=fn,
                                operation="tool_output",
                                violation_count=privacy.redacted_count,
                            )
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
                    "model": model_name,
                    "messages": messages + [{
                        "role": "system",
                        "content": (
                            "Debes responder YA al cliente con un mensaje útil y corto. "
                            "No llames más herramientas. Si faltan datos, pregunta uno solo."
                        ),
                    }],
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
