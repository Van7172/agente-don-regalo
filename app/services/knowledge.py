"""
Aprendizaje del equipo de ventas (Nivel B) — sandbox CRM.

Captura pares pregunta/respuesta cuando un asesor humano atiende en el CRM,
los indexa en Qdrant (`respuestas_equipo`) y el agente los consulta con
`buscar_conocimiento_equipo`.
"""
import re
import json
import uuid
import asyncio
import logging
from datetime import date

import httpx
from sqlalchemy import select

from app.config import settings
from app.crm.models import Message
from app.db import SessionLocal
from app.tools.search import get_qdrant, embed

log = logging.getLogger(__name__)


# ─── Clasificación de mensajes CRM ────────────────────────────────────────────

def _classify(m: dict) -> str | None:
    """Clasifica un mensaje del CRM. None si debe ignorarse."""
    if (m.get("content") or "").strip() == "":
        return None
    direction = m.get("direction")
    sender = m.get("sender_type")
    if direction == "inbound":
        return "cliente"
    if direction == "outbound":
        if sender == "bot":
            return "bot"
        if sender == "agent":
            return "vendedor"
    return None


async def _fetch_messages(conversation_id: int) -> list[dict]:
    """Trae mensajes del CRM, ordenados cronológicamente."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        )
        rows = list(result.scalars().all())
    return [
        {
            "content": r.content,
            "direction": r.direction,
            "sender_type": r.sender_type,
        }
        for r in rows
    ]


def _build_transcript(messages: list[dict]) -> tuple[str, bool]:
    """Construye un transcript etiquetado y dice si hubo intervención humana."""
    lines: list[str] = []
    hay_vendedor = False
    for m in messages:
        tipo = _classify(m)
        if tipo is None:
            continue
        if tipo == "vendedor":
            hay_vendedor = True
        etiqueta = {"cliente": "CLIENTE", "vendedor": "VENDEDOR", "bot": "BOT"}[tipo]
        lines.append(f"{etiqueta}: {(m.get('content') or '').strip()}")
    return "\n".join(lines), hay_vendedor


# ─── Extracción de conocimiento con el LLM ────────────────────────────────────

_EXTRACT_SYSTEM = """Eres un analista de calidad de Don Regalo (tienda de regalos en Lima).
Recibes el transcript de una conversación de WhatsApp donde participaron el CLIENTE,
a veces el BOT y un VENDEDOR humano.

Tu tarea: extraer SOLO el conocimiento valioso que aportó el VENDEDOR y que serviría
para responder mejor a futuros clientes. Enfócate en:
- Respuestas a preguntas que el bot no supo o no cubría (políticas, dudas, casos especiales)
- Manejo de objeciones (precio, tiempos, desconfianza) que funcionó
- Datos operativos concretos (plazos, excepciones, coordinaciones, promociones puntuales)
- Frases de cierre o aclaraciones útiles

NO extraigas:
- Charla trivial, saludos, confirmaciones vacías
- Datos personales del cliente puntual (eso va a otro sistema)
- Cosas que el bot ya respondía bien por sí mismo
- Información sensible (números de cuenta completos, datos privados)

REGLA DE PRIVACIDAD (obligatoria): la pregunta y la respuesta deben ser GENÉRICAS.
- Reemplaza nombres propios de personas por "el cliente" o "un cliente".
- NUNCA incluyas teléfonos, emails, direcciones, distritos puntuales, DNI ni números
  de cuenta/tarjeta de un cliente. (Sí puedes incluir los datos de contacto PÚBLICOS
  de Don Regalo si son parte de la respuesta útil.)

Devuelve EXCLUSIVAMENTE un JSON con esta forma:
{"items": [{"pregunta": "...", "respuesta": "...", "categoria": "..."}]}

- "pregunta": la duda del cliente redactada de forma GENÉRICA y reutilizable (no con datos del cliente puntual)
- "respuesta": la respuesta del vendedor, concisa, reutilizable y en el tono cordial de la tienda
- "categoria": una etiqueta corta (ej: envios, pagos, plazos, objecion-precio, personalizacion)

