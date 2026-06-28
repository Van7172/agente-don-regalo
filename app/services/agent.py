"""
Loop agéntico: llama al modelo, ejecuta herramientas y repite
hasta obtener una respuesta final de texto.
"""
import json
import random
import logging

import httpx

from app.config import settings
from app.tools import TOOLS, MEMORY_TOOL, HUMAN_HANDOFF_TOOL, execute_tool
from app.services.memory import save_contact_attributes
from app.services.messenger import send_message, set_typing, add_label, notify_team

log = logging.getLogger(__name__)

# Sentinela que devuelve run_agent cuando ya escaló a un humano: el mensaje de
# espera y la etiqueta ya se enviaron, así que buffer no debe mandar nada más.
HANDOFF_DONE = "__handoff_done__"

# Mensaje de espera al escalar a un asesor humano (se envía ANTES de etiquetar).
_HANDOFF_WAIT_MSG = (
    "¡Claro! Te conecto con un asesor de nuestro equipo 🙏 "
    "Dame un momento, en seguida continúan contigo."
)

# Mensaje de espera según la primera herramienta lenta del turno.
_FILLER_BY_TOOL: dict[str, list[str]] = {
    "buscar_semantico":     ["¡Genial! Déjame buscarte las mejores opciones 🎁",
                             "¡Claro! Ya te busco algo perfecto 😍"],
    "buscar_productos":     ["Un momento 😊"],
    "productos_similares":  ["¡Buena elección! Te muestro otras opciones parecidas 😊",
                             "Déjame buscarte alternativas similares 🎁"],
    "catalogo_categoria":   ["¡Perfecto! Déjame mostrarte lo que tenemos 🎁",
                             "Ya te traigo el catálogo 😊"],
    "productos_por_ocasion":["¡Qué lindo detalle! Déjame buscar algo ideal 🎁",
                             "Ya te busco opciones perfectas para la ocasión 😍"],
    "productos_destacados": ["¡Con gusto! Déjame mostrarte lo más pedido ⭐",
                             "Ya te traigo nuestras recomendaciones 😊"],
    "productos_oferta":     ["¡Me encanta! Déjame buscar nuestras mejores ofertas 🔥",
                             "Ya te traigo lo que está en promoción 🎁"],
    "detalle_producto":     ["Un momento, te traigo la info completa 😊"],
    "distritos_cobertura":  ["Un momento 😊"],
    "metodos_pago":         ["Déjame contarte las formas de pago 💳"],
    "rastrear_pedido":      ["Déjame revisar el estado de tu pedido 📦"],
    "buscar_conocimiento_equipo": ["Déjame verificar eso para darte la mejor respuesta 😊",
                                   "Permíteme revisarlo un momento 🙌"],
}


def _filler_for_tools(tool_calls: list) -> str | None:
    for call in tool_calls:
        fn = call.get("function", {}).get("name", "")
        opciones = _FILLER_BY_TOOL.get(fn)
        if opciones:
            return random.choice(opciones)
    return None


# Conversaciones que ya recibieron un mensaje de espera (filler). Se envía solo
# UNA vez por conversación: en búsquedas sucesivas resultaba repetitivo y robótico
# (el cliente ya sabe que estamos buscando, y el indicador "escribiendo…" basta).
_filler_conversations: set[int] = set()


async def run_agent(
    messages: list,
    contact_id: int | None = None,
    conversation_id: int | None = None,
) -> str | None:
    """Ejecuta el loop de function calling hasta obtener respuesta final."""
    all_tools  = TOOLS + ([MEMORY_TOOL] if contact_id else [])
    if conversation_id is not None:
        all_tools = all_tools + [HUMAN_HANDOFF_TOOL]
    filler_sent = conversation_id in _filler_conversations
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(settings.max_tool_rounds):
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model":       settings.openai_model,
                        "messages":    messages,
                        "tools":       all_tools,
                        "tool_choice": "auto",
                    },
                )
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]

                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    return msg.get("content")

                messages.append(msg)

                if not filler_sent and conversation_id is not None:
                    filler = _filler_for_tools(tool_calls)
                    if filler:
                        await send_message(conversation_id, filler)
                        await set_typing(conversation_id, True)
                        filler_sent = True
                        _filler_conversations.add(conversation_id)
                        # Cota de memoria: el set vive en el proceso.
                        if len(_filler_conversations) > 5000:
                            _filler_conversations.clear()

                for call in tool_calls:
                    fn = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    log.info("[TOOL] %s args=%s", fn, args)

                    if fn == "escalar_a_humano":
                        # Escalación a un asesor humano. Orden requerido:
                        # 1) mensaje de espera al cliente, 2) etiqueta de soporte.
                        # Tras esto NO se genera más respuesta (el gate del webhook
                        # bloquea al bot mientras la etiqueta esté activa).
                        motivo = args.get("motivo") or "no especificado"
                        log.info("[HANDOFF] conversation=%s motivo=%s", conversation_id, motivo)
                        await send_message(conversation_id, _HANDOFF_WAIT_MSG)
                        await add_label(conversation_id, settings.human_support_label)
                        await notify_team(
                            f"🙋 Atención humana solicitada (conversación {conversation_id}). "
                            f"Motivo: {motivo}."
                        )
                        return HANDOFF_DONE

                    if fn == "guardar_datos_cliente":
                        result = await save_contact_attributes(contact_id, args)
                    else:
                        result = await execute_tool(fn, args)

                    messages.append({
                        "role":         "tool",
                        "tool_call_id": call["id"],
                        "content":      result,
                    })

            log.warning("Se alcanzó MAX_TOOL_ROUNDS sin respuesta final")
            return None
    except Exception as e:
        log.error("Error en el bucle del agente: %s", e)
        return None
