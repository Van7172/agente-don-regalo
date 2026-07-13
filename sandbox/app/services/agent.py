"""
Loop agéntico adaptado al sandbox: envía por WhatsApp Cloud API y handoff vía CRM.
Soporta ejecución de tools en paralelo (excepto handoff/memoria).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import unicodedata

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crm import repository as repo
from app.services.messenger import notify_team, send_message, set_typing
from app.tools import HUMAN_HANDOFF_TOOL, MEMORY_TOOL, TOOLS, execute_tool

log = logging.getLogger(__name__)

HANDOFF_DONE = "__handoff_done__"

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

# Saludos cortos: no mandar filler temprano (evita "Un momento..." + saludo real).
_GREETING_RE = re.compile(
    r"^(?:"
    r"h+o+l+a+s?|holis|"
    r"buenas?|"
    r"buen[oa]s?\s+d[ií]as?|"
    r"buenas?\s+tardes?|"
    r"buenas?\s+noches?|"
    r"qu[eé]\s+tal|"
    r"c[oó]mo\s+est[aá]s?|"
    r"hey|hi|hello|saludos?"
    r")"
    r"(?:\s+(?:a\s+todos?|amigo|amiga|equipo|don\s*regalo))?$"
)

_filler_conversations: set[int] = set()


def _filler_for_tools(tool_calls: list) -> str | None:
    for call in tool_calls:
        fn = call.get("function", {}).get("name", "")
        opciones = _FILLER_BY_TOOL.get(fn)
        if opciones:
            return random.choice(opciones)
    return None


def _latest_user_text(messages: list) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("image_url", "image"):
                    return None
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
            joined = "\n".join(t for t in texts if t).strip()
            return joined or None
        return None
    return None


def _normalize_greeting_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[^\w\sáéíóúüñ]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _is_simple_greeting(messages: list) -> bool:
    """True si el último mensaje del usuario es solo un saludo breve."""
    raw = _latest_user_text(messages)
    if not raw or len(raw) > 80:
        return False
    norm = _normalize_greeting_text(raw)
    if not norm or len(norm) > 60:
        return False
    return bool(_GREETING_RE.match(norm))


# Vocabulario de cortesía: "ok gracias", "todo en orden hoy", "jaja", "👍"...
# Un mensaje formado SOLO por estas palabras no pide nada, así que no hay nada
# que escalar. Lista blanca corta a propósito: preferimos dejar pasar charla
# trivial a suprimir una escalación de verdad.
_SMALL_TALK_WORDS = frozenset(
    """
    gracias muchas mil ok oka okay okey vale listo perfecto genial excelente
    buenisimo entiendo entendido claro dale bueno buena ya ah aja si no nada
    todo bien en orden correcto estamos igualmente a ti usted de acuerdo por
    ahora hoy amigo amiga saludos chevere tranquilo tranquila
    """.split()
)


def _is_small_talk(messages: list) -> bool:
    """True si el último mensaje del usuario es cortesía o charla sin pedido."""
    raw = _latest_user_text(messages)
    if not raw or len(raw) > 80 or "?" in raw:
        return False

    if _is_simple_greeting(messages):
        return True

    norm = _normalize_greeting_text(raw)
    if not norm:
        # Se quedó vacío al normalizar: era solo emojis ("👍", "😊").
        return True

    tokens = norm.split()
    if not tokens or len(tokens) > 6:
        return False

    # "jaja", "jejeje", "jjj"... son risas, no un pedido.
    return all(
        t in _SMALL_TALK_WORDS or re.fullmatch(r"(?:ja|je|ji|ha)+|j+", t)
        for t in tokens
    )


# Si el cliente pide esto, el handoff SÍ procede aunque también diga "corporativo".
_HANDOFF_FORCE_RE = re.compile(
    r"asesor|humano|persona|atenci[oó]n\s+humana|p[aá]same\s+con|"
    r"comprobante|ya\s+pagu|transfer[ií]|yape|plin|"
    r"descuento|cancelar|modificar\s+(el\s+)?pedido|"
    r"mala\s+atenci|no\s+me\s+ayud|quiero\s+hablar\s+con",
    re.IGNORECASE,
)

# Contexto de venta en curso: el bot debe seguir preguntando, no escalar.
_SALES_CONTINUE_RE = re.compile(
    r"corporativ|empresa|b2b|mayorista|colegio|instituci|"
    r"recuerdo|exposici|fiestas?\s+patrias|patrias|"
    r"cantidad|unidades|docena|presupuesto|cotizaci|"
    r"cat[aá]logo|en\s+su\s+p[aá]gina|en\s+la\s+p[aá]gina|"
    r"desayuno|cesta|suculenta|arreglo|"
    r"\b\d+\s*(?:y|,|/|&)\s*\d+\b|\by\s*\d+\b",
    re.IGNORECASE,
)


def _should_discard_handoff(messages: list) -> bool | str:
    """
    True/motivo si hay que rechazar escalar_a_humano.
    Charla trivial O venta en curso (corporativo, catálogo, opción 2 y 3…)
    sin pedido explícito de humano/pago.
    """
    if _is_small_talk(messages):
        return (
            "El cliente no está pidiendo nada: es cortesía o charla suelta. "
            "No se escala. Responde tú, corto y cálido, y deja la puerta abierta."
        )
    raw = _latest_user_text(messages)
    if not raw:
        return False
    if _HANDOFF_FORCE_RE.search(raw):
        return False
    if _SALES_CONTINUE_RE.search(raw):
        return (
            "El cliente sigue en un flujo de venta (producto, corporativo, "
            "catálogo, campaña o eligiendo opciones). NO escalas: pregunta "
            "cantidad, presupuesto, distrito o fecha, o muestra productos con "
            "las tools. Solo escala si pide asesor, pago/comprobante, descuento "
            "o cancelación."
        )
    return False


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


async def run_agent(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
) -> str | None:
    all_tools = list(TOOLS)
    if contact_id or use_external_crm:
        all_tools.append(MEMORY_TOOL)
    if conversation_id is not None:
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
                data = await _chat_completion(
                    client,
                    {
                        "model": settings.openai_model,
                        "messages": messages,
                        "tools": all_tools,
                        "tool_choice": "auto",
                        "parallel_tool_calls": True,
                    },
                )
                msg = data["choices"][0]["message"]
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
                        discard = _should_discard_handoff(messages)
                        if discard:
                            log.info(
                                "[HANDOFF] descartado conversation=%s motivo_modelo=%s motivo_guard=%s",
                                conversation_id,
                                motivo,
                                discard[:80],
                            )
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": json.dumps({
                                    "ok": False,
                                    "motivo": discard,
                                }, ensure_ascii=False),
                            })
                            continue

                        log.info("[HANDOFF] conversation=%s motivo=%s", conversation_id, motivo)
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
            return data["choices"][0]["message"].get("content")
    except Exception as e:
        log.error("Error en el bucle del agente: %s", e)
        return None
    finally:
        if early_filler_task and not early_filler_task.done():
            early_filler_task.cancel()
