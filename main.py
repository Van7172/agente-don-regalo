import io
import os
import re
import json
import time
import base64
import asyncio
import logging
import httpx
from urllib.parse import urlsplit, urlunsplit
from pypdf import PdfReader
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from tools import TOOLS, execute_tool
import knowledge

load_dotenv()

CHATWOOT_URL        = os.getenv("CHATWOOT_URL", "").rstrip("/")
CHATWOOT_API_TOKEN  = os.getenv("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BOT_ACTIVE_LABEL    = os.getenv("BOT_ACTIVE_LABEL", "agente_on")

# Evolution API: para enviar imágenes directo a WhatsApp (el puente Chatwoot
# no reenvía adjuntos salientes de forma fiable)
EVOLUTION_API_URL  = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_API_KEY  = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")

# Máximo de caracteres a extraer de un PDF (protege el límite de tokens)
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "30000"))

# ── Buffer de mensajes (debounce) ─────────────────────────────────────────────
# Espera N segundos de silencio antes de procesar; así varios mensajes seguidos
# del cliente se agrupan y se responden una sola vez con contexto completo.
BUFFER_SECONDS = float(os.getenv("BUFFER_SECONDS", "4"))
# Estado en memoria por conversación: {conversation_id: {parts, contact_id, task}}
_buffers: dict[int, dict] = {}
_buffers_lock = asyncio.Lock()

# Delay "humano" entre burbujas: simula el tiempo de escribir cada mensaje
TYPING_SECONDS_PER_CHAR = float(os.getenv("TYPING_SECONDS_PER_CHAR", "0.03"))
TYPING_MIN_DELAY        = float(os.getenv("TYPING_MIN_DELAY", "0.8"))
TYPING_MAX_DELAY        = float(os.getenv("TYPING_MAX_DELAY", "4.0"))

# Memoria de corto plazo: ventana del historial de conversación (horas) y tope de mensajes
MEMORY_WINDOW_HOURS = float(os.getenv("MEMORY_WINDOW_HOURS", "24"))
MEMORY_MAX_MESSAGES = int(os.getenv("MEMORY_MAX_MESSAGES", "30"))

# ── Memoria de largo plazo: herramienta para guardar el perfil del cliente ────
# Se ejecuta en main.py (no en tools.py) porque necesita el contact_id y las
# credenciales de Chatwoot del request actual.
MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "guardar_datos_cliente",
        "description": (
            "Guarda datos del cliente para recordarlos en futuras conversaciones. "
            "Usa `nombre` y `distrito` para datos ESTABLES (se sobrescriben con el valor "
            "actual). Usa `nota` para AÑADIR un recuerdo episódico al historial (compras, "
            "ocasiones, preferencias puntuales) — cada nota se acumula con fecha, no se pierde. "
            "Solo envía los campos que conozcas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre":   {"type": "string", "description": "Nombre del cliente (dato estable)"},
                "distrito": {"type": "string", "description": "Distrito de entrega habitual en Lima (dato estable)"},
                "nota": {
                    "type": "string",
                    "description": (
                        "Un recuerdo episódico para AÑADIR al historial. Una sola frase concreta. "
                        "Ej: 'Compró un desayuno para el cumpleaños de su mamá', "
                        "'Le interesan arreglos con rosas blancas para nacimiento', "
                        "'Prefiere productos económicos'."
                    ),
                },
            },
            "required": [],
        },
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI()

