"""Subagente Orientador (lógica determinista + tool distritos)."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

import httpx

from app.config import settings
from app.harness.aliases import PLACE_ALIASES, normalize_place, pick_zone, resolve_alias
from app.harness.render import render_coverage, render_zones
from app.harness.registry import assert_tool_allowed
from app.harness.state import ConversationState
from app.harness.delivery_request import URL_RE
from app.harness.maps_location import extract_maps_url, resolve_maps_district
from app.harness.replies import YES, NO, short_reply
from app.harness.delivery_location import (
    delivery_reference, delivery_point, point_question, maps_confirmation_question, reference_district,
)
from app.tools import catalog

log = logging.getLogger(__name__)

_COVERAGE_RE = re.compile(
    r"distrito|zona|delivery|llegan?|cobertura|envio|env[ií]o|"
    r"maps|google\s*maps|donde\s+queda|palao|callao|independencia|"
    r"tarifa\s+de\s+envio|cuanto\s+cuesta\s+el\s+envio|"
    # "hace entregas a domicilio?" / "no hay delivery?" no traen ninguna de
    # las palabras de arriba salvo "delivery" a veces; sin "entrega(s)" y
    # "domicilio" esa pregunta caía a small_talk o, peor, dependía del LLM
    # de respaldo para llegar aquí.
    r"entregas?|domicilio",
    re.I,
)

# Un distrito nombrado no siempre habla de entrega ("flores inspiradas en
# Miraflores"). Para que mande a cobertura exigimos que sea una respuesta corta
# —normalmente al "¿a qué distrito?"— o que venga unido a una intención logística.
_DELIVERY_DESTINATION_RE = re.compile(
    r"\b(?:pedido|delivery|env[ií]o|enviar|mandar|llevar|entregar|entrega|"
    r"destino|despacho)\b.{0,55}\b(?:a|al|para|hacia|en)\b|"
    r"\b(?:a|al|para|hacia)\b.{0,45}\b(?:delivery|env[ií]o|entrega|despacho)\b",
    re.I,
)


def _is_bare_place(text: str) -> bool:
    """Un lugar solo, admitiendo la forma de respuesta «es en Miraflores»."""
    norm = normalize_place(text)
    norm = re.sub(
        r"^(?:es\s+en|es\s+el|el\s+distrito\s+es|distrito|en)\s+",
        "",
        norm,
    ).strip()
    return norm in PLACE_ALIASES


def looks_like_coverage(text: str) -> bool:
    raw = (text or "").strip()
    if _COVERAGE_RE.search(raw):
        return True
    place = resolve_alias(raw)
    if not place:
        return False
    # "Cercado de Lima" / "es en Miraflores": el lugar viene solo. No basta con
    # contar palabras: "flores inspiradas en Miraflores" también es corto y es
    # catálogo, no cobertura.
    return _is_bare_place(raw) or bool(_DELIVERY_DESTINATION_RE.search(raw))


def explicit_delivery_destination(text: str) -> str | None:
    """Distrito expresado como destino del pedido, o respuesta corta de lugar.

    Devuelve el nombre canónico para que el router pueda decidir sin consultar
    aún la API. La cobertura y la tarifa reales se validan después mediante
    `distritos_cobertura`.
    """
    raw = (text or "").strip()
    place = resolve_alias(raw)
    if not place:
        return None
    if _is_bare_place(raw) or _DELIVERY_DESTINATION_RE.search(raw):
        return place
    return None


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
    raw = URL_RE.sub("", text or "").strip()
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


# Verbos y conectores de frase: si aparecen, eso es una oración, no un lugar.
_NOT_A_PLACE_RE = re.compile(
    r"\b(quisiera|quiero|puedo|podr[aá]|podr[ií]a|puede|hacer|entrega|entregas|ser[ií]a|est[aá]|estoy|tengo|vengan|"
    r"venga|pagar|pago|comprar|enviar|mandar|porque|por\s+que|cu[aá]nto|"
    r"c[oó]mo|qu[eé]|gracias|"
    # "No hay delivery?" no tiene ninguno de los verbos de arriba: se leía
    # como un posible distrito y el bot respondía "No ubico 'No hay
    # delivery'... ¿lo buscas en Google Maps?" a una pregunta, no a un lugar.
    r"no\s+hay|\bhacen\b|\btienen\b|"
    # Respuesta de FECHA al "¿para qué día lo necesitas?" del cierre, no de
    # lugar: "Para mañana por favor" se citaba de vuelta como si "mañana"
    # fuera un distrito que no existe, en mitad de una compra en curso.
    r"ma[ñn]ana|\bhoy\b|por\s+favor|"
    r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
    re.I,
)


def _looks_like_place(candidate: str) -> bool:
    """¿Esto se puede citar de vuelta como un sitio? Ante la duda, no.

    Un nombre de distrito o de referencia es corto y no tiene verbos: "Villa
    María del Triunfo", "2da de Palao". Una frase del cliente no se cita: si el
    turno vino mal enrutado, citarla le enseña nuestro fallo, no lo resuelve.
    """
    clean = " ".join((candidate or "").split())
    if not (2 < len(clean) <= 40):
        return False
    if len(clean.split()) > 5:
        return False
    return not _NOT_A_PLACE_RE.search(clean)


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
    # Contesta a la zona que se le acaba de preguntar: ya tenemos sus datos.
    maps_url = extract_maps_url(user_text)
    if maps_url:
        previous_district = state.district or state.maps_previous_district
        # A new destination invalidates the previous district and shipping fee.
        # Nullable fields cannot be cleared through state.patch(None).
        state.district = ""
        state.id_distrito = None
        state.shipping_fee_sol = None
        state.shipping_fee_usd = None
        state.district_options = []
        state.direccion = ""
        candidate = await resolve_maps_district(maps_url)
        context_matches = bool(previous_district and (not candidate or _norm(candidate) == _norm(previous_district)))
        if context_matches:
            candidate = previous_district
            kind = "destination"
        else:
            kind = "district"
        pending_state = ConversationState.from_dict(state.to_dict())
        pending_state.maps_confirmation_kind = kind
        pending_state.maps_district_candidate = candidate
        ask = (
            maps_confirmation_question(pending_state) + " Con tu confirmación reviso la cobertura y la tarifa."
            if candidate else
            "Recibí el enlace de Maps, pero no pude identificar el distrito con certeza. "
            "¿En qué distrito queda la dirección? Con eso reviso la cobertura y la tarifa."
        )
        if candidate and previous_district and not context_matches:
            ask = f"El enlace de Maps indica {candidate}, pero antes me indicaste {previous_district}. ¿Confirmas que la nueva entrega sería en {candidate}?"
        return {"ok": True, "user_facing": ask, "tools_used": ["google_maps_location"],
                "structured": {"covered": None, "ambiguity": "maps_confirmation", "resolved_district": None},
                "state_patch": {"maps_url": maps_url, "maps_district_candidate": candidate,
                                "maps_location_pending": True, "maps_location_confirmed": False,
                                "maps_confirmation_kind": kind, "maps_previous_district": previous_district}}
    pending_maps = state.maps_district_candidate
    if pending_maps:
        if short_reply(user_text) == YES:
            user_text = pending_maps
        elif short_reply(user_text) == NO:
            state.maps_district_candidate = ""
            ask = (
                "Gracias por aclararlo. ¿En qué distrito queda el nuevo punto de entrega?"
                if state.maps_confirmation_kind == "destination" else
                "Gracias por aclararlo. ¿En qué distrito queda la dirección?"
            )
            return {"ok": True, "user_facing": ask,
                    "structured": {"covered": None}, "tools_used": [],
                    "state_patch": {"maps_district_candidate": "", "maps_confirmation_kind": "district",
                                    "maps_url": "", "maps_location_confirmed": False,
                                    "delivery_point": "", "delivery_point_pending": bool(state.delivery_reference)}}
        if resolve_alias(user_text):
            state.maps_district_candidate = ""
    reference = delivery_reference(user_text)
    if reference and not state.maps_location_pending:
        # A reference complements a known district. It is never looked up as
        # the district name, nor assigned a district from general model knowledge.
        district = reference_district(user_text) or state.district
        same_district = bool(district and _norm(district) == _norm(state.district))
        if same_district or not district:
            provisional = ConversationState.from_dict(state.to_dict())
            provisional.delivery_reference = reference
            ask = (
                f"Tomo {reference} como referencia para la entrega en {district}. {point_question(provisional)}"
                if district else f"Tomo {reference} como referencia. ¿En qué distrito queda para revisar la cobertura?"
            )
            return {"ok": True, "user_facing": ask, "tools_used": [],
                    "structured": {"covered": None, "ambiguity": "delivery_reference"},
                    "state_patch": {"delivery_reference": reference, "delivery_point": "", "delivery_point_pending": True,
                                    "maps_url": "", "maps_location_confirmed": False, "direccion": ""}}
    point = delivery_point(user_text) if state.delivery_point_pending else ""
    if point and not state.maps_location_pending:
        ask = (
            "¿En qué distrito queda para revisar la cobertura?" if not state.district else
            "¿Es una casa o una oficina?" if state.checkout_step == "address" else
            "¿Para qué fecha lo necesitas? 📅" if state.chosen_product_id and not state.date else
            "¿En qué rango horario lo necesitas? 🕐" if state.chosen_product_id and not state.time_slot else
            "¿Qué regalo quieres enviar? 🎁"
        )
        return {"ok": True, "user_facing": f"Anotado el punto de entrega: {point}. {ask}", "tools_used": [],
                "structured": {"covered": None, "ambiguity": "delivery_point"},
                "state_patch": {"delivery_point": point, "delivery_point_pending": False}}
    if state.district_options:
        zona = pick_zone(user_text, state.district_options)
        if zona is not None:
            return _covered(zona, user_text, state, fee_known=True, with_note=False)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=12.0)
    assert client is not None

    try:
        assert_tool_allowed("coverage", "distritos_cobertura")
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
    # Pregunta general de zonas o de "¿existe el servicio?" (sin lugar
    # concreto). "No hay delivery?" / "¿Hacen entregas a domicilio?" son la
    # misma pregunta que "qué zonas cubren" con otras palabras — antes solo
    # la segunda forma se reconocía, así que la primera caía al matching de
    # distrito, no encontraba nada y se le hacía eco al cliente como si "no
    # hay delivery" fuera el nombre de un sitio.
    if re.search(
        r"qu[eé]\s+zonas|distritos?\s+(cubren|tienen)|lista\s+de\s+distritos|"
        r"cobertura\s+en\s+lima|llegan\s+a\s+lima|"
        r"(?:hay|hacen|tienen|dan)\s+(?:deliver(?:y)?\b|entregas?|env[ií]os?)|"
        r"(?:delivery|entregas?|env[ií]os?)\s+a\s+domicilio|"
        r"entregan\s+a\s+domicilio",
        user_text or "",
        re.I,
    ) and not resolve_alias(user_text):
        # La API real trae distritos repetidos en los primeros puestos (p.ej.
        # "Ate" dos veces): sin dedupe, el ejemplo salía "Ate, Ate,
        # Barranco…" — se ve a medio armar y desperdicia un cupo de muestra.
        samples: list[str] = []
        seen_samples: set[str] = set()
        for d in districts[:20]:
            n = d.get("nombre") or d.get("distrito") or d.get("name")
            if not n:
                continue
            n = str(n)
            key = n.casefold()
            if key in seen_samples:
                continue
            seen_samples.add(key)
            samples.append(n)
            if len(samples) == 6:
                break
        sample_txt = ", ".join(samples) if samples else "Miraflores, San Isidro, Surco"
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
        # Se cita de vuelta SOLO lo que puede ser un lugar. Antes salía el primer
        # trozo del mensaje pasara lo que pasara, y un turno mal enrutado se le
        # devolvía al cliente tal cual ("No ubico “En este caso, como voy a pagar
        # la canasta…”"). Si nada parece un distrito, se pregunta sin citar.
        place = next((c for c in candidates if _looks_like_place(c)), "")
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

    if reference:
        state.delivery_reference = reference
        state.delivery_point_pending = True

    # La API repite distritos con tarifa distinta (ATE a $7 y a $15). Quedarse con el
    # primero es adivinar lo que paga el cliente. Si cada zona está descrita, se le
    # pregunta; si no hay con qué distinguirlas, no se da ninguna cifra.
    rivales = _same_name_other_fee(matched, districts)
    if rivales:
        zonas = [matched, *rivales]
        if all(str(zona.get("informacion") or "").strip() for zona in zonas):
            return _ask_zone(str(matched.get("nombre") or used_query), zonas)
    return _covered(matched, used_query, state, fee_known=not rivales, with_note=True)


def _same_name_other_fee(
    matched: dict[str, Any], districts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    nombre = _district_name(matched)
    tarifa = _as_float(matched.get("tarifa_usd") or matched.get("precio_usd"))
    return [
        d
        for d in districts
        if d is not matched
        and _district_name(d) == nombre
        and _as_float(d.get("tarifa_usd") or d.get("precio_usd")) != tarifa
    ]


def _ask_zone(name: str, zonas: list[dict[str, Any]]) -> dict[str, Any]:
    text = render_zones(name, zonas)
    return {
        "ok": True,
        "user_facing": text,
        "structured": {
            "resolved_district": None,
            "covered": True,
            "ambiguity": "zone",
            "suggest_maps": False,
            "ask": text,
            "zone_fees": [
                fee for fee in (_as_float(z.get("tarifa_sol")) for z in zonas) if fee is not None
            ],
        },
        "state_patch": {"district_options": zonas},
    }


def _covered(
    matched: dict[str, Any],
    used_query: str,
    state: ConversationState,
    *,
    fee_known: bool,
    with_note: bool,
) -> dict[str, Any]:
    """Distrito confirmado: su tarifa, si es segura, y lo que la API avisa de él."""
    # `distritos_cobertura` ya devuelve la forma canónica (`adapters.district`):
    # nombre, tarifa_usd y tarifa_sol. La API cruda usa `nombre_distrito` y
    # `tarifa_envio_distrito` en USD, y leerla directamente era el bug que hacía
    # que NINGÚN distrito hiciera match.
    name = str(matched.get("nombre") or used_query)
    previous_district = state.maps_previous_district if state.maps_location_pending else state.district
    district_changed = bool(previous_district and _norm(previous_district) != _norm(name))
    fee_sol_f = _as_float(matched.get("tarifa_sol") or matched.get("precio_sol")) if fee_known else None
    fee_usd_f = _as_float(matched.get("tarifa_usd") or matched.get("precio_usd")) if fee_known else None

    ask = "¿Qué regalo quieres enviar? 🎁"
    if state.chosen_product_name or state.checkout_step not in ("idle", ""):
        ask = "¿En qué rango horario lo necesitas? 🕐" if state.date else "¿Para qué fecha lo necesitas? 📅"
    if state.checkout_step == "address":
        ask = "¿Es una casa o una oficina?"
    if state.delivery_reference and (not district_changed or delivery_reference(used_query)) and (
        (state.delivery_point_pending and not state.maps_location_pending)
        or (state.maps_location_pending and not state.maps_url)
    ):
        ask = point_question(state)

    notas: list[str] = []
    if not fee_known:
        notas.append("La tarifa exacta depende de la zona: te la confirmamos al coordinar el pago.")
    informacion = str(matched.get("informacion") or "").strip()
    if with_note and informacion:
        # "No cubre Santa Rosa": mejor saberlo antes de llenar todo el formulario. La
        # API no siempre le pone punto, y detrás va la pregunta.
        cierre = "" if informacion[-1] in ".!?" else "."
        notas.append(f"Ten en cuenta: {informacion}{cierre}")

    text = render_coverage(
        district=name,
        covered=True,
        fee_sol=fee_sol_f,
        fee_usd=fee_usd_f,
        ask=ask,
        note=" ".join(notas),
    )
    patch = {
        "district": name,
        "shipping_fee_sol": fee_sol_f,
        "shipping_fee_usd": fee_usd_f,
        "intent_last": "coverage",
        "maps_location_pending": False,
        "maps_district_candidate": "",
        "maps_confirmation_kind": "",
        "maps_previous_district": "",
    }
    if state.maps_location_pending:
        patch["maps_location_confirmed"] = bool(state.maps_url)
        patch["delivery_point_pending"] = bool(state.delivery_reference and not state.maps_url)
        patch["delivery_point"] = ""  # confirmed pin is the handoff point
        if district_changed:
            patch["delivery_reference"] = ""
            patch["delivery_point_pending"] = False
    elif district_changed:
        patch.update(maps_url="", maps_location_confirmed=False, delivery_point="", direccion="")
        if not delivery_reference(used_query):
            patch.update(delivery_reference="", delivery_point_pending=False)
    if state.district_options:
        patch["district_options"] = []
    # `id_distrito` lo exige `POST /pedidos/temporales`; lo guardamos aquí para no
    # tener que resolver el nombre otra vez al cerrar.
    if matched.get("id_distrito") is not None:
        try:
            patch["id_distrito"] = int(matched["id_distrito"])
        except (TypeError, ValueError):
            pass
    if state.checkout_step in ("idle", "district"):
        patch["checkout_step"] = ("schedule" if state.date else "date") if state.chosen_product_id else "idle"

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
