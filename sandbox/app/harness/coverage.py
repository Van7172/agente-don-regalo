"""Subagente Orientador (lógica determinista + tool distritos)."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

import httpx

from app.config import settings
from app.harness.aliases import normalize_place, resolve_alias
from app.harness.render import render_coverage
from app.harness.state import ConversationState
from app.tools import catalog

log = logging.getLogger(__name__)

_COVERAGE_RE = re.compile(
    r"distrito|zona|delivery|llegan?|cobertura|envio|env[ií]o|"
    r"maps|google\s*maps|donde\s+queda|palao|callao|independencia|"
    r"tarifa\s+de\s+envio|cuanto\s+cuesta\s+el\s+envio",
    re.I,
)


def looks_like_coverage(text: str) -> bool:
    return bool(_COVERAGE_RE.search(text or ""))


def _norm(s: str) -> str:
    s = (s or "").casefold()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# Conectores que no distinguen un distrito de otro: "Lima - Cercado" y
# "Cercado de Lima" son el mismo sitio.
_STOPWORDS = frozenset({"de", "del", "la", "el", "los", "las", "y"})


def _tokens(s: str) -> frozenset[str]:
    """Palabras significativas del nombre, sin tildes, puntuación ni conectores."""
    words = re.split(r"[^\w]+", _norm(s))
    return frozenset(w for w in words if w and w not in _STOPWORDS)


def _squash(s: str) -> str:
    """Todo pegado, para cuando el cliente se come los espacios (“sanisidro”)."""
    return re.sub(r"[^\w]+", "", _norm(s))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _district_name(d: dict[str, Any]) -> str:
    # `nombre` es la forma canónica; `nombre_distrito` es la cruda de la API, por
    # si algún call site se salta el adaptador.
    return _norm(
        str(
            d.get("nombre")
            or d.get("nombre_distrito")
            or d.get("distrito")
            or d.get("name")
            or ""
        )
    )


def match_district(query: str, districts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fuzzy match contra lista de distritos_cobertura.

    Compara por PALABRAS, no por substring: la API llama “LIMA - CERCADO” a lo que
    el cliente escribe “Cercado de Lima”, y ninguno de los dos contiene al otro.
    Con substring, cuanto más preciso era el cliente, peor iba: “cercado” a secas
    sí matcheaba, “Cercado de Lima” no, y una clienta acabó en Google Maps
    buscando el distrito en el que ya estaba.

    Va de más a menos preciso, para que “San Juan de Miraflores” no caiga en
    “Miraflores”: mismas palabras → subconjunto → substring.
    """
    if not _norm(query):
        return None

    # El alias traduce el lugar informal (“2da de Palao”) al distrito canónico,
    # pero NO le gana a un acierto exacto del texto crudo: “San Juan de
    # Miraflores” contiene el alias “Miraflores”, y dejarlo mandar le cobraba a
    # SJM la tarifa de Miraflores sin preguntar nada.
    alias = resolve_alias(query)
    both = [alias, query] if alias else [query]
    named = [(d, n) for d, n in ((d, _district_name(d)) for d in districts) if n]

    same = lambda qt, nt: qt == nt          # noqa: E731
    subset = lambda qt, nt: qt <= nt or nt <= qt  # noqa: E731

    passes: list[tuple[list[str], Any]] = [([query], same)]
    if alias:
        passes.append(([alias], same))
    passes.append((both, subset))

    for cands, match in passes:
        for cand in cands:
            q_tokens = _tokens(cand)
            if not q_tokens:
                continue
            for d, name in named:
                if match(q_tokens, _tokens(name)):
                    return d

    # Última red: palabras pegadas (“sanisidro”). Compara por IGUALDAD, no por
    # contención: “ate” vive dentro de “chocolates”, y la contención mandaba a
    # Ate a quien solo pedía chocolates.
    for cand in both:
        squashed = _squash(cand)
        words = {_squash(w) for w in _norm(cand).split()}
        for d, name in named:
            n = _squash(name)
            if n and (n == squashed or n in words):
                return d
    return None


def extract_place_candidates(text: str) -> list[str]:
    """Extrae posibles lugares del mensaje (líneas / frases cortas)."""
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\n.?!]+", raw)
    cands: list[str] = []
    for p in parts:
        p = p.strip()
        if len(p) < 3:
            continue
        # Quitar muletillas
        p2 = re.sub(
            r"^(creo que es|es en|es el|el distrito|distrito|donde es|a ver si me ayudas|porfa|por favor)\s*",
            "",
            p,
            flags=re.I,
        ).strip(" ?.!,")
        if p2:
            cands.append(p2)
    if not cands:
        cands.append(raw)
    return cands