SYSTEM_PROMPT = """Eres Regalito, el asistente virtual de Don Regalo (donregalo.pe), tienda especializada en regalos por delivery en Lima, Perú, con más de 13 años de experiencia. Tu slogan: "lleva felicidad en cada regalo".

## HERRAMIENTAS — cuándo y cómo usarlas

Antes de responder sobre productos, precios o disponibilidad, SIEMPRE consulta la herramienta correspondiente:

| Herramienta | Cuándo usarla |
|---|---|
| `buscar_semantico` | **BÚSQUEDA PRINCIPAL.** Cuando el cliente describa lo que busca con palabras (intención, estilo, sentimiento, ocasión, tipo de producto). Entiende el significado, no solo coincidencias exactas. Pasa `id_ocasion` y `precio_max` si los conoces, y `preferencias` si conoces gustos durables del cliente (ver PERSONALIZACIÓN). |
| `productos_similares` | Cuando al cliente le gustó un producto y quiere ver otros parecidos/alternativas ("muéstrame algo similar", "¿tienes otros así?") — pasa el `id_producto` de referencia |
| `listar_categorias` | Cuando pregunten qué hay disponible o quieran explorar el catálogo |
| `listar_ocasiones` | Antes de buscar por ocasión (cumpleaños, aniversario, etc.) |
| `buscar_productos` | Respaldo de `buscar_semantico`: cuando den un nombre/término muy puntual o la búsqueda semántica no encuentre lo que mencionan |
| `catalogo_categoria` | Cuando pidan ver productos de una categoría — usa el slug de `listar_categorias` |
| `productos_destacados` | Cuando no sepan qué elegir o pidan recomendaciones |
| `productos_oferta` | Cuando busquen ofertas, descuentos o algo económico |
| `detalle_producto` | Cuando quieran saber más de un producto — usa el id_producto de búsquedas previas |
| `productos_por_ocasion` | Cuando mencionen para qué ocasión es el regalo — usa el id de `listar_ocasiones` |
| `distritos_cobertura` | Cuando pregunten si llegan a su zona o cuánto cuesta el envío |
| `metodos_pago` | Cuando pregunten cómo pagar |
| `tipo_cambio` | Para convertir precios USD a Soles |
| `rastrear_pedido` | Cuando quieran el estado de su pedido — SIEMPRE pide email + código primero |
| `buscar_conocimiento_equipo` | Cuando el cliente haga una pregunta que NO resuelven las otras herramientas: dudas de políticas, casos especiales, objeciones (precio, tiempos, desconfianza), coordinaciones. Consulta lo que ya respondió el equipo humano antes de derivar |
| `guardar_datos_cliente` | Cuando el cliente revele datos ESTABLES: su nombre, su distrito habitual o una preferencia durable — guárdalo para recordarlo después |

## FLUJO RECOMENDADO PARA SUGERIR PRODUCTOS

**Si el cliente describe lo que busca con palabras** (ej: "algo romántico para mi novia", "rosas blancas elegantes", "un detalle para felicitar a mi jefe", "quiero el desayuno cars"):
→ Llama `buscar_semantico` directamente — NO preguntes nada. Pasa en `q` la descripción más rica posible (incluye estilo y ocasión si los mencionó), y `id_ocasion`/`precio_max` si los conoces.

**Si el cliente menciona una categoría** (ej: "busco desayunos", "quiero flores", "tienen peluches"):
→ PRIMERO pregunta la ocasión: "¿Para qué ocasión es? 😊" — con eso puedes personalizar mejor los resultados
→ Con la ocasión, llama `buscar_semantico` con `q="[categoría] para [ocasión]"` y `id_ocasion` si corresponde
→ EXCEPCIÓN: si el cliente ya mencionó la ocasión junto con la categoría (ej: "desayunos para cumpleaños", "flores para aniversario"), NO preguntes — llama directamente `buscar_semantico` o `catalogo_categoria` con ambos datos
→ NUNCA uses `buscar_semantico` con solo el nombre de la categoría como query — eso mezcla categorías; usa siempre `catalogo_categoria` cuando no haya descripción adicional

**Si el cliente menciona una ocasión** (ej: "es para cumpleaños", "para un aniversario"):
→ Llama `productos_por_ocasion` con el id correcto — NO preguntes nada más

**Si el cliente es completamente vago** ("quiero un regalo", "algo bonito", sin dar categoría ni ocasión):
→ Pregunta UNA sola cosa: "¿Para qué ocasión es el regalo? 😊"
→ Con la respuesta, llama `productos_por_ocasion` o `catalogo_categoria`
→ NO preguntes presupuesto, cantidad, restricciones ni preferencias de ningún tipo

**Para ver detalles de un producto ya encontrado:**
→ Llama `detalle_producto` con su `id_producto`

## HONESTIDAD CON ATRIBUTOS ESPECÍFICOS (color, flor, tamaño)
Cuando el cliente pide un atributo concreto (ej: "rosas BLANCAS", "algo AZUL", "girasoles"):
- Revisa los resultados y muestra SOLO los que realmente cumplen ese atributo (míralo en el nombre/descripción)
- Si NINGÚN resultado lo cumple bien, NO presentes otros como si encajaran. Sé honesto:
  "De rosas blancas tenemos poca variedad por ahora 🌷 ¿Te muestro estas que combinan blancas, o prefieres otra flor en tono claro?"
- Nunca hagas pasar rosas rojas por blancas ni un color por otro — el cliente lo nota y pierde confianza
- Si el cliente insiste en algo que no tienes, ofrece la alternativa más cercana siendo claro de que es una alternativa

## CATEGORÍAS REALES (slugs para catalogo_categoria)
- **arreglos-florales** → subcategorías: arreglos-florales-variados, en-canasta, arreglos-florales-con-peluche, cajas, corporativos, ramos-de-flores, floreros, arreglos-florales-de-navidad
- **desayunos** → subcategorías: desayunos-criollos, desayunos-de-amor, desayunos-light, desayunos-tematicos
- **peluches**
- **arreglos-funebres** → subcategorías: cruces-funebres, lagrimas-funebres, coronas-para-difuntos, mantos-funebres
- **regalo-para-bebe**
- **cestas**
- **plantas** → subcategorías: terrarios, orquideas, suculentas
- **dia-de-la-madre**

## OCASIONES REALES (ids para productos_por_ocasion)
- id=1 → Cumpleaños
- id=2 → Aniversario
- id=3 → Felicitación
- id=4 → Nacimiento
- id=5 → Agradecimiento
- id=6 → Negocios
- id=7 → Otros

## ARREGLOS FÚNEBRES — cuándo mostrarlos (MUY IMPORTANTE)
La ocasión define si corresponden o no. Por defecto los productos fúnebres están
EXCLUIDOS de las búsquedas; solo se incluyen en contexto de luto/condolencias.

- **Contexto de condolencias** (el cliente menciona: fallecimiento, velorio, sepelio,
  difunto, "en paz descanse", pésame, luto, misa de difunto, corona/manto/cruz fúnebre):
  → usa `buscar_semantico` con `incluir_funebre: true`, o `catalogo_categoria` con slug
    `arreglos-funebres`. Responde con un tono respetuoso y sobrio (sin emojis festivos).

- **Cualquier otra ocasión** (cumpleaños, aniversario, felicitación, nacimiento, etc.):
  → NUNCA incluyas fúnebres. Deja `incluir_funebre` en false (su valor por defecto).

- **Si la consulta es ambigua y podría ser fúnebre o no** (ej: solo "arreglos florales"
  sin contexto): mantén los fúnebres excluidos (default seguro). Si dudas si es para
  un fallecimiento, una pregunta breve y delicada aclara: "¿Para qué ocasión es el arreglo? 🌷"
  — así sabes si corresponde mostrar arreglos de condolencias o no.

## PRECIOS Y MONEDA
- Los precios de productos vienen en **USD ($)** desde la API
- SIEMPRE muestra el precio en ambas monedas: USD y Soles (S/)
- Para convertir: multiplica el precio en USD × tipo de cambio actual (usa la herramienta `tipo_cambio`)
- Formato obligatorio al mostrar precios: **S/XX.XX ($XX.XX)**
- Ejemplo: "S/87.50 ($25.00)"
- Los precios de envío ya vienen en ambas monedas desde `distritos_cobertura`

## HORARIOS DE ATENCIÓN
- **Lunes a Viernes**: 7:00 am – 10:00 pm (hora Lima)
- **Sábados**: 7:00 am – 8:00 pm (hora Lima)
- Pedidos web: 24/7

## DELIVERY
- Entregas **lunes a domingo** (excepto feriados)
- Pedido el **mismo día** con coordinación previa por WhatsApp o teléfono
- **Desayunos sorpresa**: solicitar con **1 día de anticipación**
- Notificación al cliente por email y WhatsApp al realizar la entrega

## RANGOS HORARIOS DE ENTREGA
Al coordinar un pedido, el cliente puede elegir uno de estos rangos de llegada:

| Rango | Horario |
|---|---|
| Mañana temprano | 07:00 AM – 09:00 AM |
| Mañana | 09:00 AM – 11:00 AM |
| Mediodía | 11:00 AM – 02:00 PM |
| Tarde | 02:00 PM – 05:00 PM |
| Tarde-noche | 04:00 PM – 07:00 PM |

- Cuando el cliente pregunte **a qué hora llega** o quiera coordinar la entrega, preséntale estos rangos para que elija
- Si el cliente da una hora exacta (ej: "a las 3 pm"), ubícalo en el rango correspondiente ("Tarde: 02:00 PM – 05:00 PM") y confírmalo
- Para **desayunos sorpresa**: los rangos disponibles son Mañana temprano (07-09h) y Mañana (09-11h); aclarar que debe pedirse con 1 día de anticipación
- Para **cerrar el rango**, di algo como: "¿En qué horario prefieres que llegue? 🕐" y lista las opciones

## DETECCIÓN DE DISTRITOS
- Cuando el cliente mencione un lugar, barrio o zona junto a su pedido (ej: "para Comas", "en Miraflores", "delivery a San Isidro"), interpreta SIEMPRE como el distrito de entrega en Lima
- Inmediatamente llama `distritos_cobertura` para verificar si hay cobertura y obtener la tarifa
- Don Regalo SOLO entrega dentro de Lima Metropolitana — si el distrito no aparece en la respuesta de `distritos_cobertura`, responde: "Lo sentimos, por el momento solo realizamos delivery dentro de Lima Metropolitana 🙏"
- Si el distrito SÍ tiene cobertura, confirma la tarifa y continúa con la conversación
- Lima tiene 43 distritos — Comas, Ate, Chorrillos, La Molina, etc. son todos distritos válidos de Lima

## MÉTODOS DE PAGO (confirmar con `metodos_pago`)
- Tarjeta de crédito/débito vía **PayPal o Payu** (Visa, Mastercard, Amex, Discover)
- Depósito/Transferencia: **BCP, Scotiabank, Interbank, BBVA**
- **Yape / Plin** al número 943 113 807
- Transferencia internacional: Western Union, Xoom, Money Gram
- ⚠️ Pagos desde provincia: comisión adicional de S/7.50
- Después de depositar, enviar comprobante a ventas@donregalo.pe

## DEVOLUCIONES Y CANCELACIONES
- Cambios dentro de las **primeras 5 horas** tras la entrega (con justificación)
- Devolución por depósito en cuenta — no en efectivo
- Tarjeta: reembolso en ~2 días hábiles
- Cancelación de pedido: mínimo **1 día antes**, informar por teléfono (5351616) Y email (ventas@donregalo.pe)
- Cambios para pedido del día siguiente: hasta las **4:00 pm**
- Cambios para pedido del lunes: hasta el **sábado 11:00 am**

## CONTACTO
- 📱 WhatsApp: (+51) 977174485
- 📞 Teléfono: (511) 5351616 / 923149666
- 📧 Email: ventas@donregalo.pe
- 🌐 donregalo.pe

## ESTILO DE CONVERSACIÓN — MUY IMPORTANTE
- Responde SIEMPRE con UN solo mensaje corto
- Ante un saludo, responde SOLO: "¡Hola! 😊 ¿En qué te puedo ayudar hoy?"
- NO presentes capacidades ni servicios hasta que el cliente pregunte algo concreto
- Haz UNA pregunta a la vez
- Mensajes cortos: máximo 3-4 líneas por respuesta
- Usa emojis con moderación (1-2 por mensaje máximo)

## FORMATO AL LISTAR PRODUCTOS — OBLIGATORIO

Cuando listes múltiples productos, cada producto va en su propio bloque: primero la URL de imagen, luego el texto con viñeta. Deja una línea en blanco entre productos.

https://donregalo.pe/.../imagen1.jpg
• 🎁 *Nombre del producto* — S/XX.XX ($XX.XX)
  Descripción corta en una línea

https://donregalo.pe/.../imagen2.jpg
• 🎁 *Otro producto* — S/XX.XX ($XX.XX)
  Descripción corta en una línea

¿Quieres más detalles de alguno? 😊

Reglas de formato:
- Cada producto = su imagen_url en la línea anterior a la viñeta (•)
- Si un producto tiene imagen_url null/vacío → omite su línea de URL, solo escribe la viñeta
- Nunca escribas la etiqueta: solo la URL sola (sin "imagen_url:" ni texto extra)
- Precio siempre en ambas monedas: S/XX.XX ($XX.XX)
- Muestra SIEMPRE entre 4 y 5 productos si la herramienta devuelve esa cantidad o más — nunca cortes en 2 o 3 sin razón
- Si la herramienta devuelve menos de 4 productos, muéstralos todos igual
- La pregunta "¿Quieres más detalles de alguno? 😊" va al final, sola, sin URL

Si el cliente pide SOLO la foto de un producto:
→ Escribe ÚNICAMENTE la imagen_url en una sola línea. Sin nombre, sin precio, sin descripción.

## REGLAS
1. **Nunca inventes productos ni precios** — usa siempre las herramientas
2. **Si el cliente nombra un producto específico, búscalo YA** — no hagas más preguntas
3. **Solo pregunta lo que realmente necesitas** — no pidas datos que no usarás (ej: no pidas "código de producto", la API busca por nombre)
4. Tono cordial y cercano al cliente peruano
5. Si no sabes algo, PRIMERO consulta `buscar_conocimiento_equipo` (puede que el equipo ya lo haya respondido antes). Solo si tampoco aparece ahí, deriva: "Te comunico con nuestro equipo: WhatsApp (+51) 977174485"
6. Para rastrear pedido: pide email + código ANTES de llamar la herramienta
7. Para imágenes, usa SIEMPRE el campo `imagen_url` del producto que viene en las listas (buscar_semantico, buscar_productos, catalogo_categoria, productos_destacados, etc.) — NUNCA uses los campos del array `imagenes[]` que devuelve detalle_producto
8. **Eres una tienda de delivery de regalos — NUNCA preguntes:**
   - Cuántas personas van a comer o recibir el regalo
   - Restricciones alimentarias, alergias o preferencias de cocina
   - Si prefiere "casero", "a domicilio" o "restaurante" — Don Regalo SIEMPRE es delivery
   - Qué hora le gustaría servir el desayuno
   Si el cliente pregunta por personalización del producto, deriva al equipo: WhatsApp (+51) 977174485

## MEMORIA DEL CLIENTE
- Cuando el cliente revele datos útiles (su nombre, distrito de entrega, la ocasión que le interesa, un producto que le gustó), guárdalos con `guardar_datos_cliente` para recordarlos en futuras conversaciones
- Si ya conoces datos del cliente (aparecen al inicio como "DATOS CONOCIDOS DEL CLIENTE"), úsalos para personalizar y NO vuelvas a preguntarlos
- No anuncies que estás guardando datos — hazlo de forma natural y silenciosa

## PERSONALIZACIÓN DE BÚSQUEDAS
- Si en "DATOS CONOCIDOS DEL CLIENTE" hay gustos o preferencias durables (ej: le gustan los girasoles, prefiere chocolates, le gusta lo minimalista), pásalos en el parámetro `preferencias` de `buscar_semantico` para afinar las sugerencias a su gusto
- En `preferencias` resume SOLO gustos reales que conoces del cliente — nunca inventes preferencias
- `preferencias` afina el orden de los resultados, pero la consulta `q` (lo que pide AHORA) siempre manda: no fuerces un gusto pasado si no encaja con lo que busca hoy
- Cuando muestres un producto y el cliente quiera ver más opciones parecidas, usa `productos_similares` con el `id_producto` de ese producto"""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/debug-webhook")
async def debug_webhook(request: Request):
    """Endpoint temporal para inspeccionar el payload de Chatwoot."""
    payload = await request.json()
    message = payload.get("data") or payload
    log.info("[DEBUG] payload=%s", json.dumps(payload, default=str))
    return {
        "event":              payload.get("event"),
        "message_type":       message.get("message_type"),
        "sender_type":        message.get("sender", {}).get("type"),
        "labels":             message.get("conversation", {}).get("labels"),
        "content":            message.get("content"),
        "content_attributes": message.get("content_attributes"),
    }


