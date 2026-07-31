"""Orquestador del harness.

Un turno = percibir → clasificar → delegar → reducir → persistir.

Dos reglas que lo definen:

1. **El orquestador no habla con el cliente.** Todo texto de cara al cliente sale
   de un especialista (`registry.AGENTS`), incluidos los saludos, que atiende el
   `concierge`. Por eso su propio prompt no lleva ni identidad ni estilo.
2. **El estado se reduce desde `AgentResult`,** nunca desde la prosa de la
   respuesta. Los ids de producto vienen de los resultados de las tools.
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.guardrails import (
    HANDOFF_RULES,
    SAFE_INJECTION_REPLY,
    dedupe_artifacts,
    detect_prompt_injection,
    guard_reply,
    handoff_policy,
    latest_user_text,
    minimize_historical_messages,
    sanitize_messages_for_model,
    sanitize_reply,
)
from app.harness.checkout import (
    advance_checkout,
    resolve_chosen_product,
    wants_checkout,
)
from app.harness.contracts import (
    AgentResult,
    Product,
    Turn,
    extract_products,
)
from app.harness.coverage import resolve_coverage
from app.harness.orders import create_from_state as create_temporal_order
from app.harness.quoting import split_quote
from app.harness.registry import spec_for
from app.harness.sale import announce as announce_sale
from app.harness.render import render_product_list
from app.harness.router import classify
from app.harness.state import ConversationState, load_state, save_state
from app.harness.stock import is_available, unavailable_message
from app.harness.taxonomy import (
    MAX_MENU_DEPTH,
    _MENU_LINE as _taxonomy_menu_line,
    as_state,
    looks_like_numbered_menu,
    match_category,
    parse_navegacion,
    render_menu,
    resolve_option,
    resolve_options,
)
from app.harness.trace import Trace
from app.observability import (
    agent_context,
    audit_event,
    collect_turn_usage,
    current_turn_usage,
    record_operation,
)
from app.prompts.compose import build_system, prompt_version
from app.prompts.playbooks import WELCOME
from app.services.agent import (
    HANDOFF_DONE,
    HANDOFF_FAILED_MSG,
    perform_handoff,
    run_specialist,
)
from app.tools.executor import execute_tool

log = logging.getLogger(__name__)

# Alias de compatibilidad para extensiones/tests anteriores a `app.guardrails`.
_degrade_unsafe_reply = sanitize_reply


# El bot OFRECIENDO un asesor: una pregunta que menciona a una persona del equipo
# ("¿Quieres que consulte con un asesor?", "¿te paso con un ejecutivo?"). Si el
# cliente dice "sí" a esto, está aceptando la derivación.
_ADVISOR_RE = re.compile(
    r"\b(asesor\w*|ejecutiv\w*|human[oa]s?|una persona|del equipo)\b", re.I
)

def _offers_handoff(reply: str | None) -> bool:
    """El bot ofrece un asesor y espera respuesta: un "sí" es aceptar la derivación.

    Antes se exigía que "asesor" cayera DENTRO de los ¿…?, pero el modelo lo deja en
    la frase anterior ("Puedo confirmarlo con un asesor. ¿Deseas que lo consulte
    ahora?") y entonces el "Si" del cliente no derivaba.
    """
    return bool(reply and _ADVISOR_RE.search(reply) and "?" in reply)

def _caption_of(messages: list) -> str | None:
    """Texto que acompaña a una imagen (`latest_user_text` lo descarta a propósito).

    Sirve para enrutar: un "ya pagué" pegado a una foto debe seguir escalando, y un
    "quisiera" pegado a la captura de un producto no debe perderse.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            texts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return "\n".join(t for t in texts if t).strip() or None
        return None
    return None


def perceive(messages: list) -> Turn:
    """Qué nos llega del cliente en este turno.

    La cita se separa AQUÍ, en la frontera: a partir de este punto `turn.text`
    son las palabras del cliente y nada más. El marcador que la trae es contexto
    del sistema, y cuando viajaba dentro de `text` el router enrutaba por
    palabras que el cliente no había escrito y cobertura acabó devolviéndole el
    marcador entero (ver `harness.quoting`).
    """
    text = latest_user_text(messages)
    has_media = text is None
    # Con imagen, `text` viene None; conservamos el caption para poder enrutar.
    raw = (text if not has_media else _caption_of(messages)) or ""
    own, quoted = split_quote(raw)
    return Turn(text=own, quoted=quoted, has_media=has_media, messages=messages)


# Turnos de descubrimiento seguidos sin enseñar nada antes de dejar de preguntar
# y mostrar productos. Tres: con Lichi habría saltado en la tercera pregunta, en
# vez de en la décima.
MAX_TURNS_WITHOUT_PRODUCTS = 3

# Intents donde "enseñar productos" ES la respuesta correcta. Cobertura, cierre,
# políticas y derivación quedan fuera a propósito: ahí preguntar no es dar
# largas, y soltarle un listado a quien pregunta por el Yape sería peor que el
# problema que esto arregla.
_DISCOVERY_INTENTS = frozenset({"greet", "small_talk", "catalog_search", "product_detail"})


def _reduce(
    state: ConversationState, result: AgentResult, *, intent: str = ""
) -> ConversationState:
    """Aplica al estado lo que el especialista aprendió."""
    if result.state_patch:
        state.patch(result.state_patch)

    # ¿Este turno el cliente vio algo? Se cuenta ANTES de mirar los artifacts
    # para que el reset de abajo mande cuando los hay.
    if intent in _DISCOVERY_INTENTS and not result.artifacts and result.escalate is None:
        state.turns_without_products += 1
    elif result.artifacts or result.escalate is not None:
        state.turns_without_products = 0

    if result.artifacts:
        state.patch({
            "shown_product_ids": [p.id_producto for p in result.artifacts],
            "recent_products": [
                {"id_producto": p.id_producto, "nombre": p.nombre}
                for p in result.artifacts
            ],
            # Ya hay productos en pantalla: el menú anterior dejó de estar
            # vigente y su numeración ahora es la de los productos. Sin esto,
            # un "2" sobre el listado se resolvería contra el menú viejo.
            "recent_options": [],
            "menu_depth": 0,
        })

    if result.escalate is not None:
        state.handoff_reason = result.escalate.motivo or state.handoff_reason
        if result.escalate.is_payment:
            state.checkout_step = "payment"

    return state


async def run_master(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
) -> str | None:
    """Un turno, con su gasto de LLM contabilizado aparte.

    El contador va aquí fuera y no dentro: un turno hace varias llamadas al
    modelo (rondas de tools, respuesta final sin tools, y el router antes de
    todo), y lo que se quiere saber es lo que costó EL TURNO. Es un `ContextVar`,
    así que dos conversaciones en vuelo a la vez no se mezclan las cuentas.
    """
    with collect_turn_usage():
        return await _run_master(
            messages,
            wa_id=wa_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            session=session,
            use_external_crm=use_external_crm,
            persist=persist,
        )


async def _run_master(
    messages: list,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
) -> str | None:
    turn = perceive(messages)

    # La entrada se evalúa antes del router, el LLM y cualquier herramienta.
    # Solo se inspecciona el turno actual: los ataques antiguos se sanean abajo,
    # pero no pueden dejar una conversación bloqueada para siempre.
    input_guard = detect_prompt_injection(turn.text)
    if input_guard.blocked:
        record_operation("guardrail.input", "blocked")
        audit_event(
            "guardrail.input",
            "blocked",
            conversation_id=conversation_id,
            risk_level=input_guard.risk,
            risk_score=input_guard.score,
            rule_count=len(input_guard.findings),
        )
        trace = Trace(
            conversation_id=conversation_id,
            intent="security",
            agent="input_guardrail",
            router="guardrail",
            user_text=turn.text,
            violations=[
                f"prompt_injection:{rule}" for rule in input_guard.rules
            ],
        )
        trace.with_usage(current_turn_usage()).done().emit()
        return SAFE_INJECTION_REPLY

    # Un ataque bloqueado ya quedó persistido en el CRM como mensaje de usuario.
    # En turnos posteriores no se reenvía al modelo: se sustituye por un marcador.
    safe_messages, removed = sanitize_messages_for_model(turn.messages)
    if removed:
        record_operation("guardrail.history", "sanitized")
        audit_event(
            "guardrail.history",
            "sanitized",
            conversation_id=conversation_id,
            violation_count=removed,
        )
    safe_messages, redacted_personal_data = minimize_historical_messages(safe_messages)
    if redacted_personal_data:
        record_operation("guardrail.personal_data", "redacted")
        audit_event(
            "guardrail.personal_data",
            "redacted",
            conversation_id=conversation_id,
            operation="historical_messages",
            violation_count=redacted_personal_data,
        )
    turn.messages = safe_messages

    state = (
        await load_state(conversation_id, wa_id=wa_id)
        if conversation_id is not None
        else ConversationState()
    )
    # Foto del estado tal y como se cargó. El turno tarda segundos (el LLM está
    # en medio) y en ese hueco escriben el releaser y el propio handoff: al
    # guardar hay que aplicar SOLO lo que cambió este turno, no el documento
    # entero. Sin esto, `perform_handoff` guardaba `handoff_at` a mitad del turno
    # y el guardado final lo borraba con el valor viejo.
    state_base = state.to_dict()

    classification = await classify(
        turn.text, state, has_media=turn.has_media, quoted=turn.quoted
    )
    intent = classification.intent
    prev_intent = state.intent_last  # antes de sobrescribir: ¿venía de una derivación?
    state.intent_last = intent

    trace = Trace(
        conversation_id=conversation_id,
        intent=intent,
        agent=spec_for(intent).name,
        confidence=classification.confidence,
        router=classification.source,
        prompt_version=prompt_version(spec_for(intent)),
        checkout_step=state.checkout_step,
        user_text=turn.text,
    )

    result = await _handle(
        intent,
        turn,
        state,
        prev_intent=prev_intent,
        wa_id=wa_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        session=session,
        use_external_crm=use_external_crm,
        persist=persist,
    )

    # La barrera de salida corre ANTES de reducir y antes de enviar al cliente.
    # `user_text` son las palabras del cliente en ESTE turno (no la cita, que es
    # contexto del sistema). La barrera lo necesita para saber que el teléfono
    # que acaba de aparecer en la respuesta lo escribió él hace un segundo: corre
    # antes de reducir el estado, así que ese dato todavía no está en el pedido.
    # Un menú es "nuestro" si el turno dejó opciones en el estado: eso solo lo
    # hacen `_answer_menu` y `_own_the_menu`. Si la respuesta trae una lista
    # numerada y nadie registró sus opciones, la escribió el modelo y nadie la
    # reescribió — que es como salieron los nueve submenús inventados.
    guarded = guard_reply(
        result.user_facing,
        state=state,
        artifacts=result.artifacts,
        user_text=turn.text,
        tools_used=result.tools_used,
        agent=spec_for(intent).name,
        menu_owned=bool(result.state_patch.get("recent_options")),
    )
    violations = list(guarded.violations)

    if guarded.blocked:
        log.warning(
            "[GUARDRAIL] conversation=%s respuesta degradada por %s",
            conversation_id,
            [str(v) for v in violations],
        )
        result.user_facing = guarded.reply

    # Un marcador interno en la respuesta significa que perdimos el hilo del
    # turno: la clienta que preguntaba qué venía dentro de la canasta recibió el
    # marcador de la cita dentro de un "No ubico … en nuestra lista". El cliente
    # no tiene por qué leer nuestros fallos: esto se cede a un humano. No es
    # venta (no hay pedido temporal ni verde en el CRM), es rescate.
    rotas = {v.rule for v in violations} & HANDOFF_RULES
    if rotas and result.escalate is None and conversation_id is not None:
        log.error(
            "[GUARDRAIL] conversation=%s respuesta comprometida (%s); se deriva",
            conversation_id,
            sorted(rotas),
        )
        unsupported = any(rule.startswith("unsupported_") for rule in rotas)
        record_operation(
            "guardrail.output",
            "unsupported_capability" if unsupported else "internal_leak",
        )
        result = AgentResult(
            user_facing=None,
            artifacts=result.artifacts,
            tools_used=result.tools_used,
            state_patch=result.state_patch,
            escalate=await perform_handoff(
                wa_id=wa_id,
                conversation_id=conversation_id,
                motivo=(
                    "el bot prometió una acción que no puede ejecutar"
                    if unsupported
                    else "fallo técnico del bot: la respuesta llevaba contexto interno"
                ),
                use_external_crm=use_external_crm,
                session=session,
                persist=persist,
            ),
        )

    state = _reduce(state, result, intent=intent)

    # ¿Este turno el bot ofreció un asesor? Si el cliente responde "sí", el router
    # sabrá que está aceptando la derivación y no una charla. Se recalcula cada
    # turno, así que se apaga solo en cuanto el bot deja de ofrecerlo.
    state.handoff_offered = _offers_handoff(result.user_facing)

    trace.tools = result.tools_used
    trace.product_ids = result.product_ids
    trace.escalated = result.escalate is not None
    trace.handoff_reason = result.escalate.motivo if result.escalate else ""
    trace.violations = [str(v) for v in violations]
    trace.state_patch = result.state_patch
    trace.with_usage(current_turn_usage()).done().emit()

    if conversation_id is not None:
        await save_state(conversation_id, state, wa_id=wa_id, base=state_base)

    if result.escalate is not None:
        # `HANDOFF_DONE` significa "ya se habló y ahora manda un humano". Si la
        # cesión falló, ninguna de las dos cosas es cierta: nadie va a entrar y
        # este turno no le dijo nada al cliente. Callarse ahí es dejarlo
        # esperando a un asesor que no existe, así que el bot sigue él.
        if not result.escalate.ceded and not result.user_facing:
            log.warning(
                "[HANDOFF] conversation=%s cesión fallida; responde el bot",
                conversation_id,
            )
            return HANDOFF_FAILED_MSG
        return HANDOFF_DONE
    return result.user_facing


def is_first_contact(state: ConversationState, messages: list) -> bool:
    """¿Hay que presentarse?

    Manda el estado (`presented`), no el historial: la ventana de historial se
    recorta a las últimas N horas, así que en un chat que ya existía el bot nunca
    llegaba a presentarse — soltaba un "¡Hola! ¿En qué te ayudo?" genérico.
    """
    if state.presented:
        return False
    return not any(m.get("role") == "assistant" for m in messages)


async def _handle(
    intent: str, turn: Turn, state: ConversationState, *, prev_intent: str = "", **ctx
) -> AgentResult:
    """Enruta el turno al especialista o a la máquina de estados que le toca."""

    # ── Primer saludo: presentación determinista ──────────────────
    if intent == "greet" and is_first_contact(state, turn.messages):
        return AgentResult(user_facing=WELCOME, state_patch={"presented": True})

    # ── Derivación: determinista, sin LLM ─────────────────────────
    # El bot decía "te paso con un asesor, un momento" y no cedía el control: la
    # frase la escribía un modelo que nunca llamaba `escalar_a_humano`, o el turno
    # caía en `concierge` (sin esa tool). La derivación no es una frase, es un
    # cambio de estado, así que la ejecuta el código, no el modelo.
    if intent == "escalate":
        return await _handle_escalate(turn, state, prev_intent=prev_intent, **ctx)

    # ── Cobertura: determinista, sin LLM ──────────────────────────
    if intent == "coverage":
        raw = await resolve_coverage(turn.text, state)
        return AgentResult(
            user_facing=raw.get("user_facing") or raw.get("structured", {}).get("ask"),
            state_patch=raw.get("state_patch") or {},
            tools_used=["distritos_cobertura"],
        )

    # ── Cierre: máquina de estados, sin LLM ───────────────────────
    if intent == "checkout" or (
        wants_checkout(turn.text) and state.checkout_step in ("idle", "")
    ):
        return await _handle_checkout(turn, state, **ctx)

    # ── Detalle: el contenido se trae en código, no se le pide al modelo ──
    if intent == "product_detail":
        return await _handle_detail(turn, state, **ctx)

    # ── ¿Responde a un menú NUESTRO? Se resuelve en código, venga el intent que
    #    venga. Esto colgaba de `intent == "catalog_search"`, y ahí estaba el
    #    agujero: el router mandaba un "4" pelado a `small_talk` → concierge, que
    #    no tiene tools de catálogo, así que el turno nunca llegaba hasta aquí y
    #    lo contestaba un modelo sin ninguna disciplina de menú. La numeración la
    #    escribió el código; resolverla también le toca al código, y no depende de
    #    que el router acierte la etiqueta.
    #
    #    Va después de escalate/cobertura/cierre/detalle a propósito: esos son
    #    dueños del turno cuando aplican. `_answer_menu` devuelve `None` si no hay
    #    menú vivo o si no está claro a qué opción se refiere.
    respondido = await _answer_menu(turn, state)
    if respondido is not None:
        return respondido

    # ── Se acabaron las preguntas: productos ──────────────────────
    if intent in _DISCOVERY_INTENTS and state.turns_without_products >= MAX_TURNS_WITHOUT_PRODUCTS:
        rescate = await _show_something(state, **ctx)
        if rescate is not None:
            return rescate

    # ── Resto: especialista LLM con toolset acotado ───────────────
    return await _run_specialty(intent, turn, state, **ctx)


async def _show_something(state: ConversationState, **ctx) -> AgentResult | None:
    """Deja de preguntar y enseña algo. El `step_retries` de la venta.

    El cierre ya aprendió esto: un paso que no entiende y responde lo mismo lo
    hace para siempre, y una clienta se fue tras cuatro "No pude confirmar esa
    fecha". El descubrimiento tenía el mismo agujero sin el mismo freno — Lichi
    pidió "los modelos" cuatro veces en veinte minutos y el bot le contestó con
    nueve menús.

    Lo más pedido es la respuesta honesta cuando no sabemos la categoría: el
    cliente ve precios y fotos reales, y desde ahí se puede seguir. Si ni eso
    sale, el problema es nuestro (o de la API) y lo coge un humano: seguir
    preguntando es exactamente lo que no funcionó.
    """
    productos = await _destacados()
    if productos:
        return AgentResult(
            user_facing=compose_product_reply(
                "Mejor te enseño 🎁 Esto es lo que más nos piden:", productos
            ),
            artifacts=productos,
            state_patch={"recent_options": [], "menu_depth": 0},
        )

    conversation_id = ctx.get("conversation_id")
    if conversation_id is None:
        return None
    log.warning(
        "[catalog] conversation=%s %s turnos sin productos y destacados vacío; se deriva",
        conversation_id,
        state.turns_without_products,
    )
    return AgentResult(
        user_facing=None,
        escalate=await perform_handoff(
            wa_id=ctx.get("wa_id") or "",
            conversation_id=conversation_id,
            motivo="el bot lleva varios turnos sin poder mostrar productos",
            use_external_crm=ctx.get("use_external_crm", False),
            session=ctx.get("session"),
            persist=ctx.get("persist"),
        ),
    )


async def _destacados() -> list[Product]:
    """Los más pedidos, para cuando no sabemos qué categoría quiere."""
    try:
        payload = json.loads(await execute_tool("productos_destacados", {}))
    except Exception as err:
        log.warning("[catalog] no pude traer los destacados: %s", err)
        return []
    return extract_products(payload)


async def _answer_menu(turn: Turn, state: ConversationState) -> AgentResult | None:
    """El cliente eligió una opción del menú que le ofrecimos. Sin LLM.

    El modelo no puede resolver esto de forma fiable porque el menú lo escribió
    él: a Yudith le ofreció siete tipos de planta (existen tres) y, al elegir
    uno, seis productos inventados. Si la numeración la pone el código, el "7"
    del cliente es un slug, y con un slug ya no hay nada que preguntar.

    Devuelve `None` si esto no aplica (no había menú, o no está claro a qué
    opción se refiere): entonces manda el especialista de siempre.
    """
    # Dentro del cierre un "2" es un horario, no una categoría. El menú no
    # debería seguir vivo a esas alturas, pero no se adivina sobre el pedido.
    if state.checkout_step not in ("idle", ""):
        return None

    options = state.recent_options or []
    if not options:
        return None
    chosen = resolve_option(turn.text, options)
    if chosen is None:
        # Varios números en el mismo turno (el buffer une los mensajes seguidos:
        # "4" a las 16:01 y "1" a las 16:02). No se adivina cuál era, pero
        # tampoco se suelta el turno: se pregunta con las opciones que ya
        # compuso el código, renumeradas, para que la respuesta siguiente se
        # resuelva sola. Si el cliente vuelve a ser ambiguo, el contador de
        # turnos sin producto corta por lo sano.
        candidatos = resolve_options(turn.text, options)
        if len(candidatos) > 1:
            return AgentResult(
                user_facing=render_menu(
                    candidatos,
                    "Me diste más de un número y no quiero mandarte lo que no "
                    "era 😊 ¿Cuál de estas? Responde con el número:",
                ),
                state_patch={"recent_options": as_state(candidatos)},
            )
        return None

    hijos = chosen.get("hijos") or []
    nombre = chosen["nombre"]

    # Un segundo menú solo si hay hijas REALES y aún queda paso. Las categorías
    # sin hijas (Cestas, Peluches) van directas a productos, como el playbook
    # ya pedía y el modelo no siempre hacía.
    if hijos and state.menu_depth < MAX_MENU_DEPTH:
        return AgentResult(
            user_facing=render_menu(
                hijos,
                f"¡Buena elección! 🎁 Dentro de *{nombre}* tenemos esto. "
                "Responde con el número:",
            ),
            state_patch={
                "recent_options": as_state(hijos),
                "menu_depth": state.menu_depth + 1,
            },
        )

    # Con el slug en la mano no se pregunta más: productos.
    productos = await _productos_de(chosen["slug"])
    if not productos:
        # Sin stock en esa rama, el modelo busca alternativas con contexto.
        log.info("[catalog] %s (%s) no devolvió productos", nombre, chosen["slug"])
        return None

    return AgentResult(
        user_facing=compose_product_reply(
            f"¡Genial! Te muestro nuestros *{nombre}* 🎁", productos
        ),
        artifacts=productos,
        state_patch={"recent_options": [], "menu_depth": 0},
    )


async def _productos_de(slug: str) -> list[Product]:
    """Los productos REALES de una categoría, por su slug de la taxonomía."""
    try:
        payload = json.loads(await execute_tool("catalogo_categoria", {"slug": slug}))
    except Exception as err:
        log.warning("[catalog] no pude traer los productos de %s: %s", slug, err)
        return []
    return extract_products(payload)


async def _taxonomia() -> list[dict]:
    """Las categorías padre reales. `explorar_catalogo` ya viene cacheado."""
    try:
        return parse_navegacion(json.loads(await execute_tool("explorar_catalogo", {})))
    except Exception as err:
        log.warning("[catalog] no pude traer la taxonomía: %s", err)
        return []


async def _handle_escalate(
    turn: Turn, state: ConversationState, *, prev_intent: str = "", **ctx
) -> AgentResult:
    """Cede el control a un humano en código, no de palabra.

    Antes esto lo hacía el LLM del especialista `escalate`: a veces entraba en un
    bucle pidiendo el nombre ("¿me confirmas tu nombre para derivarte?") y nunca
    llamaba `escalar_a_humano`, o el turno de confirmación ("sí, pásame ahora")
    caía en `concierge`, que no tiene esa tool. El bot decía "te paso con un asesor,
    un momento" y jamás cedía el control.
    """
    # Red de seguridad contra un falso positivo del router LLM: si esto es charla
    # trivial y NO viene de una derivación, no derivamos — que responda concierge.
    # NO se re-juzga cuando el cliente está aceptando una derivación: ni si ya
    # estaba en curso (prev_intent=escalate), ni si el propio bot le ofreció el
    # asesor el turno anterior (`handoff_offered`). Un "sí" ahí parece charla para
    # `is_small_talk`, y descartarlo dejaba al cliente pidiendo un asesor que
    # nunca llegaba.
    if prev_intent != "escalate" and not state.handoff_offered:
        decision = handoff_policy(turn.messages)
        if not decision.allow:
            return await _run_specialty("small_talk", turn, state, **ctx)

    motivo = (
        state.handoff_reason
        or (turn.text or "").strip()
        or "cliente solicitó atención humana"
    )
    escalate = await perform_handoff(
        wa_id=ctx.get("wa_id"),
        conversation_id=ctx.get("conversation_id"),
        motivo=motivo,
        use_external_crm=ctx.get("use_external_crm", False),
        session=ctx.get("session"),
        persist=ctx.get("persist"),
    )
    return AgentResult(user_facing=None, escalate=escalate)


async def _handle_checkout(turn: Turn, state: ConversationState, **ctx) -> AgentResult:
    if state.checkout_step in ("idle", ""):
        # Con la cita: responder a la foto de un producto ES nombrarlo. Lo que
        # NO se consume aquí es el texto del cierre — eso va limpio, más abajo.
        chosen = resolve_chosen_product(state, turn.text_with_quote)
        if chosen is not None:
            # El cliente pudo verlo hace horas, y Qdrant va con retraso respecto al
            # catálogo. Cerrar el pedido de un producto dado de baja significa que
            # el asesor entra al chat verde a cobrar algo que no existe.
            if await is_available(chosen[0]) is False:
                log.info("[stock] producto %s ya no disponible; no se abre el cierre", chosen[0])
                # Fuera de la memoria del chat: si sigue ahí, el próximo "ese lo
                # quiero" volvería a resolver al producto muerto. `patch` fusiona
                # listas, así que hay que quitarlo a mano.
                muerto = chosen[0]
                state.recent_products = [
                    p for p in state.recent_products if p.get("id_producto") != muerto
                ]
                state.shown_product_ids = [
                    i for i in state.shown_product_ids if i != muerto
                ]
                return AgentResult(user_facing=unavailable_message(chosen[1]))

            # Solo fijamos el producto. El paso lo avanza `advance_checkout` desde
            # "idle", que además NO consume este texto: "quiero el panditas" es la
            # elección del producto, no el distrito.
            state.chosen_product_id, state.chosen_product_name = chosen
        elif not state.chosen_product_id and state.recent_products:
            # Varias opciones a la vista y una referencia ambigua ("ese"): preguntar
            # es mejor que cerrar el pedido del producto equivocado. Pero si el
            # producto YA estaba elegido (lo fijó un especialista LLM el turno
            # anterior, ver `_capture_choice`), no lo re-preguntamos: seguimos.
            names = ", ".join(
                p["nombre"] for p in state.recent_products[:5] if p.get("nombre")
            )
            return AgentResult(
                user_facing=(
                    f"¡Genial! 😊 ¿Cuál de estos te llevas: {names}?"
                    if names
                    else "¡Genial! 😊 ¿Cuál de los que te mostré te llevas?"
                )
            )

    state, reply, meta = advance_checkout(state, turn.text)

    # El cierre se atascó (no lo entendimos tres veces) o el cliente se está
    # yendo. Se cede el chat de verdad, en código — pero NO es una venta: no se
    # crea pedido temporal ni se anuncia nada en verde en el CRM. El asesor entra
    # a rescatar la conversación, no a cobrar.
    if meta.get("handoff"):
        escalate = await perform_handoff(
            wa_id=ctx.get("wa_id"),
            conversation_id=ctx.get("conversation_id"),
            motivo=state.handoff_reason or "el cierre se atascó",
            use_external_crm=ctx.get("use_external_crm", False),
            session=ctx.get("session"),
            persist=ctx.get("persist"),
        )
        return AgentResult(user_facing=reply, escalate=escalate)

    if not meta.get("escalate"):
        return AgentResult(user_facing=reply)

    # Resumen confirmado: el bot cerró la venta. Antes de escalar dejamos dos
    # rastros del pedido:
    #   1. El pedido temporal en el panel de donregalo (best-effort). Así ventas
    #      lo convierte con un clic en vez de recapturar los datos a mano.
    #   2. La venta en el CRM (chat en verde), para que el asesor entre sabiendo
    #      qué se vendió en vez de reconstruirlo leyendo el hilo.
    conversation_id = ctx.get("conversation_id")
    if meta.get("create_order") and settings.pedido_temporal_enabled:
        data = await create_temporal_order(state, ctx.get("wa_id") or "")
        if data and data.get("id_pedido_temporal"):
            try:
                state.pedido_temporal_id = int(data["id_pedido_temporal"])
            except (TypeError, ValueError):
                pass
    if conversation_id is not None:
        await announce_sale(conversation_id, state)

    # El pago lo coordina un humano. La derivación es una transición de estado,
    # no una decisión generativa: se ejecuta directamente y nunca invoca OpenAI.
    motivo = state.handoff_reason or "cliente listo para pagar / coordinar comprobante"
    escalate = await perform_handoff(
        wa_id=ctx.get("wa_id"),
        conversation_id=ctx.get("conversation_id"),
        motivo=motivo,
        use_external_crm=ctx.get("use_external_crm", False),
        session=ctx.get("session"),
        persist=ctx.get("persist"),
    )
    return AgentResult(user_facing=None, escalate=escalate)


async def _handle_detail(turn: Turn, state: ConversationState, **ctx) -> AgentResult:
    """Detalle de producto con el contenido ya en la mano.

    "¿Qué contiene?" solo la puede responder `GET /productos/{id}`: el listado
    trae `descripcion_corta`, que es copy de marketing ("Sorprende con un
    Desayuno Regalo para enamorar"), no la lista de items. Hasta ahora el único
    camino a ese dato era que el modelo DECIDIERA llamar `detalle_producto`, y a
    veces no lo hacía: entonces respondía con el copy, o se lo inventaba, o
    prometía consultarlo con un asesor — algo que no puede hacer.

    Así que se trae en código antes de que el modelo escriba, igual que el
    formato de la ficha o el cambio de divisa. El modelo ya no puede olvidarse:
    cuando le toca redactar, el contenido está en su contexto.

    Si no se puede resolver a qué producto se refiere, no se adivina: se corre el
    especialista como antes y él pregunta cuál.
    """
    detalle = await _prefetch_detalle(turn, state)
    return await _run_specialty(
        "product_detail",
        turn,
        state,
        extra_system=_render_contenido(detalle),
        fallback_artifacts=_artifacts_from(detalle),
        **ctx,
    )


def _detalle_target(turn: Turn, state: ConversationState) -> int | None:
    """¿De qué producto pregunta? `None` si no es unívoco.

    Mira también la cita: "¿qué contiene?" respondiendo a la foto de un desayuno
    dice de cuál se pregunta, aunque el texto no lo nombre.
    """
    chosen = resolve_chosen_product(state, turn.text_with_quote)
    if chosen is not None:
        return chosen[0]
    # El cliente ya lo había elegido y ahora pregunta por él sin nombrarlo
    # ("¿y qué trae?"): `resolve_chosen_product` mira lo mostrado, no lo elegido.
    return state.chosen_product_id or None


async def _prefetch_detalle(turn: Turn, state: ConversationState) -> dict | None:
    pid = _detalle_target(turn, state)
    if pid is None:
        return None
    try:
        payload = json.loads(await execute_tool("detalle_producto", {"id_producto": pid}))
    except Exception as err:
        # Best-effort: si la API falla seguimos con el especialista de siempre.
        # Quedarse sin responder por no poder precargar sería peor que antes.
        log.warning("[detail] no pude precargar el detalle de %s: %s", pid, err)
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("id_producto"):
        return data
    return None


def _render_contenido(detalle: dict | None) -> str:
    """El "¿qué contiene?" como hecho del sistema, no como algo que ir a buscar."""
    if not detalle:
        return ""
    descripcion = str(detalle.get("descripcion") or "").strip()
    if not descripcion:
        return ""
    nombre = str(detalle.get("nombre") or "").strip()
    return (
        "## CONTENIDO REAL DE ESTE PRODUCTO (ya consultado por el sistema)\n"
        f"{nombre} (id {detalle.get('id_producto')}):\n"
        f"{descripcion}\n\n"
        "Es el dato oficial de la API. Responde con ESTO, tal cual, sin añadir ni "
        "quitar items. Si el cliente pregunta por algo que no aparece aquí, dilo: "
        "no lo supongas."
    )


def _artifacts_from(detalle: dict | None) -> list[Product]:
    if not detalle:
        return []
    product = Product.from_raw(detalle)
    return [product] if product else []


async def _run_specialty(
    intent: str,
    turn: Turn,
    state: ConversationState,
    *,
    wa_id: str,
    contact_id: int | None = None,
    conversation_id: int | None = None,
    session: AsyncSession | None = None,
    use_external_crm: bool = False,
    persist=None,
    extra_system: str = "",
    fallback_artifacts: list[Product] | None = None,
) -> AgentResult:
    spec = spec_for(intent)
    if spec.deterministic:
        raise RuntimeError(
            f"el especialista determinista {spec.name} no puede ejecutarse vía LLM"
        )
    runtime_tools = spec.tools(
        with_memory=bool(contact_id or use_external_crm),
        with_handoff=conversation_id is not None,
    )
    runtime_tool_names = tuple(
        str(tool.get("function", {}).get("name") or "")
        for tool in runtime_tools
        if isinstance(tool, dict)
    )
    system = build_system(
        spec,
        state,
        extra=extra_system,
        turn_text=turn.text,
        has_media=turn.has_media,
        available_tool_names=runtime_tool_names,
    )
    model = (
        settings.openai_fast_model
        if spec.model_tier == "fast"
        else settings.openai_model
    )

    # Etiqueta del gasto. Sin ella, "cuánto cuesta el catálogo" y "cuánto cuesta
    # el concierge" serían la misma cifra y no habría forma de saber qué prompt
    # engordó. Va por contexto para no cruzar el nombre por media docena de
    # firmas hasta la llamada HTTP.
    with agent_context(spec.name):
        result = await run_specialist(
            [{"role": "system", "content": system}, *turn.messages],
            wa_id=wa_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            session=session,
            use_external_crm=use_external_crm,
            persist=persist,
            tools_override=runtime_tools,
            include_handoff=spec.can_handoff,
            include_memory=spec.customer_facing,
            model=model,
            max_tool_rounds=spec.max_tool_rounds,
            max_tool_calls=spec.max_tool_calls,
            parallel_tool_calls=spec.parallel_tool_calls,
        )

    output_policy = spec.output_policy

    # Nunca mostrar dos veces el mismo producto: el cliente lo lee como que no le
    # hicimos caso ("otras, no esas").
    if output_policy == "catalog":
        result.artifacts = dedupe_artifacts(state.shown_product_ids, result.artifacts)

    # El modelo respondió sin llamar la tool porque el dato ya lo tenía en el
    # system. Sin esto el turno saldría sin ficha (foto, nombre, precio) y sin
    # `chosen_product_*`: el producto quedaría solo en la prosa, que es
    # justamente de donde este harness no lee nada.
    if not result.artifacts and fallback_artifacts:
        result.artifacts = list(fallback_artifacts)

    if output_policy in ("catalog", "detail") and result.artifacts:
        result.user_facing = compose_product_reply(result.user_facing, result.artifacts)

    # El modelo ofreció un menú de categorías en vez de productos. Se le deja
    # (a veces toca preguntar), pero la lista la reescribe el código: así los
    # nombres son los reales y la numeración es la que luego sabemos resolver.
    if output_policy == "catalog" and not result.artifacts:
        await _own_the_menu(result, turn, state)

    _capture_choice(output_policy, turn, state, result)

    return result


async def _own_the_menu(
    result: AgentResult, turn: Turn, state: ConversationState
) -> None:
    """Reescribe el menú del modelo con la taxonomía real, y lo recuerda.

    Dos motivos, los dos vistos en producción. Uno: los nombres inventados
    ("Plantas de interior", "Terrarios y kokedamas") mandan al cliente a pedir
    cosas que no vendemos. Dos, y más sutil: si la numeración la pone el modelo
    y no la guardamos, el "7" del cliente vuelve a depender de que el modelo
    recuerde su propio menú. Guardarla es lo que permite que el turno siguiente
    lo resuelva `_answer_menu` sin preguntar otra vez.

    **El nivel se deduce del mensaje, no se asume.** La primera versión de esto
    reescribía SIEMPRE con las categorías padre, y a un cliente que preguntó
    "¿Cuáles son las opciones de flores disponibles?" le contestó con desayunos,
    peluches y cestas: le borró de la respuesta lo único que había pedido. Si ya
    nombró una categoría, el menú es el de SUS hijas — y si esa categoría no
    tiene hijas, no hay nada que preguntar y se muestran productos.
    """
    if not _looks_like_menu(result.user_facing):
        return
    options = await _taxonomia()
    if not options:
        return

    # ¿El cliente ya dijo de qué categoría habla? Entonces no se le vuelve a
    # preguntar por la categoría.
    padre = match_category(turn.text, options)
    if padre is not None and not padre["hijos"]:
        # Cestas, Peluches, Regalos para Bebé: sin hijas, un menú sobra.
        productos = await _productos_de(padre["slug"])
        if productos:
            result.artifacts = productos
            result.user_facing = compose_product_reply(
                f"¡Genial! Te muestro nuestros *{padre['nombre']}* 🎁", productos
            )
            result.state_patch = {
                **result.state_patch, "recent_options": [], "menu_depth": 0,
            }
            return

    nivel = padre["hijos"] if padre is not None and padre["hijos"] else options
    profundidad = 2 if nivel is not options else 1

    intro = _intro_of(result.user_facing) or "¡Perfecto! 🎁 ¿Qué te interesa?"
    result.user_facing = render_menu(nivel, f"{intro} Responde con el número:")
    result.state_patch = {
        **result.state_patch,
        "recent_options": as_state(nivel),
        "menu_depth": profundidad,
    }


# La definición de "menú" vive en `taxonomy`, que es quien los compone y quien
# los vigila desde los guardrails. Aquí solo se le da nombre local.
_looks_like_menu = looks_like_numbered_menu
_MENU_LINE = _taxonomy_menu_line


def _intro_of(reply: str | None) -> str:
    """La frase del modelo antes de la lista; el tono suyo, los datos nuestros."""
    if not reply:
        return ""
    cabeza = _MENU_LINE.split(reply)[0] if reply else ""
    primera = (cabeza or reply).strip().splitlines()
    linea = primera[0].strip() if primera else ""
    # Sin la coletilla de "elige el número" / "responde con el número": la
    # ponemos nosotros al final, y sin quitarla salía duplicada de verdad
    # ("¿Qué tipo de flores buscas? Elige el número Responde con el número:").
    linea = _COLETILLA.sub("", linea)
    return linea.rstrip(":").strip()


_COLETILLA = re.compile(
    r"[\s,.;:]*(?:responde|contesta|elige|escoge|indica|dime|selecciona)"
    r"(?:me)?\s+(?:con\s+)?(?:el\s+|la\s+|tu\s+)?"
    r"(?:n[uú]mero|opci[oó]n)\s*:?\s*$",
    re.I,
)


def _capture_choice(
    output_policy: str, turn: Turn, state: ConversationState, result: AgentResult
) -> None:
    """Fija el producto elegido cuando la elección la resuelve un especialista LLM.

    El bug (Roberto, 17-07): el cliente eligió un producto ("4") y lo atendió el
    especialista `detail`, no el FSM de cierre. Los especialistas LLM NUNCA
    escribían `chosen_product_*`, así que la elección se quedaba solo en la prosa
    del modelo. En cuanto el turno siguiente pasaba a cierre o cobertura —los dos
    exigen ese campo— volvían a preguntar "¿cuál te llevas?" / "¿qué regalo
    quieres enviar?" sobre algo ya decidido.

    Se persiste desde la fuente autoritativa (el `artifact` que devolvió la tool,
    no la prosa) o, si el modelo respondió de contexto sin volver a llamarla,
    desde la lista que el cliente YA vio, con la misma resolución que usa el FSM.
    """
    if output_policy not in ("catalog", "detail"):
        return
    # Dentro del cierre el producto ya está fijo y un "2" es un horario, no una
    # elección de producto: no lo tocamos.
    if state.checkout_step not in ("idle", ""):
        return

    chosen: tuple[int, str] | None = None
    # 1. Autoritativo: el detalle de UN solo producto es el que el cliente mira.
    if output_policy == "detail" and len(result.artifacts) == 1:
        art = result.artifacts[0]
        chosen = (art.id_producto, art.nombre)
    else:
        # 2. Elección explícita ("el segundo", "4", por nombre) que el modelo
        #    respondió de contexto. `allow_implicit=False` no fija nada ante un
        #    "ese" vago ni una búsqueda nueva: preferimos no adivinar.
        chosen = resolve_chosen_product(state, turn.text, allow_implicit=False)

    if chosen is not None:
        result.state_patch = {
            **result.state_patch,
            "chosen_product_id": chosen[0],
            "chosen_product_name": chosen[1],
        }


# Invariantes que NO se pueden dejar pasar al cliente: un precio inventado que el
# cliente da por bueno, o un medio de pago que no existe. Las demás
# (`image_urls_on_own_line`, repetidos) solo pueden venir del listado, que ya arma
# el código y es fiable por construcción: registrarlas basta.
# Una línea que lleva una URL de imagen, la escriba el modelo como la escriba.
_IMG_LINE = re.compile(r"https?://\S+\.(?:jpe?g|png|webp|gif)", re.I)
# Viñeta de producto: "• 🎁 *Nombre* — S/149.60 ($44.00)".
_BULLET_LINE = re.compile(r"^\s*[•\-\*]|—\s*S\s*/|^\s*\d+[.)]\s+\S.*S\s*/", re.I)
_CLOSING_LINE = re.compile(r"^\s*¿.*detalle", re.I)


def compose_product_reply(model_text: str | None, artifacts: list) -> str:
    """El listado de productos lo arma el código, no el modelo.

    Durante semanas el formato de los productos vivió en el prompt: "la URL va sola
    en su línea, luego la viñeta". Cuando el modelo se desviaba —y se desviaba— el
    cliente recibía un muro de enlaces en vez de fotos, porque el emisor solo
    convierte en imagen una línea que reconoce como URL.

    Los productos ya vienen tipados en `artifacts` (id, nombre, precios, imagen),
    así que no hay ninguna razón para pedirle al modelo que los formatee. Nos
    quedamos con su intro (que aporta el tono) y el resto lo renderizamos.
    """
    intro_lines: list[str] = []
    for line in (model_text or "").split("\n"):
        if _IMG_LINE.search(line) or _BULLET_LINE.search(line) or _CLOSING_LINE.match(line):
            continue
        intro_lines.append(line)

    intro = "\n".join(intro_lines).strip()
    listado = render_product_list([_as_dict(p) for p in artifacts])

    return f"{intro}\n\n{listado}" if intro else listado


def _as_dict(product) -> dict:
    return {
        "id_producto": product.id_producto,
        "nombre": product.nombre,
        "precio_sol": product.precio_sol,
        "precio_usd": product.precio_usd,
        "imagen_url": product.imagen_url,
        "descripcion_corta": product.descripcion,
    }
