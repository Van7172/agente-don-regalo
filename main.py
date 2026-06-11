import io
import os
import base64
import logging
import httpx
from pypdf import PdfReader
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

load_dotenv()

CHATWOOT_URL        = os.getenv("CHATWOOT_URL", "").rstrip("/")
CHATWOOT_API_TOKEN  = os.getenv("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BOT_ACTIVE_LABEL    = os.getenv("BOT_ACTIVE_LABEL", "agente_on")

# Máximo de caracteres a extraer de un PDF (protege el límite de tokens)
PDF_MAX_CHARS = int(os.getenv("PDF_MAX_CHARS", "30000"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI()

SYSTEM_PROMPT = (
    "Eres un asistente virtual de WhatsApp para DYTIA. "
    "Respondes de forma clara, útil y en español neutro. "
    "Eres amable, conciso y profesional."
)


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

    log.info(
        "[IN] conversation=%s content=%r attachments=%d",
        conversation_id, content, len(attachments),
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

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

    reply = await _call_llm(messages)

    if reply:
        await _send_message(conversation_id, reply)
        log.info("[OUT] conversation=%s reply=%r", conversation_id, reply)

    return {"status": "ok"}


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _download(url: str) -> bytes | None:
    """Descarga un archivo desde Chatwoot usando el token de acceso."""
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


async def _call_llm(messages: list) -> str | None:
    """Llama a la API de OpenAI y devuelve el texto de respuesta."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": messages,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.error("Error llamando al LLM: %s", e)
        return None


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


# Correr con:
# uvicorn main:app --host 0.0.0.0 --port 8000