# Conversaciones ya procesadas para captura de conocimiento (evita duplicar
# cuando Chatwoot dispara varios conversation_updated por la misma resolución)
_kb_captured: set[int] = set()


async def _handle_conversation_event(payload: dict) -> dict:
    """Cuando una conversación se resuelve, captura en segundo plano el
    conocimiento que aportó el vendedor humano (Nivel B)."""
    data = payload.get("data") or payload
    status = data.get("status") or payload.get("status")
    conversation_id = data.get("id") or payload.get("id")

    if status != "resolved":
        return {"status": "ignored", "reason": f"status={status!r}"}
    if not conversation_id:
        return {"status": "ignored", "reason": "sin conversation_id"}
    if conversation_id in _kb_captured:
        return {"status": "ignored", "reason": "ya capturada"}

    _kb_captured.add(conversation_id)
    # No bloquear el webhook: capturar en segundo plano
    asyncio.create_task(knowledge.capturar_de_conversacion(conversation_id))
    return {"status": "capturing", "conversation_id": conversation_id}


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    event = payload.get("event")

    # Conversación resuelta → capturar el conocimiento que aportó el vendedor
    if event in ("conversation_status_changed", "conversation_resolved", "conversation_updated"):
        return await _handle_conversation_event(payload)

    if event != "message_created":
        return {"status": "ignored", "reason": f"event {event!r} no manejado"}

    # Chatwoot envía los campos directamente en la raíz (sin wrapper "data")
    message = payload.get("data") or payload

    # Chatwoot envía message_type como int (0=incoming, 1=outgoing) o string
    msg_type = message.get("message_type")
    if msg_type not in ("incoming", 0):
        return {"status": "ignored", "reason": f"not incoming (type={msg_type!r})"}

    sender_type = message.get("sender", {}).get("type", "")
    if sender_type in ("agent_bot", "agent"):
        return {"status": "ignored", "reason": "sent by agent"}

    conversation   = message.get("conversation", {})
    conversation_id = conversation.get("id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="No conversation_id in payload")

    # El bot solo responde si la conversación tiene la etiqueta activa
    labels = conversation.get("labels") or []
    if BOT_ACTIVE_LABEL not in labels:
        log.info("[INACTIVE] conversation=%s labels=%s", conversation_id, labels)
        return {"status": "ignored", "reason": "bot not active"}

    content     = message.get("content") or ""
    attachments = message.get("attachments") or []

    # Si el cliente citó/respondió a un mensaje específico de WhatsApp,
    # Chatwoot lo indica en content_attributes.in_reply_to (ID del mensaje citado)
    in_reply_to_id = (message.get("content_attributes") or {}).get("in_reply_to")

    # id del contacto para la memoria de largo plazo
    sender_meta = conversation.get("meta", {}).get("sender", {})
    contact_id = sender_meta.get("id") or message.get("sender", {}).get("id")

    # Identificador de WhatsApp (ej: 51946344918@s.whatsapp.net) para Evolution
    wa_identifier = (
        sender_meta.get("identifier")
        or message.get("sender", {}).get("identifier")
        or ""
    )
    wa_number = wa_identifier.split("@")[0] if wa_identifier else ""

    log.info(
        "[IN] conversation=%s contact=%s content=%r attachments=%d in_reply_to=%s",
        conversation_id, contact_id, content, len(attachments), in_reply_to_id,
    )

    # Convertir el mensaje entrante en partes de contenido (texto / imagen)
    parts = await _message_to_parts(content, attachments)

    # Bufferizar: acumular y reprogramar el flush tras BUFFER_SECONDS de silencio
    async with _buffers_lock:
        buf = _buffers.get(conversation_id)
        if buf and buf.get("task"):
            buf["task"].cancel()
        if not buf:
            buf = {"parts": [], "contact_id": contact_id, "wa_number": wa_number}
            _buffers[conversation_id] = buf
        buf["parts"].extend(parts)
        buf["contact_id"] = contact_id
        buf["wa_number"] = wa_number
        # Guardar el mensaje citado solo si viene en este turno
        if in_reply_to_id:
            buf["in_reply_to_id"] = in_reply_to_id
        buf["task"] = asyncio.create_task(_flush_after_delay(conversation_id))

    return {"status": "buffered"}


# ─── Buffer: conversión, debounce y flush ─────────────────────────────────────

async def _message_to_parts(content: str, attachments: list) -> list[dict]:
    """Convierte un mensaje entrante de Chatwoot en partes de contenido OpenAI
    (lista de {type: text} y/o {type: image_url})."""
    if not attachments:
        return [{"type": "text", "text": content}] if content else []

    att      = attachments[0]
    att_type = att.get("file_type", "")
    att_url  = att.get("data_url") or att.get("download_url", "")
    att_name = (att.get("file_name") or att_url).lower()

    # ── AUDIO → transcribir con Whisper ──────────────────────────────────────
    if att_type == "audio":
        audio_bytes = await _download(att_url)
        if audio_bytes:
            transcription = await _transcribe_audio(audio_bytes, att_name)
            if transcription:
                log.info("[AUDIO] transcription=%r", transcription)
                text = f"[Nota de voz transcrita]: {transcription}"
                return [{"type": "text", "text": f"{content}\n{text}" if content else text}]
            return [{"type": "text", "text": content or "[Nota de voz no transcribible]"}]
        return [{"type": "text", "text": content or "[Nota de voz no descargable]"}]

    # ── IMAGEN → visión con base64 ────────────────────────────────────────────
    if att_type == "image":
        image_bytes = await _download(att_url)
        if image_bytes:
            if att_name.endswith(".png"):
                mime = "image/png"
            elif att_name.endswith(".webp"):
                mime = "image/webp"
            elif att_name.endswith(".gif"):
                mime = "image/gif"
            else:
                mime = "image/jpeg"
            b64 = base64.b64encode(image_bytes).decode()
            return [
                {"type": "text", "text": content or "El usuario envió una imagen. Descríbela y responde apropiadamente."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        return [{"type": "text", "text": content or "[Imagen no descargable]"}]

    # ── PDF → extraer texto ───────────────────────────────────────────────────
    if att_type == "file" and att_name.endswith(".pdf"):
        pdf_bytes = await _download(att_url)
        if pdf_bytes:
            pdf_text = _extract_pdf_text(pdf_bytes)
            if pdf_text:
                log.info("[PDF] extracted %d chars", len(pdf_text))
                prefix = f"{content}\n\n" if content else ""
                return [{"type": "text", "text": f"{prefix}[Contenido del PDF]:\n{pdf_text}"}]
            return [{"type": "text", "text": content or "[PDF sin texto extraíble]"}]
        return [{"type": "text", "text": content or "[PDF no descargable]"}]

    # ── Otro tipo de archivo ──────────────────────────────────────────────────
    return [{"type": "text", "text": content or f"[Archivo adjunto de tipo: {att_type}]"}]


def _collapse_parts(parts: list[dict]) -> str | list[dict]:
    """Combina las partes acumuladas en el contenido de un único mensaje user.

    Si no hay imágenes → une los textos en un string.
    Si hay imágenes → devuelve la lista (texto unido + las imágenes)."""
    texts  = [p["text"] for p in parts if p.get("type") == "text" and p.get("text")]
    images = [p for p in parts if p.get("type") == "image_url"]
    joined = "\n".join(texts)
    if not images:
        return joined
    content = []
    if joined:
        content.append({"type": "text", "text": joined})
    content.extend(images)
    return content


def _human_delay(text: str) -> float:
    """Calcula una pausa proporcional a la longitud del texto, acotada
    entre TYPING_MIN_DELAY y TYPING_MAX_DELAY, para simular escritura humana."""
    secs = len(text or "") * TYPING_SECONDS_PER_CHAR
    return max(TYPING_MIN_DELAY, min(secs, TYPING_MAX_DELAY))


async def _flush_after_delay(conversation_id: int) -> None:
    """Espera el silencio y luego procesa el buffer. Cancelable si llega otro mensaje."""
    try:
        await asyncio.sleep(BUFFER_SECONDS)
    except asyncio.CancelledError:
        return
    await _flush_buffer(conversation_id)


async def _flush_buffer(conversation_id: int) -> None:
    """Procesa todos los mensajes acumulados de una conversación como uno solo."""
    async with _buffers_lock:
        buf = _buffers.pop(conversation_id, None)
    if not buf or not buf.get("parts"):
        return

    contact_id     = buf["contact_id"]
    wa_number      = buf.get("wa_number", "")
    in_reply_to_id = buf.get("in_reply_to_id")
    user_content   = _collapse_parts(buf["parts"])
    if not user_content:
        return

    # Si el cliente respondió a un mensaje específico (WhatsApp reply), inyectar
    # el texto del mensaje citado para que el agente sepa a qué producto se refiere.
    if in_reply_to_id:
        quoted = await _get_quoted_message(conversation_id, in_reply_to_id)
        if quoted:
            quoted_short = quoted[:400] + "…" if len(quoted) > 400 else quoted
            prefix = f"[El cliente está respondiendo al mensaje: «{quoted_short}»]\n"
            if isinstance(user_content, str):
                user_content = prefix + user_content
            else:
                user_content = [{"type": "text", "text": prefix}] + user_content

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Memoria de largo plazo: inyectar el perfil conocido del cliente
    profile = await _get_contact_attributes(contact_id) if contact_id else {}
    if profile:
        log.info("[MEM] contact=%s profile=%s", contact_id, profile)
        datos = "\n".join(f"- {k}: {v}" for k, v in profile.items() if v)
        messages.append({
            "role": "system",
            "content": (
                "DATOS CONOCIDOS DEL CLIENTE (de conversaciones previas):\n"
                f"{datos}\n"
                "Úsalos para personalizar la atención y NO vuelvas a preguntar lo que ya sabes. "
                "Si aprendes datos nuevos o corregidos, guárdalos con `guardar_datos_cliente`."
            ),
        })

    # Memoria de corto plazo: historial reciente de la conversación (24h)
    history = await _get_conversation_history(conversation_id)
    if history:
        log.info("[CTX] conversation=%s history=%d mensajes", conversation_id, len(history))
        messages.extend(history)

    messages.append({"role": "user", "content": user_content})

    # Mostrar "escribiendo…" mientras el agente procesa y envía
    await _set_typing(conversation_id, True)
    try:
        reply = await _run_agent(messages, contact_id, conversation_id)

        if reply:
            log.info("[OUT] conversation=%s reply=%r", conversation_id, reply)
            for segment in _split_reply(reply):
                if segment["type"] == "image":
                    # Pausa breve según el caption antes de la imagen
                    await asyncio.sleep(_human_delay(segment.get("caption", "")))
                    await _send_image(conversation_id, wa_number, segment["url"], segment["caption"])
                else:
                    await asyncio.sleep(_human_delay(segment["text"]))
                    await _send_message(conversation_id, segment["text"])
    finally:
        await _set_typing(conversation_id, False)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _normalize_chatwoot_url(url: str) -> str:
    """Reescribe el host del adjunto al de CHATWOOT_URL.

    Chatwoot a veces devuelve el data_url con el hostname interno del contenedor
    (con guiones bajos) que da 404. Forzamos el host público de CHATWOOT_URL.
    """
    if not CHATWOOT_URL or not url:
        return url
    base = urlsplit(CHATWOOT_URL)
    parts = urlsplit(url)
    # Reemplaza scheme + host (netloc) por los de CHATWOOT_URL, conserva path/query
    return urlunsplit((base.scheme, base.netloc, parts.path, parts.query, parts.fragment))


async def _download(url: str) -> bytes | None:
    """Descarga un archivo desde Chatwoot usando el token de acceso."""
    url = _normalize_chatwoot_url(url)
    try:
        # verify=False por certificado SSL interno de EasyPanel (hostname mismatch)
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            r = await client.get(
                url,
                headers={"api_access_token": CHATWOOT_API_TOKEN},
                follow_redirects=True,
            )
            r.raise_for_status()
            return r.content
    except Exception as e:
        log.error("Error descargando archivo %s: %s", url, e)
        return None


async def _transcribe_audio(audio_bytes: bytes, filename: str) -> str | None:
    """Transcribe una nota de voz usando OpenAI Whisper."""
    # Determinar extensión para Whisper (soporta: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg)
    ext = "ogg"
    for supported in ("mp3", "mp4", "m4a", "wav", "webm", "ogg", "mpeg"):
        if filename.endswith(f".{supported}"):
            ext = supported
            break

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": (f"audio.{ext}", audio_bytes, f"audio/{ext}")},
                data={"model": "whisper-1", "language": "es"},
            )
            r.raise_for_status()
            return r.json().get("text", "").strip()
    except Exception as e:
        log.error("Error transcribiendo audio: %s", e)
        return None


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extrae el texto de un PDF usando pypdf."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages  = [page.extract_text() or "" for page in reader.pages]
        text   = "\n\n".join(p.strip() for p in pages if p.strip())

        # Evita exceder el límite de tokens del modelo con PDFs muy largos
        if len(text) > PDF_MAX_CHARS:
            text = text[:PDF_MAX_CHARS] + "\n\n[...documento truncado por longitud...]"
        return text
    except Exception as e:
        log.error("Error extrayendo texto del PDF: %s", e)
        return ""


MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "6"))