async def resolve_coverage(
    user_text: str,
    state: ConversationState,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Resuelve cobertura en un solo paso.
    Retorna dict structured + user_facing + state_patch.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30.0)
    assert client is not None

    try:
        raw = await catalog.distritos_cobertura(client, {})
    finally:
        if own_client:
            await client.aclose()

    districts: list[dict] = []
    if isinstance(raw, dict):
        districts = list(raw.get("data") or raw.get("distritos") or [])
        if not districts and isinstance(raw.get("data"), dict):
            districts = list(raw["data"].values()) if raw["data"] else []
    elif isinstance(raw, list):
        districts = raw

    candidates = extract_place_candidates(user_text)
    # Pregunta general de zonas (sin lugar concreto).
    if re.search(
        r"qu[eé]\s+zonas|distritos?\s+(cubren|tienen)|lista\s+de\s+distritos|"
        r"cobertura\s+en\s+lima|llegan\s+a\s+lima",
        user_text or "",
        re.I,
    ) and not resolve_alias(user_text):
        samples = []
        for d in districts[:8]:
            n = d.get("nombre") or d.get("distrito") or d.get("name")
            if n:
                samples.append(str(n))
        sample_txt = ", ".join(samples[:6]) if samples else "Miraflores, San Isidro, Surco"
        text = (
            f"Hacemos delivery en Lima Metropolitana y parte de Callao 🚚 "
            f"Algunos distritos: {sample_txt}… "
            "¿A qué distrito exacto lo enviamos para confirmarte la tarifa?"
        )
        return {
            "ok": True,
            "user_facing": text,
            "structured": {
                "resolved_district": None,
                "covered": True,
                "ambiguity": "need_district",
                "suggest_maps": False,
                "ask": text,
            },
            "state_patch": {"intent_last": "coverage"},
        }

    matched: dict[str, Any] | None = None
    used_query = ""
    for cand in candidates:
        matched = match_district(cand, districts)
        if matched:
            used_query = cand
            break

    if not matched:
        # Si ya tenemos distrito en estado y el mensaje es solo "dónde queda X" ambiguo
        place = candidates[0] if candidates else user_text
        ask = render_coverage(suggest_maps=True, place_query=place[:80])
        return {
            "ok": True,
            "user_facing": ask,
            "structured": {
                "resolved_district": None,
                "covered": None,
                "ambiguity": "place_unknown",
                "suggest_maps": True,
                "ask": ask,
            },
            "state_patch": {},
        }

    # `distritos_cobertura` ya devuelve la forma canónica (`adapters.district`):
    # nombre, tarifa_usd y tarifa_sol. La API cruda usa `nombre_distrito` y
    # `tarifa_envio_distrito` en USD, y leerla directamente era el bug que hacía
    # que NINGÚN distrito hiciera match.
    name = str(matched.get("nombre") or used_query)
    fee_sol_f = _as_float(matched.get("tarifa_sol") or matched.get("precio_sol"))
    fee_usd_f = _as_float(matched.get("tarifa_usd") or matched.get("precio_usd"))

    ask = "¿Qué regalo quieres enviar? 🎁"
    if state.chosen_product_name or state.checkout_step not in ("idle", ""):
        ask = "¿Para qué fecha lo necesitas? 📅"

    text = render_coverage(
        district=name,
        covered=True,
        fee_sol=fee_sol_f,
        fee_usd=fee_usd_f,
        ask=ask,
    )
    patch = {
        "district": name,
        "shipping_fee_sol": fee_sol_f,
        "shipping_fee_usd": fee_usd_f,
        "intent_last": "coverage",
    }
    # `id_distrito` lo exige `POST /pedidos/temporales`; lo guardamos aquí para no
    # tener que resolver el nombre otra vez al cerrar.
    if matched.get("id_distrito") is not None:
        try:
            patch["id_distrito"] = int(matched["id_distrito"])
        except (TypeError, ValueError):
            pass
    if state.checkout_step in ("idle", "district"):
        patch["checkout_step"] = "date" if state.chosen_product_id else "idle"

    return {
        "ok": True,
        "user_facing": text,
        "structured": {
            "resolved_district": name,
            "covered": True,
            "fee_sol": fee_sol_f,
            "fee_usd": fee_usd_f,
            "ambiguity": None,
            "suggest_maps": False,
            "ask": ask,
        },
        "state_patch": patch,
    }
