"""La cita de WhatsApp es CONTEXTO DEL SISTEMA, no palabras del cliente.

Cuando el cliente responde a un mensaje, `services.buffer` antepone un marcador
al turno (`[El cliente está respondiendo al mensaje: «…»]`) para que el modelo
sepa a qué se refiere ese "quiero este". El marcador viaja dentro del MISMO
string que escribió el cliente, y ahí estaba el problema: todo lo determinista
—router, cobertura, FSM del cierre— lo leía como si lo hubiera escrito él.

Una clienta respondió a la cotización de un asesor («… + delivery 15.00 = 150»)
preguntando si los productos venían dentro de la canasta. La palabra "delivery"
de la CITA enrutó el turno a cobertura; cobertura no encontró ningún distrito en
la frase y le devolvió el marcador entero: *No ubico “[El cliente está
respondiendo al mensaje: «Brunch de Feliz Cumpleaños modificado»” en nuestra
lista*. Le pintamos nuestra tubería al cliente, en el turno en el que estaba
cerrando la compra.

Así que el marcador se compone aquí y se parte aquí: `turn.text` son las
palabras del cliente y `turn.quoted` es la cita. El modelo sigue viendo las dos
(van en `turn.messages`, intactas); lo determinista ya no las confunde.

`internal_leak` es la otra mitad: si un marcador interno —el de la cita, el de
contenido saneado, una etiqueta de PII, el stub `[image]`— se cuela igualmente
en un texto de cara al cliente, eso NO se envía. Un fallo nuestro se deriva a un
humano; no se le enseña al cliente.
"""
from __future__ import annotations

import re

_QUOTE_TEMPLATE = "[El cliente está respondiendo al mensaje: «{quoted}»]"

# `.*?` con DOTALL: la cita de un producto trae saltos de línea (URL + viñeta).
QUOTE_MARKER_RE = re.compile(
    r"\[El cliente est[áa] respondiendo al mensaje:\s*«(?P<quoted>.*?)»\]",
    re.S,
)

# Marcadores que el sistema mete en el texto y que el cliente NUNCA debe leer.
# Cada uno tiene dueño: la cita (`buffer`), el saneado de inyecciones
# (`guardrails.input`), las etiquetas de PII (`guardrails.privacy`) y los stubs
# de media que el CRM guarda como contenido (`[image]` de una foto sin caption).
_INTERNAL_RES: tuple[re.Pattern[str], ...] = (
    # Solo la APERTURA: en el incidente el marcador salió cortado a 80 caracteres
    # («…«Brunch de Feliz Cumpleaños modificado»” en nuestra lista»), sin el `»]`
    # del final. Un detector que exigiera el cierre no habría visto nada.
    re.compile(r"\[El cliente est[áa] respondiendo", re.I),
    re.compile(r"\[contenido omitido por seguridad\]", re.I),
    re.compile(
        r"\[(?:direcci[óo]n|correo|documento|tel[ée]fono|dato de pago) protegid[oa]\]",
        re.I,
    ),
    re.compile(r"\[(?:image|audio|video|document|sticker|location|unknown)\]", re.I),
)


def build_quote_marker(quoted_text: str) -> str:
    """El marcador tal cual lo antepone el buffer. Fuente única del formato."""
    return _QUOTE_TEMPLATE.format(quoted=quoted_text)


def split_quote(text: str) -> tuple[str, str]:
    """Parte el turno en (lo que escribió el cliente, lo que citó).

    Quita TODOS los marcadores, no solo el primero: el buffer junta varios
    mensajes seguidos y el cliente puede responder a dos cosas en la misma
    ráfaga.
    """
    raw = text or ""
    quotes = [m.group("quoted").strip() for m in QUOTE_MARKER_RE.finditer(raw)]
    if not quotes:
        return raw.strip(), ""
    # El marcador deja su línea vacía detrás; se quitan solo si hubo marcador,
    # para no reformatear de gratis lo que escribe el cliente.
    sin_marcador = QUOTE_MARKER_RE.sub(" ", raw)
    clean = "\n".join(
        line.strip() for line in sin_marcador.splitlines() if line.strip()
    ).strip()
    return clean, "\n".join(q for q in quotes if q)


def internal_leak(text: str) -> str | None:
    """El primer marcador interno que se coló en un texto de cara al cliente."""
    for pattern in _INTERNAL_RES:
        match = pattern.search(text or "")
        if match:
            return match.group(0)
    return None