# Mensaje cálido de "ya voy" que se envía al cliente mientras el agente trabaja,
# según la herramienta que está por usar. Se manda UNA sola vez por respuesta.
# Las herramientas rápidas/internas (no listadas) no disparan ningún mensaje.
_FILLER_BY_TOOL: dict[str, list[str]] = {
    "buscar_semantico":     ["¡Genial! Déjame buscarte las mejores opciones 🎁",
                             "¡Claro! Ya te busco algo perfecto 😍"],
    "buscar_productos":     ["Déjame buscar eso para ti 🔎",
                             "¡Voy a revisar qué tenemos! 🎁"],
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
    "detalle_producto":     ["¡Claro! Déjame traerte los detalles 😊",
                             "Un momentito, reviso ese producto 🎁"],
    "distritos_cobertura":  ["Déjame revisar la cobertura de tu zona 🛵",
                             "Ya verifico si llegamos a tu distrito 😊"],
    "metodos_pago":         ["Déjame contarte las formas de pago 💳"],
    "rastrear_pedido":      ["Déjame revisar el estado de tu pedido 📦"],
    "buscar_conocimiento_equipo": ["Déjame verificar eso para darte la mejor respuesta 😊",
                                   "Permíteme revisarlo un momento 🙌"],
}


def _filler_for_tools(tool_calls: list) -> str | None:
    """Devuelve un mensaje de espera para la primera herramienta lenta del turno."""
    import random
    for call in tool_calls:
        fn = call.get("function", {}).get("name", "")
        opciones = _FILLER_BY_TOOL.get(fn)
        if opciones:
            return random.choice(opciones)
    return None


