import io
import os
import re
import json
import base64
import logging
import httpx
from urllib.parse import urlsplit, urlunsplit
from pypdf import PdfReader
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

from tools import TOOLS, execute_tool

load_dotenv()

CHATWOOT_URL        = os.getenv("CHATWOOT_URL", "").rstrip("/")
CHATWOOT_API_TOKEN  = os.getenv("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BOT_ACTIVE_LABEL    = os.getenv("BOT_ACTIVE_LABEL", "agente_on")

# Máximo de caracteres a extraer de un PDF (protege el límite de tokens)
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "30000"))

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
| `listar_categorias` | Cuando pregunten qué hay disponible o quieran explorar el catálogo |
| `listar_ocasiones` | Antes de buscar por ocasión (cumpleaños, aniversario, etc.) |
| `buscar_productos` | Cuando mencionen un producto, flor, característica o precio específico |
| `catalogo_categoria` | Cuando pidan ver productos de una categoría — usa el slug de `listar_categorias` |
| `productos_destacados` | Cuando no sepan qué elegir o pidan recomendaciones |
| `productos_oferta` | Cuando busquen ofertas, descuentos o algo económico |
| `detalle_producto` | Cuando quieran saber más de un producto — usa el id_producto de búsquedas previas |
| `productos_por_ocasion` | Cuando mencionen para qué ocasión es el regalo — usa el id de `listar_ocasiones` |
| `distritos_cobertura` | Cuando pregunten si llegan a su zona o cuánto cuesta el envío |
| `metodos_pago` | Cuando pregunten cómo pagar |
| `tipo_cambio` | Para convertir precios USD a Soles |
| `rastrear_pedido` | Cuando quieran el estado de su pedido — SIEMPRE pide email + código primero |
| `guardar_datos_cliente` | Cuando el cliente revele datos ESTABLES: su nombre, su distrito habitual o una preferencia durable — guárdalo para recordarlo después |

## FLUJO RECOMENDADO PARA SUGERIR PRODUCTOS

**Si el cliente menciona un producto específico por nombre** (ej: "quiero el desayuno cars"):
→ Llama `buscar_productos` directamente — NO preguntes nada

**Si el cliente menciona una categoría** (ej: "busco desayunos", "quiero flores", "tienen peluches"):
→ Llama `catalogo_categoria` con el slug correspondiente — NO preguntes nada
→ Si también menciona una ocasión (ej: "desayunos para el día del padre") → usa ambos datos para la búsqueda

**Si el cliente menciona una ocasión** (ej: "es para cumpleaños", "para un aniversario"):
→ Llama `productos_por_ocasion` con el id correcto — NO preguntes nada más

**Si el cliente es completamente vago** ("quiero un regalo", "algo bonito", sin dar categoría ni ocasión):
→ Pregunta UNA sola cosa: "¿Para qué ocasión es el regalo? 😊"
→ Con la respuesta, llama `productos_por_ocasion` o `catalogo_categoria`
→ NO preguntes presupuesto, cantidad, restricciones ni preferencias de ningún tipo

**Para ver detalles de un producto ya encontrado:**
→ Llama `detalle_producto` con su `id_producto`

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
- Máximo 4-5 productos por mensaje
- La pregunta "¿Quieres más detalles de alguno? 😊" va al final, sola, sin URL

Si el cliente pide SOLO la foto de un producto:
→ Escribe ÚNICAMENTE la imagen_url en una sola línea. Sin nombre, sin precio, sin descripción.

## REGLAS
1. **Nunca inventes productos ni precios** — usa siempre las herramientas
2. **Si el cliente nombra un producto específico, búscalo YA** — no hagas más preguntas
3. **Solo pregunta lo que realmente necesitas** — no pidas datos que no usarás (ej: no pidas "código de producto", la API busca por nombre)
4. Tono cordial y cercano al cliente peruano
5. Si no sabes algo, deriva: "Te comunico con nuestro equipo: WhatsApp (+51) 977174485"
6. Para rastrear pedido: pide email + código ANTES de llamar la herramienta
7. Para imágenes, usa SIEMPRE el campo `imagen_url` del producto que viene en las listas (buscar_productos, catalogo_categoria, productos_destacados, etc.) — NUNCA uses los campos del array `imagenes[]` que devuelve detalle_producto
8. **Eres una tienda de delivery de regalos — NUNCA preguntes:**
   - Cuántas personas van a comer o recibir el regalo
   - Restricciones alimentarias, alergias o preferencias de cocina
   - Si prefiere "casero", "a domicilio" o "restaurante" — Don Regalo SIEMPRE es delivery
   - Qué hora le gustaría servir el desayuno
   Si el cliente pregunta por personalización del producto, deriva al equipo: WhatsApp (+51) 977174485

