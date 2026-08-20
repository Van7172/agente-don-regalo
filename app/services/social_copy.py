"""Copys de redes sociales a partir de un producto real del catálogo.

Herramienta interna del CRM: **esto no habla con clientes**. Por eso no compone
el system message por capas como los especialistas (`prompts/compose.py`) — no
hay conversación, no hay estado y no hay nadie al otro lado a quien se le pueda
filtrar nada. Lo que sí se hereda es la regla que más caro salió:

    **el precio NO lo escribe el modelo.**

Un post con un precio inventado no es un error de estilo: es publicidad
engañosa que el equipo pega en Instagram y que luego alguien reclama. El importe
sale del producto canónico (`tools/adapters.py`, que ya convirtió de dólares a
soles) y lo estampa el código; al modelo se le prohíbe escribir cifras y, por si
las escribe igual, `_sin_precios_inventados` las borra antes de devolver nada.
Es la misma idea que la invariante `prices_are_sourced` del bot, aplicada aquí.

El modelo solo pone las palabras.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from app.observability import audit_event, record_llm_usage, record_operation
from app.resilience import circuit_breaker

log = logging.getLogger(__name__)

# Cuántas redacciones distintas se le piden de una sola llamada. Tres es lo que
# cabe en pantalla sin scroll y lo que alguien compara de verdad; con seis se
# elige la primera igual, pero se paga por las seis.
MAX_VARIANTES = 3

# El límite es del formato, no nuestro: un pie de Instagram se corta sobre los
# 125 caracteres y el titular de una pieza 9:16 no entra en dos líneas si pasa
# de ~60. Si el modelo se pasa, se recorta en el borde.
MAX_TITULAR = 70
MAX_CUERPO = 320
MAX_HASHTAGS = 6

TONOS = {
    "casual": "cercano y directo, como le hablarías a un amigo por WhatsApp",
    "elegante": "sobrio y cuidado, sin exclamaciones ni emojis chillones",
    "divertido": "con humor ligero y juguetón, sin caer en payasada",
    "romantico": "cálido y emotivo, centrado en el gesto y en quien lo recibe",
    "urgente": "con sensación de oportunidad y de que conviene decidirse hoy",
}
TONO_POR_DEFECTO = "casual"

# Proporciones que el equipo publica. 9:16 manda porque es historia de Instagram
# y TikTok, que es donde entra la mayoría de los leads.
FORMATOS = {
    "9:16": "historia vertical de Instagram/TikTok (pantalla completa)",
    "4:5": "publicación vertical de feed de Instagram",
    "1:1": "publicación cuadrada de feed",
}
FORMATO_POR_DEFECTO = "9:16"

_PRECIO_RE = re.compile(
    r"(?:S/\.?|US\$|\$|soles?\b)\s*\d[\d.,]*|\b\d[\d.,]*\s*soles\b",
    re.IGNORECASE,
)

# Un descuento inventado es tan reclamable como un precio inventado. Se exige el
# contexto de rebaja a propósito: "100% peruano" es una frase legítima de
# marketing y un filtro que se lleve por delante cualquier porcentaje acabaría
# mutilando copys buenos.
_DESCUENTO_RE = re.compile(
    r"\d[\d.,]*\s*%\s*(?:de\s+)?(?:dscto|descuento|off|rebaja)"
    r"|(?:dscto|descuento|rebaja|off)\s*(?:de\s+)?\d[\d.,]*\s*%",
    re.IGNORECASE,
)


def _recorta(texto: str, limite: int) -> str:
    limpio = " ".join(str(texto or "").split())
    if len(limpio) <= limite:
        return limpio
    corte = limpio[:limite].rstrip()
    # Cortar a mitad de palabra se lee como un fallo del sistema; en el último
    # espacio se lee como una frase corta.
    if " " in corte:
        corte = corte[: corte.rfind(" ")]
    return corte.rstrip(" ,;:.") + "…"


def menciona_dinero(texto: str) -> bool:
    """¿El modelo escribió un importe o un descuento por su cuenta?

    La variante entera se descarta, no se parchea. Borrar la cifra deja frases
    mutiladas ("Solo hoy a con de descuento") y esto no es un chat donde una
    frase rara se disculpa en el siguiente mensaje: es una pieza que alguien
    pega en Instagram tal cual. Es la misma decisión que toma `master` con una
    respuesta insegura — se tira la prosa del modelo y se conserva lo que sí
    viene del catálogo.

    Tampoco se intenta "corregir" la cifra al precio real: un `$29` puede ser un
    descuento o un envío que no existen, y sustituirlo por el precio del
    producto convertiría un invento en otro.
    """
    return bool(_PRECIO_RE.search(texto or "") or _DESCUENTO_RE.search(texto or ""))


def precio_visible(producto: dict) -> str:
    """La línea de precio, tal cual sale del catálogo. Vacía si no hay dato."""
    precio = producto.get("precio_sol")
    if precio is None:
        return ""
    linea = f"S/ {float(precio):.2f}"
    lista = producto.get("precio_lista_sol")
    if producto.get("tiene_oferta") and lista and float(lista) > float(precio):
        linea += f" (antes S/ {float(lista):.2f})"
    return linea


def _prompt_sistema(tono: str, formato: str, incluir_cta: bool) -> str:
    estilo = TONOS.get(tono, TONOS[TONO_POR_DEFECTO])
    pieza = FORMATOS.get(formato, FORMATOS[FORMATO_POR_DEFECTO])
    reglas = [
        "Escribes en español de Perú, para Don Regalo (donregalo.pe), delivery "
        "de regalos en Lima.",
        f"La pieza es una {pieza}.",
        f"El tono es {estilo}.",
        "NUNCA escribas precios, cifras de dinero, descuentos ni porcentajes: "
        "el precio lo pone el sistema aparte, y si lo escribes tú se borra.",
        "No inventes características, contenidos ni promesas de entrega que no "
        "estén en los datos del producto.",
        "No prometas stock, plazos ni horarios concretos.",
    ]
    if incluir_cta:
        reglas.append(
            "Cada variante lleva un CTA corto que invite a escribir por WhatsApp."
        )
    else:
        reglas.append("Deja `cta` vacío: esta pieza no lleva llamada a la acción.")

    return (
        "Eres redactor publicitario del equipo de marketing de Don Regalo.\n"
        + "\n".join(f"- {r}" for r in reglas)
        + "\n\nDevuelve SOLO un JSON con esta forma exacta:\n"
        '{"variantes": [{"titular": "...", "cuerpo": "...", "cta": "...", '
        '"hashtags": ["#uno", "#dos"]}]}'
    )


def _prompt_usuario(
    producto: dict,
    *,
    instrucciones: str,
    variantes: int,
    incluir_precio: bool,
) -> str:
    lineas = [
        f"Producto: {producto.get('nombre') or 'sin nombre'}",
    ]
    if producto.get("categoria"):
        lineas.append(f"Categoría: {producto['categoria']}")
    if producto.get("descripcion_corta"):
        lineas.append(f"Descripción: {producto['descripcion_corta']}")
    if producto.get("descripcion"):
        # El "¿qué contiene?" del detalle: es lo único que da material concreto
        # para escribir sin inventar.
        lineas.append(f"Contenido:\n{producto['descripcion']}")
    if incluir_precio:
        # Se le dice que HAY precio y que lo pone el sistema, para que no deje
        # un hueco tipo "por solo ___" esperando rellenarlo.
        lineas.append(
            "El sistema añadirá el precio al final de la pieza. No lo escribas."
        )
    if instrucciones.strip():
        lineas.append(f"Indicaciones del asesor: {instrucciones.strip()}")
    lineas.append(
        f"Escribe {variantes} variante(s) claramente distintas entre sí, no "
        "reformulaciones de la misma frase."
    )
    return "\n".join(lineas)


def _variante(
    cruda: dict, producto: dict, *, incluir_precio: bool, incluir_cta: bool
) -> dict | None:
    """Una variante lista para pegar, o `None` si hay que descartarla."""
    titular = _recorta(cruda.get("titular"), MAX_TITULAR)
    cuerpo = _recorta(cruda.get("cuerpo"), MAX_CUERPO)
    cta = _recorta(cruda.get("cta"), 90) if incluir_cta else ""

    if any(menciona_dinero(t) for t in (titular, cuerpo, cta)):
        record_operation("openai.social", "precio_inventado")
        log.warning("[contenido] variante descartada: el modelo escribió cifras")
        return None

    hashtags: list[str] = []
    for tag in cruda.get("hashtags") or []:
        limpio = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÑáéíóúñ_]", "", str(tag))
        if limpio:
            hashtags.append("#" + limpio)
        if len(hashtags) >= MAX_HASHTAGS:
            break

    precio = precio_visible(producto) if incluir_precio else ""

    # El bloque listo para pegar. Se arma aquí y no en el navegador porque es lo
    # que el asesor copia: si el CRM lo recompusiera por su cuenta, habría dos
    # versiones del mismo texto y la que se publica sería la que nadie revisó.
    bloque = [p for p in (titular, cuerpo, precio, cta) if p]
    if hashtags:
        bloque.append(" ".join(hashtags))

    return {
        "titular": titular,
        "cuerpo": cuerpo,
        "precio": precio,
        "cta": cta,
        "hashtags": hashtags,
        "texto": "\n\n".join(bloque),
    }


async def generar_copys(
    *,
    producto: dict,
    tono: str = TONO_POR_DEFECTO,
    formato: str = FORMATO_POR_DEFECTO,
    instrucciones: str = "",
    incluir_precio: bool = True,
    incluir_cta: bool = True,
    variantes: int = MAX_VARIANTES,
) -> list[dict]:
    """Devuelve entre 1 y `MAX_VARIANTES` copys. Lanza si el LLM no responde.

    Que lance es a propósito: el CRM tiene que poder decirle al asesor "no se
    pudo generar, vuelve a intentarlo". Un texto de relleno silencioso acabaría
    publicado.
    """
    from app.config import settings

    if not settings.openai_api_key:
        raise RuntimeError("No hay OPENAI_API_KEY configurada en el agente.")

    variantes = max(1, min(int(variantes or 1), MAX_VARIANTES))
    tono = tono if tono in TONOS else TONO_POR_DEFECTO
    formato = formato if formato in FORMATOS else FORMATO_POR_DEFECTO

    payload = {
        "model": settings.router_model,
        "messages": [
            {"role": "system", "content": _prompt_sistema(tono, formato, incluir_cta)},
            {
                "role": "user",
                "content": _prompt_usuario(
                    producto,
                    instrucciones=instrucciones,
                    variantes=variantes,
                    incluir_precio=incluir_precio,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
        # Un poco de temperatura: tres variantes a 0 salen casi idénticas y la
        # pantalla de "elige una" deja de tener sentido.
        "temperature": 0.8,
    }

    started = time.monotonic()
    try:

        async def _request() -> dict:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    json=payload,
                )
                r.raise_for_status()
                return r.json()

        # Circuito propio: que el estudio de contenido se caiga no puede abrir el
        # breaker que atiende a los clientes, ni al revés.
        raw = await circuit_breaker("openai.social").call(_request)
        record_llm_usage("social", str(payload["model"]), raw)
        data = json.loads(raw["choices"][0]["message"]["content"])
    except Exception as err:
        latencia = (time.monotonic() - started) * 1000
        record_operation("openai.social", "error", duration_ms=latencia)
        audit_event(
            "openai.request",
            "error",
            backend="openai",
            operation="social",
            latency_ms=latencia,
            error_type=type(err).__name__,
        )
        log.warning("[contenido] no se pudo generar el copy: %s", err)
        raise

    crudas = data.get("variantes")
    if not isinstance(crudas, list):
        crudas = [data] if isinstance(data, dict) and data.get("titular") else []

    salida = [
        v
        for v in (
            _variante(
                c if isinstance(c, dict) else {},
                producto,
                incluir_precio=incluir_precio,
                incluir_cta=incluir_cta,
            )
            for c in crudas[:variantes]
        )
        if v and (v["titular"] or v["cuerpo"])
    ]
    if not salida:
        # Sin variantes limpias se falla en voz alta. El CRM le dice al asesor
        # que reintente: un texto de relleno silencioso acabaría publicado.
        record_operation("openai.social", "invalid")
        raise RuntimeError("El modelo no devolvió ninguna variante utilizable.")

    record_operation("openai.social", "ok", duration_ms=(time.monotonic() - started) * 1000)
    return salida