async def _run_agent(messages: list, contact_id: int | None = None,
                     conversation_id: int | None = None) -> str | None:
    """Bucle agéntico: llama al modelo, ejecuta herramientas y repite hasta
    obtener una respuesta final de texto."""
    all_tools = TOOLS + ([MEMORY_TOOL] if contact_id else [])
    filler_sent = False
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(MAX_TOOL_ROUNDS):
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": OPENAI_MODEL,
                        "messages": messages,
                        "tools": all_tools,
                        "tool_choice": "auto",
                    },
                )
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]

                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    return msg.get("content")

                # Registrar el turno del asistente con sus tool_calls
                messages.append(msg)

                # Avisar al cliente "ya voy" (una sola vez) según la herramienta
                if not filler_sent and conversation_id is not None:
                    filler = _filler_for_tools(tool_calls)
                    if filler:
                        await _send_message(conversation_id, filler)
                        await _set_typing(conversation_id, True)  # seguir "escribiendo…"
                        filler_sent = True

                # Ejecutar cada herramienta y agregar su resultado
                for call in tool_calls:
                    fn   = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    log.info("[TOOL] %s args=%s", fn, args)

                    # La herramienta de memoria se maneja localmente (necesita contact_id)
                    if fn == "guardar_datos_cliente":
                        result = await _save_contact_attributes(contact_id, args)
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


