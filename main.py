import os
import base64
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

load_dotenv()

CHATWOOT_URL        = os.getenv("CHATWOOT_URL", "").rstrip("/")
CHATWOOT_API_TOKEN  = os.getenv("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.getenv("CHATWOOT_ACCOUNT_ID", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()

    # Solo procesar eventos de mensaje creado
    if payload.get("event") != "message_created":
        return {"status": "ignored", "reason": "event != message_created"}

    message = payload.get("data", {})

    # Solo mensajes entrantes (del usuario)
    if message.get("message_type") != "incoming":
        return {"status": "ignored", "reason": "not incoming"}

    # Ignorar mensajes enviados por el propio agente/bot (evita bucles)
    sender_type = message.get("sender", {}).get("type", "")
    if sender_type in ("agent_bot", "agent"):
        return {"status": "ignored", "reason": "sent by agent"}

    conversation_id = message.get("conversation", {}).get("id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="No conversation_id in payload")

    content     = message.get("content") or ""
    attachments = message.get("attachments") or []

    log.info(
        "[IN] conversation=%s content=%r attachments=%d",
        conversation_id, content, len(attachments),
    )

    # Construir mensajes para el LLM
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if attachments:
        att      = attachments[0]
        att_type = att.get("file_type", "")
        att_url  = att.get("data_url") or att.get("download_url", "")

        if att_type == "audio":
            audio_bytes = await _download(att_url)
            if audio_bytes:
                b64 = base64.b64encode(audio_bytes).decode()
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": content or "El usuario envió una nota de voz. Transcríbela y responde apropiadamente.",
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {"data": b64, "format": "ogg"},
                        },
                    ],
                })
            else:
                messages.append({
                    "role": "user",
                    "content": content or "[El usuario envió una nota de voz pero no se pudo descargar]",
                })

        elif att_type == "image":
            image_bytes = await _download(att_url)
            if image_bytes:
                b64      = base64.b64encode(image_bytes).decode()
                mime     = "image/jpeg"
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

        else:
            messages.append({
                "role": "user",
                "content": content or f"[Archivo adjunto: {att_type}]",
            })

    else:
        messages.append({"role": "user", "content": content})

    # Llamar al LLM vía OpenRouter
    reply = await _call_llm(messages)

    if reply:
        await _send_message(conversation_id, reply)
        log.info("[OUT] conversation=%s reply=%r", conversation_id, reply)

    return {"status": "ok"}


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _download(url: str) -> bytes | None:
    """Descarga un archivo desde Chatwoot usando el token de acceso."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