Si no hay nada reutilizable, devuelve {"items": []}. No inventes."""


# ─── Filtro de datos personales (PII) ─────────────────────────────────────────
# Red de seguridad determinista: aunque el LLM debería genericizar, redactamos
# por regex cualquier email/teléfono/documento de una persona ANTES de indexar,
# para que datos de un cliente nunca lleguen a la base de conocimiento.
# Se preservan los datos PÚBLICOS del negocio (su WhatsApp, teléfono, email, Yape).

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Secuencia tipo teléfono/documento: 7 a 15 dígitos con espacios o guiones simples
# (no usa puntos, así no captura precios como 149.60 ni horas como 07:00).
_NUM_RE = re.compile(r"\+?\d(?:[\s-]?\d){6,14}")

# Datos públicos del negocio que SÍ deben conservarse (normalizados a solo dígitos).
_BUSINESS_PHONES = {
    "977174485", "51977174485",   # WhatsApp
    "923149666",                  # teléfono
    "943113807",                  # Yape / Plin
}
_BUSINESS_EMAILS = {"ventas@donregalo.pe"}


def _scrub_pii(text: str) -> str:
    """Redacta emails y números personales, conservando los datos del negocio."""
    if not text:
        return text

    def _email_sub(m: re.Match) -> str:
        return m.group(0) if m.group(0).lower() in _BUSINESS_EMAILS else "[dato omitido]"

    def _num_sub(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 7 or digits in _BUSINESS_PHONES:
            return m.group(0)
        return "[dato omitido]"

    text = _EMAIL_RE.sub(_email_sub, text)
    text = _NUM_RE.sub(_num_sub, text)
    return text


def _scrub_items(items: list[dict]) -> list[dict]:
    """Aplica el filtro de PII a la pregunta y la respuesta de cada item."""
    for it in items:
        it["pregunta"]  = _scrub_pii(it.get("pregunta", ""))
        it["respuesta"] = _scrub_pii(it.get("respuesta", ""))
    return items


async def _extract_qa(transcript: str) -> list[dict]:
    """Llama al LLM para extraer items de conocimiento del transcript."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": _EXTRACT_SYSTEM},
                        {"role": "user", "content": f"Transcript:\n\n{transcript}"},
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        data  = json.loads(content)
        items = data.get("items", [])
        validos = [
            it for it in items
            if isinstance(it, dict) and it.get("pregunta") and it.get("respuesta")
        ]
        # Red de seguridad: redactar PII aunque el LLM la haya dejado pasar.
        return _scrub_items(validos)
    except Exception as e:
        log.error("[KB] error extrayendo conocimiento: %s", e)
        return []


# ─── Indexado en Qdrant ───────────────────────────────────────────────────────

def _point_id(pregunta: str) -> str:
    """Id estable derivado de la pregunta normalizada → dedup entre conversaciones."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, pregunta.strip().lower()))


def ensure_kb_collection(qc) -> None:
    """Crea la colección de conocimiento si no existe."""
    from qdrant_client.models import Distance, VectorParams
    existing = [c.name for c in qc.get_collections().collections]
    if settings.kb_collection not in existing:
        log.info("[KB] creando colección '%s'", settings.kb_collection)
        qc.create_collection(
            collection_name=settings.kb_collection,
            vectors_config=VectorParams(size=settings.embed_dim, distance=Distance.COSINE),
        )


async def _index_items(items: list[dict], conversation_id: int | None) -> int:
    """Embebe las preguntas y hace upsert en Qdrant. Devuelve cuántos indexó."""
    from qdrant_client.models import PointStruct

    qc = get_qdrant()
    if qc is None:
        log.warning("[KB] Qdrant no configurado; no se indexa conocimiento.")
        return 0

    await asyncio.to_thread(ensure_kb_collection, qc)

    textos  = [f"{it['pregunta']}\n{it['respuesta']}" for it in items]
    vectors = await embed(textos)

    points = []
    for it, vec in zip(items, vectors):
        points.append(PointStruct(
            id=_point_id(it["pregunta"]),
            vector=vec,
            payload={
                "pregunta":        it["pregunta"],
                "respuesta":       it["respuesta"],
                "categoria":       it.get("categoria", ""),
                "conversation_id": conversation_id,
                "fecha":           date.today().isoformat(),
            },
        ))

    def _upsert():
        qc.upsert(collection_name=settings.kb_collection, points=points)

    await asyncio.to_thread(_upsert)
    return len(points)


# ─── Orquestador ──────────────────────────────────────────────────────────────

async def capturar_de_conversacion(conversation_id: int) -> int:
    """Captura conocimiento del vendedor de una conversación. Devuelve cuántos
    items se indexaron (0 si no hubo intervención humana o nada reutilizable)."""
    try:
        messages = await _fetch_messages(conversation_id)
    except Exception as e:
        log.error("[KB] no se pudo leer la conversación %s: %s", conversation_id, e)
        return 0

    transcript, hay_vendedor = _build_transcript(messages)
    if not hay_vendedor:
        log.info("[KB] conversación %s sin intervención humana; se omite.", conversation_id)
        return 0

    items = await _extract_qa(transcript)
    if not items:
        log.info("[KB] conversación %s: nada reutilizable.", conversation_id)
        return 0

    n = await _index_items(items, conversation_id)
    log.info("[KB] conversación %s: %d items de conocimiento indexados.", conversation_id, n)
    return n