# Detecta una URL de imagen al inicio de una línea
_IMG_LINE = re.compile(r'^\s*(https?://\S+\.(?:jpg|jpeg|png|webp))\s*$', re.IGNORECASE)


def _split_reply(reply: str) -> list[dict]:
    """Divide la respuesta del agente en segmentos de imagen y texto.

    Patrón esperado del system prompt:
      URL de imagen (línea sola)
      • texto del producto
      ...

    Cada URL de imagen se convierte en un segmento {type: image} con el texto
    que la sigue como caption. El texto restante sin imagen va como {type: text}.
    """
    lines = reply.split("\n")
    segments: list[dict] = []
    pending_image: str | None = None
    text_buffer: list[str] = []

    def flush_text():
        text = "\n".join(text_buffer).strip()
        text_buffer.clear()
        return text

    for line in lines:
        m = _IMG_LINE.match(line)
        if m:
            # Antes de una imagen nueva, cierra el bloque previo
            if pending_image is not None:
                segments.append({
                    "type": "image",
                    "url": pending_image,
                    "caption": flush_text(),
                })
            else:
                leftover = flush_text()
                if leftover:
                    segments.append({"type": "text", "text": leftover})
            pending_image = m.group(1)
        else:
            text_buffer.append(line)

    # Cierra el último bloque
    if pending_image is not None:
        segments.append({
            "type": "image",
            "url": pending_image,
            "caption": flush_text(),
        })
    else:
        leftover = flush_text()
        if leftover:
            segments.append({"type": "text", "text": leftover})

    return segments or [{"type": "text", "text": reply}]


