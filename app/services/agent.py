"""
Loop agéntico: llama al modelo, ejecuta herramientas y repite
hasta obtener una respuesta final de texto.
"""
import json
import random
import logging

import httpx

from app.config import settings
from app.tools import TOOLS, MEMORY_TOOL, execute_tool
from app.services.memory import save_contact_attributes
from app.services.messenger import send_message, set_typing

log = logging.getLogger(__name__)

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


async def run_agent(
    messages: list,
    contact_id: int | None = None,
    conversation_id: int | None = None,
) -> str | None:
    """Ejecuta el loop de function calling hasta obtener respuesta final."""
    all_tools  = TOOLS + ([MEMORY_TOOL] if contact_id else [])
    filler_sent = False
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

                for call in tool_calls:
                    fn = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    log.info("[TOOL] %s args=%s", fn, args)

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
