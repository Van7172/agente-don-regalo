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
from app.harness.checkout import (
    advance_checkout,
    resolve_chosen_product,
    wants_checkout,
)
from app.harness.contracts import AgentResult, EscalateReason, Product, Turn
from app.harness.coverage import resolve_coverage
from app.harness.invariants import check_reply
from app.harness.orders import create_from_state as create_temporal_order
from app.harness.policies import dedupe_artifacts, handoff_policy, latest_user_text
from app.harness.registry import spec_for
from app.harness.sale import announce as announce_sale
from app.harness.render import render_product_list
from app.harness.router import classify
from app.harness.state import ConversationState, load_state, save_state
from app.harness.stock import is_available, unavailable_message
from app.harness.trace import Trace
from app.prompts.compose import build_system
from app.prompts.playbooks import WELCOME
from app.services.agent import HANDOFF_DONE, perform_handoff, run_specialist
from app.tools.executor import execute_tool

log = logging.getLogger(__name__)


# El bot OFRECIENDO un asesor: una pregunta que menciona a una persona del equipo
# ("¿Quieres que consulte con un asesor?", "¿te paso con un ejecutivo?"). Si el
# cliente dice "sí" a esto, está aceptando la derivación.
_ADVISOR_RE = re.compile(
    r"\b(asesor\w*|ejecutiv\w*|human[oa]s?|una persona|del equipo)\b", re.I
)

# El bot PROMETE meter a un asesor: lo AFIRMA, no lo pregunta. Regalito no puede
# consultarle nada a nadie ni "volver con la respuesta" — lo único que puede hacer
# con un asesor es cederle el chat. Si lo dice y no se ejecuta, el cliente espera a
# alguien que nunca viene ("consulto con un asesor y te vuelvo", "un asesor te
# enviará las instrucciones", "te paso con un asesor").
_PROMISES_HANDOFF_RE = re.compile(
    r"(consult|pregunt|verific|confirm|averigu|revis)\w*[^.?!¿]{0,60}\bcon\s+"
    r"(un|una|el|la|mi|nuestro)?\s*(asesor|ejecutiv|compa|human|persona|equipo)"
    r"|\b(un|una)\s+(asesor\w*|ejecutiv\w*)\s+(te|le)\s+\w+"
    r"|\bte\s+(paso|conecto|derivo|comunico|transfiero)\s+con\s+"
    r"(un|una|el|la)?\s*(asesor|ejecutiv|human|persona)",
    re.I,
)


def _offers_handoff(reply: str | None) -> bool:
    """El bot ofrece un asesor y espera respuesta: un "sí" es aceptar la derivación.

    Antes se exigía que "asesor" cayera DENTRO de los ¿…?, pero el modelo lo deja en
    la frase anterior ("Puedo confirmarlo con un asesor. ¿Deseas que lo consulte
    ahora?") y entonces el "Si" del cliente no derivaba.
    """
    return bool(reply and _ADVISOR_RE.search(reply) and "?" in reply)


def _promises_handoff(reply: str | None) -> bool:
    return bool(reply and _PROMISES_HANDOFF_RE.search(reply))


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
    """Qué nos llega del cliente en este turno."""
    text = latest_user_text(messages)
    has_media = text is None
    # Con imagen, `text` viene None; conservamos el caption para poder enrutar.
    return Turn(text=(text if not has_media else _caption_of(messages)) or "", has_media=has_media, messages=messages)