async def _send_message(conversation_id: int, content: str) -> None:
    """Envía un mensaje saliente a la conversación en Chatwoot."""
    url = (
        f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                headers={
                    "api_access_token": CHATWOOT_API_TOKEN,
                    "Content-Type": "application/json",
                },
                json={
                    "content": content,
                    "message_type": "outgoing",
                    "private": False,
                    # Marca para distinguir mensajes del bot vs del vendedor humano
                    "content_attributes": {"bot": True},
                },
            )
            r.raise_for_status()
    except Exception as e:
        log.error("Error enviando mensaje a conversación %s: %s", conversation_id, e)


async def _get_quoted_message(conversation_id: int, message_id: int) -> str:
    """Devuelve el texto del mensaje que el cliente citó (WhatsApp reply).
    Hace la misma llamada que _get_conversation_history pero solo busca un ID."""
    url = (
        f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            r = await client.get(url, headers={"api_access_token": CHATWOOT_API_TOKEN})
            r.raise_for_status()
            for m in r.json().get("payload", []):
                if m.get("id") == message_id:
                    return (m.get("content") or "").strip()
    except Exception as e:
        log.warning("No se pudo leer mensaje citado id=%s: %s", message_id, e)
    return ""


async def _get_conversation_history(conversation_id: int) -> list[dict]:
    """Lee el transcript reciente de Chatwoot como memoria de corto plazo.

    Devuelve una lista de mensajes en formato OpenAI ({role, content}) de las
    últimas MEMORY_WINDOW_HOURS horas. Excluye el turno actual del cliente
    (la racha final de mensajes 'user'), que se agrega aparte ya procesado.
    """
    url = (
        f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.get(url, headers={"api_access_token": CHATWOOT_API_TOKEN})
            r.raise_for_status()
            payload = r.json().get("payload", [])
    except Exception as e:
        log.error("Error leyendo historial %s: %s", conversation_id, e)
        return []

    # Ordenar cronológicamente (más antiguo primero)
    payload.sort(key=lambda m: m.get("created_at") or 0)

    cutoff  = time.time() - MEMORY_WINDOW_HOURS * 3600
    history: list[dict] = []
    for m in payload:
        if m.get("private"):
            continue
        mtype = m.get("message_type")
        if mtype not in (0, 1):  # 0=incoming (cliente), 1=outgoing (bot/agente)
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        created = m.get("created_at")
        if isinstance(created, (int, float)) and created < cutoff:
            continue
        history.append({
            "role":    "user" if mtype == 0 else "assistant",
            "content": content,
        })

    # Quitar la racha final de mensajes 'user' = el turno actual (se agrega aparte)
    while history and history[-1]["role"] == "user":
        history.pop()

    # Limitar a los últimos N mensajes para controlar tokens
    if len(history) > MEMORY_MAX_MESSAGES:
        history = history[-MEMORY_MAX_MESSAGES:]

    return history


async def _set_typing(conversation_id: int, on: bool) -> None:
    """Activa/desactiva el indicador 'escribiendo…' en Chatwoot."""
    url = (
        f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}/toggle_typing_status"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            r = await client.post(
                url,
                headers={
                    "api_access_token": CHATWOOT_API_TOKEN,
                    "Content-Type": "application/json",
                },
                json={"typing_status": "on" if on else "off"},
            )
            r.raise_for_status()
    except Exception as e:
        # No es crítico; si falla solo no se ve el indicador
        log.warning("No se pudo cambiar typing status (%s): %s", conversation_id, e)


async def _get_contact_attributes(contact_id: int) -> dict:
    """Lee los custom_attributes (perfil de largo plazo) de un contacto."""
    url = f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/{contact_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.get(url, headers={"api_access_token": CHATWOOT_API_TOKEN})
            r.raise_for_status()
            payload = r.json().get("payload", {})
            return payload.get("custom_attributes") or {}
    except Exception as e:
        log.error("Error leyendo contacto %s: %s", contact_id, e)
        return {}


async def _save_contact_attributes(contact_id: int, new_attrs: dict) -> str:
    """Fusiona y guarda datos del cliente en custom_attributes del contacto.

    Devuelve un string JSON con el resultado (lo consume el modelo como tool result).
    """
    # Limpiar campos vacíos
    new_attrs = {k: v for k, v in (new_attrs or {}).items() if v not in (None, "")}
    if not new_attrs:
        return json.dumps({"ok": False, "motivo": "sin datos para guardar"})

    # La nota episódica se acumula aparte (no es un campo de sobrescritura)
    nota = new_attrs.pop("nota", None)

    url = f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/{contact_id}"
    try:
        # Datos estables (nombre, distrito): se fusionan sobrescribiendo
        current = await _get_contact_attributes(contact_id)
        merged  = {**current, **new_attrs}

        # Nota episódica: se AÑADE al historial con fecha, conservando lo anterior
        if nota:
            from datetime import date
            entry    = f"[{date.today().isoformat()}] {nota}"
            historial = (current.get("notas") or "").strip()
            historial = f"{historial}\n{entry}" if historial else entry
            # Cap para que no crezca sin límite: conserva las entradas más recientes
            if len(historial) > 2000:
                historial = "…" + historial[-2000:]
            merged["notas"] = historial

        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            r = await client.put(
                url,
                headers={
                    "api_access_token": CHATWOOT_API_TOKEN,
                    "Content-Type": "application/json",
                },
                json={"custom_attributes": merged},
            )
            r.raise_for_status()
        guardado = {**new_attrs, **({"nota": nota} if nota else {})}
        log.info("[MEM] guardado contact=%s attrs=%s", contact_id, guardado)
        return json.dumps({"ok": True, "guardado": guardado})
    except Exception as e:
        log.error("Error guardando contacto %s: %s", contact_id, e)
        return json.dumps({"ok": False, "motivo": str(e)})


async def _send_image(conversation_id: int, wa_number: str, image_url: str, caption: str = "") -> None:
    """Envía una imagen de producto a WhatsApp.

    Vía preferida: Evolution API directo con la URL pública (el puente
    Chatwoot no reenvía adjuntos salientes de forma fiable).
    Fallback: subir el adjunto a Chatwoot.
    """
    # Diagnóstico: mostrar qué condición de Evolution falta (si alguna)
    log.info(
        "[IMG] evolution_check url=%s key=%s instance=%s wa_number=%r",
        bool(EVOLUTION_API_URL), bool(EVOLUTION_API_KEY),
        EVOLUTION_INSTANCE or "(vacío)", wa_number,
    )

    # ── Vía Evolution API (directo a WhatsApp) ────────────────────────────────
    if EVOLUTION_API_URL and EVOLUTION_API_KEY and EVOLUTION_INSTANCE and wa_number:
        endpoint = f"{EVOLUTION_API_URL}/message/sendMedia/{EVOLUTION_INSTANCE}"
        body = {
            "number":    wa_number,
            "mediatype": "image",
            "media":     image_url,
            "fileName":  image_url.split("/")[-1].split("?")[0] or "imagen.jpg",
        }
        if caption:
            body["caption"] = caption
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    endpoint,
                    headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                    json=body,
                )
                r.raise_for_status()
            log.info("[IMG] enviada vía Evolution a %s: %s", wa_number, image_url)
            return
        except Exception as e:
            log.error("Error Evolution sendMedia (%s): %s — intentando Chatwoot", image_url, e)

    # ── Fallback: subir adjunto a Chatwoot ────────────────────────────────────
    url = (
        f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            img = await client.get(image_url, follow_redirects=True)
            img.raise_for_status()
            image_bytes = img.content

        filename = image_url.split("/")[-1].split("?")[0] or "imagen.jpg"
        mime = img.headers.get("content-type", "image/jpeg")

        data = {"message_type": "outgoing", "private": "false"}
        if caption:
            data["content"] = caption

        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            r = await client.post(
                url,
                headers={"api_access_token": CHATWOOT_API_TOKEN},
                data=data,
                files={"attachments[]": (filename, image_bytes, mime)},
            )
            r.raise_for_status()
    except Exception as e:
        log.error("Error enviando imagen %s a conversación %s: %s", image_url, conversation_id, e)
        # Último recurso: enviar la URL como texto para no perder el contenido
        fallback = f"{caption}\n{image_url}".strip() if caption else image_url
        await _send_message(conversation_id, fallback)


# Correr con:
# uvicorn main:app --host 0.0.0.0 --port 8000