## MEMORIA DEL CLIENTE
- Cuando el cliente revele datos útiles (su nombre, distrito de entrega, la ocasión que le interesa, un producto que le gustó), guárdalos con `guardar_datos_cliente` para recordarlos en futuras conversaciones
- Si ya conoces datos del cliente (aparecen al inicio como "DATOS CONOCIDOS DEL CLIENTE"), úsalos para personalizar y NO vuelvas a preguntarlos
- No anuncies que estás guardando datos — hazlo de forma natural y silenciosa"""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/debug-webhook")
async def debug_webhook(request: Request):
    """Endpoint temporal para inspeccionar el payload de Chatwoot."""
    payload = await request.json()
    message = payload.get("data", {})
    log.info("[DEBUG] payload=%s", payload)
    return {
        "event":        payload.get("event"),
        "message_type": message.get("message_type"),
        "sender_type":  message.get("sender", {}).get("type"),
        "labels":       message.get("conversation", {}).get("labels"),
        "content":      message.get("content"),
    }


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()

    if payload.get("event") != "message_created":
        return {"status": "ignored", "reason": "event != message_created"}

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

    # id del contacto para la memoria de largo plazo
    contact_id = (
        conversation.get("meta", {}).get("sender", {}).get("id")
        or message.get("sender", {}).get("id")
    )

    log.info(
        "[IN] conversation=%s contact=%s content=%r attachments=%d",
        conversation_id, contact_id, content, len(attachments),
    )

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

    if attachments:
        att      = attachments[0]
        att_type = att.get("file_type", "")
        att_url  = att.get("data_url") or att.get("download_url", "")
        att_name = (att.get("file_name") or att_url).lower()

        # ── AUDIO → transcribir con Whisper ──────────────────────────────────
        if att_type == "audio":
            audio_bytes = await _download(att_url)
            if audio_bytes:
                transcription = await _transcribe_audio(audio_bytes, att_name)
                if transcription:
                    log.info("[AUDIO] transcription=%r", transcription)
                    user_text = f"[Nota de voz transcrita]: {transcription}"
                    if content:
                        user_text = f"{content}\n{user_text}"
                    messages.append({"role": "user", "content": user_text})
                else:
                    messages.append({
                        "role": "user",
                        "content": content or "[El usuario envió una nota de voz pero no se pudo transcribir]",
                    })
            else:
                messages.append({
                    "role": "user",
                    "content": content or "[El usuario envió una nota de voz pero no se pudo descargar]",
                })

        # ── IMAGEN → visión con base64 ────────────────────────────────────────
        elif att_type == "image":
            image_bytes = await _download(att_url)
            if image_bytes:
                # Detectar mime type por extensión
                if att_name.endswith(".png"):
                    mime = "image/png"
                elif att_name.endswith(".webp"):
                    mime = "image/webp"
                elif att_name.endswith(".gif"):
                    mime = "image/gif"
                else:
                    mime = "image/jpeg"

                b64 = base64.b64encode(image_bytes).decode()
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": content or "El usuario envió una imagen. Descríbela y responde apropiadamente.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                })
            else:
                messages.append({
                    "role": "user",
                    "content": content or "[El usuario envió una imagen pero no se pudo descargar]",
                })

        # ── PDF → extraer texto ───────────────────────────────────────────────
        elif att_type == "file" and att_name.endswith(".pdf"):
            pdf_bytes = await _download(att_url)
            if pdf_bytes:
                pdf_text = _extract_pdf_text(pdf_bytes)
                if pdf_text:
                    log.info("[PDF] extracted %d chars", len(pdf_text))
                    user_text = (
                        f"{content}\n\n" if content else ""
                    ) + f"[Contenido del PDF]:\n{pdf_text}"
                    messages.append({"role": "user", "content": user_text})
                else:
                    messages.append({
                        "role": "user",
                        "content": content or "[El usuario envió un PDF pero no se pudo extraer el texto]",
                    })
            else:
                messages.append({
                    "role": "user",
                    "content": content or "[El usuario envió un PDF pero no se pudo descargar]",
                })

        # ── Otro tipo de archivo ──────────────────────────────────────────────
        else:
            messages.append({
                "role": "user",
                "content": content or f"[El usuario envió un archivo adjunto de tipo: {att_type}]",
            })

    else:
        messages.append({"role": "user", "content": content})

    reply = await _run_agent(messages, contact_id)

    if reply:
        log.info("[OUT] conversation=%s reply=%r", conversation_id, reply)
        # Divide la respuesta en segmentos (imágenes + texto) y los envía por separado
        for segment in _split_reply(reply):
            if segment["type"] == "image":
                await _send_image(conversation_id, segment["url"], segment["caption"])
            else:
                await _send_message(conversation_id, segment["text"])

    return {"status": "ok"}


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


async def _run_agent(messages: list, contact_id: int | None = None) -> str | None:
    """Bucle agéntico: llama al modelo, ejecuta herramientas y repite hasta
    obtener una respuesta final de texto."""
    all_tools = TOOLS + ([MEMORY_TOOL] if contact_id else [])
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
                },
            )
            r.raise_for_status()
    except Exception as e:
        log.error("Error enviando mensaje a conversación %s: %s", conversation_id, e)


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


async def _send_image(conversation_id: int, image_url: str, caption: str = "") -> None:
    """Descarga una imagen de producto y la envía como adjunto a Chatwoot."""
    url = (
        f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}/messages"
    )
    try:
        # Descargar la imagen del producto (donregalo.pe)
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            img = await client.get(image_url, follow_redirects=True)
            img.raise_for_status()
            image_bytes = img.content

        filename = image_url.split("/")[-1].split("?")[0] or "imagen.jpg"
        mime = img.headers.get("content-type", "image/jpeg")

        # Subir como adjunto multipart. content (caption) es opcional.
        data = {
            "message_type": "outgoing",
            "private":      "false",
        }
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
        # Fallback: enviar la URL como texto para no perder el contenido
        fallback = f"{caption}\n{image_url}".strip() if caption else image_url
        await _send_message(conversation_id, fallback)


# Correr con:
# uvicorn main:app --host 0.0.0.0 --port 8000