def _reduce(state: ConversationState, result: AgentResult) -> ConversationState:
    """Aplica al estado lo que el especialista aprendió."""
    if result.state_patch:
        state.patch(result.state_patch)

    if result.artifacts:
        state.patch({
            "shown_product_ids": [p.id_producto for p in result.artifacts],
            "recent_products": [
                {"id_producto": p.id_producto, "nombre": p.nombre}
                for p in result.artifacts
            ],
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
    turn = perceive(messages)
    state = (
        await load_state(conversation_id, wa_id=wa_id)
        if conversation_id is not None
        else ConversationState()
    )

    classification = await classify(turn.text, state, has_media=turn.has_media)
    intent = classification.intent
    prev_intent = state.intent_last  # antes de sobrescribir: ¿venía de una derivación?
    state.intent_last = intent

    trace = Trace(
        conversation_id=conversation_id,
        intent=intent,
        agent=spec_for(intent).name,
        confidence=classification.confidence,
        router=classification.source,
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

    # El modelo prometió meter a un asesor. Eso no es una frase, es un cambio de
    # estado: Regalito no puede consultarle nada a nadie ni volver con la respuesta,
    # solo cederle el chat. Si lo dijo, se ejecuta — y si no, el cliente se queda
    # esperando a alguien que nunca viene ("consulto con un asesor y te vuelvo").
    if (
        result.escalate is None
        and conversation_id is not None
        and _promises_handoff(result.user_facing)
    ):
        log.info(
            "[HANDOFF] conversation=%s la respuesta prometía un asesor; se ejecuta de verdad",
            conversation_id,
        )
        result = AgentResult(
            user_facing=None,
            artifacts=result.artifacts,
            tools_used=result.tools_used,
            state_patch=result.state_patch,
            escalate=await perform_handoff(
                wa_id=wa_id,
                conversation_id=conversation_id,
                motivo=state.handoff_reason
                or "el bot ofreció consultar con un asesor (no puede hacerlo)",
                use_external_crm=use_external_crm,
                session=session,
                persist=persist,
            ),
        )

    # Las invariantes se miden ANTES de reducir: comparan lo que trae este turno
    # contra lo que el cliente ya había visto.
    violations = check_reply(
        result.user_facing, state=state, artifacts=result.artifacts
    )

    # Detectar no basta: lo grave no sale al cliente (ver `_degrade_unsafe_reply`).
    if result.user_facing is not None:
        seguro = _degrade_unsafe_reply(
            result.user_facing, violations, result.artifacts
        )
        if seguro != result.user_facing:
            log.warning(
                "[INVARIANTE] conversation=%s respuesta degradada por %s",
                conversation_id,
                [str(v) for v in violations],
            )
            result.user_facing = seguro

    state = _reduce(state, result)

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
    trace.done().emit()

    if conversation_id is not None:
        await save_state(conversation_id, state, wa_id=wa_id)

    if result.escalate is not None:
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
        )

    # ── Cierre: máquina de estados, sin LLM ───────────────────────
    if intent == "checkout" or (
        wants_checkout(turn.text) and state.checkout_step in ("idle", "")
    ):
        return await _handle_checkout(turn, state, **ctx)

    # ── Detalle: el contenido se trae en código, no se le pide al modelo ──
    if intent == "product_detail":
        return await _handle_detail(turn, state, **ctx)

    # ── Resto: especialista LLM con toolset acotado ───────────────
    return await _run_specialty(intent, turn, state, **ctx)


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
        chosen = resolve_chosen_product(state, turn.text)
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

    # El pago lo coordina un humano.
    motivo = state.handoff_reason or "cliente listo para pagar / coordinar comprobante"
    escalated = await _run_specialty(
        "escalate",
        turn,
        state,
        extra_system=(
            "El cliente confirmó el resumen del pedido y pasa a pago. "
            f"Llama YA `escalar_a_humano` con motivo: {motivo}."
        ),
        **ctx,
    )
    if escalated.escalate is not None:
        return escalated

    # El especialista no llamó la tool: el handoff se hace igual. Un cliente
    # listo para pagar no puede quedarse esperando.
    return AgentResult(
        user_facing=reply, escalate=EscalateReason(motivo=motivo, is_payment=True)
    )


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
    """¿De qué producto pregunta? `None` si no es unívoco."""
    chosen = resolve_chosen_product(state, turn.text)
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
    system = build_system(spec, state, extra=extra_system)

    result = await run_specialist(
        [{"role": "system", "content": system}, *turn.messages],
        wa_id=wa_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        session=session,
        use_external_crm=use_external_crm,
        persist=persist,
        tools_override=spec.tools(
            with_memory=bool(contact_id or use_external_crm),
            with_handoff=conversation_id is not None,
        ),
        include_handoff=spec.can_handoff,
        include_memory=spec.customer_facing,
    )

    # Nunca mostrar dos veces el mismo producto: el cliente lo lee como que no le
    # hicimos caso ("otras, no esas").
    if spec.name == "catalog":
        result.artifacts = dedupe_artifacts(state.shown_product_ids, result.artifacts)

    # El modelo respondió sin llamar la tool porque el dato ya lo tenía en el
    # system. Sin esto el turno saldría sin ficha (foto, nombre, precio) y sin
    # `chosen_product_*`: el producto quedaría solo en la prosa, que es
    # justamente de donde este harness no lee nada.
    if not result.artifacts and fallback_artifacts:
        result.artifacts = list(fallback_artifacts)

    if spec.name in ("catalog", "detail") and result.artifacts:
        result.user_facing = compose_product_reply(result.user_facing, result.artifacts)

    _capture_choice(spec.name, turn, state, result)

    return result


def _capture_choice(
    spec_name: str, turn: Turn, state: ConversationState, result: AgentResult
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
    if spec_name not in ("catalog", "detail"):
        return
    # Dentro del cierre el producto ya está fijo y un "2" es un horario, no una
    # elección de producto: no lo tocamos.
    if state.checkout_step not in ("idle", ""):
        return

    chosen: tuple[int, str] | None = None
    # 1. Autoritativo: el detalle de UN solo producto es el que el cliente mira.
    if spec_name == "detail" and len(result.artifacts) == 1:
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
_BLOCKING_RULES = frozenset({"prices_are_sourced", "no_cash_on_delivery"})

# Respaldo cuando el turno no tiene productos que preservar (prosa pura). No cita
# ninguna cifra —que es justo lo que se degradó— y no promete traer a un asesor:
# eso dispararía `_promises_handoff`, y Regalito no puede consultar y volver.
_SAFE_FALLBACK = (
    "Para no darte un dato equivocado, prefiero confirmártelo bien 🙏 "
    "El pago es siempre por adelantado (Yape/Plin, transferencia bancaria o "
    "tarjeta). ¿Te comparto los precios exactos del regalo que te interesa?"
)


def _degrade_unsafe_reply(
    reply: str | None, violations: list, artifacts: list
) -> str | None:
    """Ante una violación grave, no enviar la prosa del modelo tal cual.

    Hasta ahora las invariantes eran solo observacionales: `check_reply` dejaba la
    violación en la traza y la respuesta salía igual. Es decir, el fallo más caro
    del negocio —un precio que el modelo se inventó— quedaba registrado *después*
    de que el cliente ya lo había leído.

    La degradación aprovecha que la respuesta es de dos piezas
    (`compose_product_reply`): la intro la escribe el modelo, el listado lo arma el
    código desde `artifacts`. Lo contaminado solo puede ser la intro, así que se
    tira la intro y se conserva el listado, que trae los precios reales. Sin
    productos que preservar, cae al respaldo fijo.
    """
    rotas = {v.rule for v in violations} & _BLOCKING_RULES
    if not rotas:
        return reply

    if artifacts:
        return render_product_list([_as_dict(p) for p in artifacts])
    return _SAFE_FALLBACK


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
