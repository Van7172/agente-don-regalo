"""Respuestas cortas: sí, no, contradictorio, o no es una respuesta corta.

Había una lista de palabras por cada paso del cierre, cada una un poco distinta. Aquí
vive la única lectura. La regla: TODAS las palabras del mensaje tienen que ser de este
vocabulario. Una palabra de más significa que el cliente está diciendo otra cosa, y ante
esa duda no se cierra una venta ni se decide por él. Lo contradictorio se repregunta.
"""
from __future__ import annotations

import re
import unicodedata

YES = "yes"
NO = "no"
MIXED = "mixed"
OTHER = "other"

_AFFIRM = frozenset(
    """
    si sii siii sip ok oka okay okey okis oki dale correcto correcta perfecto perfecta
    confirmo confirmado confirmada confirmar confirmamos listo lista conforme claro yes
    vale va bueno exacto adelante procede procedamos seguimos bien acuerdo ya obvio
    supuesto porfa asi
    """.split()
)
_NEGATE = frozenset("no nop nope nel negativo nunca tampoco ninguna ninguno nada".split())
_FILLER = frozenset(
    "todo esta es de gracias muchas mil por favor que mejor todavia aun ahora momento".split()
)
_EMOJI_YES = "👍👌✅🙌"
_EMOJI_NO = "👎❌"
_EMOJI_ONLY_RE = re.compile(rf"^[\s{_EMOJI_YES}{_EMOJI_NO}\U0001F3FB-\U0001F3FF️]+$")

# Frases cuyo sentido no se saca sumando sus palabras: "no hay problema" es un sí y
# "claro que no" un no. Se resuelven antes de mirar palabra por palabra.
_PHRASES = (
    (re.compile(r"\b(?:no hay problema|sin problema|de una)\b"), "si"),
    (re.compile(r"\b(?:claro|por supuesto|obvio|ya) que no\b|\bya no\b"), "no"),
)


def _words(text: str) -> str:
    nfd = unicodedata.normalize("NFD", (text or "").casefold())
    plain = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", plain).split())


def short_reply(
    text: str,
    *,
    weak_yes: frozenset[str] = frozenset(),
    filler: frozenset[str] = frozenset(),
) -> str:
    """Cómo se lee el mensaje entero: `YES`, `NO`, `MIXED` u `OTHER`.

    `weak_yes` son palabras que cuentan como sí en un paso concreto ("quiero" en la
    tarjeta), pero no llevan la contraria a un no: "no quiero tarjeta" es un no.
    """
    raw = (text or "").strip()
    if raw and _EMOJI_ONLY_RE.match(raw):
        tiene_si = any(c in _EMOJI_YES for c in raw)
        tiene_no = any(c in _EMOJI_NO for c in raw)
        return MIXED if tiene_si and tiene_no else (YES if tiene_si else NO)

    plain = _words(raw)
    for pattern, replacement in _PHRASES:
        plain = pattern.sub(replacement, plain)
    tokens = plain.split()
    if not tokens or len(tokens) > 8:
        return OTHER

    allowed = _AFFIRM | _NEGATE | _FILLER | weak_yes | filler
    if any(t not in allowed for t in tokens):
        return OTHER
    # "asi" solo afirma si no lo acompaña un no: "así no es".
    has_no = any(t in _NEGATE for t in tokens)
    has_yes = any(t in _AFFIRM and t != "asi" for t in tokens)
    if has_no and has_yes:
        return MIXED
    if has_no:
        return NO
    if has_yes or "asi" in tokens or any(t in weak_yes for t in tokens):
        return YES
    return OTHER
