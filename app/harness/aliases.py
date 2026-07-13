"""Aliases de lugares informales → distrito canónico (Lima / Callao)."""
from __future__ import annotations

import re
import unicodedata

# Clave normalizada → nombre de distrito como en la API Don Regalo.
PLACE_ALIASES: dict[str, str] = {
    # Callao / Palao
    "2da de palao": "Callao",
    "segunda de palao": "Callao",
    "2 de palao": "Callao",
    "palao": "Callao",
    "callao": "Callao",
    "la perla": "La Perla",
    "bellavista callao": "Bellavista",
    # Independencia / zonas conocidas
    "independencia": "Independencia",
    "tahuantinsuyo": "Independencia",
    # Miraflores / sur
    "malecon": "Miraflores",
    "larcomar": "Miraflores",
    "miraflores": "Miraflores",
    "san isidro": "San Isidro",
    "barranco": "Barranco",
    "chorrillos": "Chorrillos",
    "surco": "Santiago de Surco",
    "santiago de surco": "Santiago de Surco",
    "la molina": "La Molina",
    "san borja": "San Borja",
    "surquillo": "Surquillo",
    "magdalena": "Magdalena del Mar",
    "magdalena del mar": "Magdalena del Mar",
    "pueblo libre": "Pueblo Libre",
    "jesus maria": "Jesús María",
    "lince": "Lince",
    "breña": "Breña",
    "rimac": "Rímac",
    "san miguel": "San Miguel",
    "los olivos": "Los Olivos",
    "comas": "Comas",
    "smp": "San Martín de Porres",
    "san martin de porres": "San Martín de Porres",
    "carabayllo": "Carabayllo",
    "ate": "Ate",
    "santa anita": "Santa Anita",
    "el Agustino": "El Agustino",
    "el agustino": "El Agustino",
    "sjl": "San Juan de Lurigancho",
    "san juan de lurigancho": "San Juan de Lurigancho",
    "sjm": "San Juan de Miraflores",
    "villa el salvador": "Villa El Salvador",
    "ves": "Villa El Salvador",
    "villa maria": "Villa María del Triunfo",
    "cercado": "Cercado de Lima",
    "cercado de lima": "Cercado de Lima",
    "lima centro": "Cercado de Lima",
}


def normalize_place(text: str) -> str:
    text = (text or "").casefold().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_alias(text: str) -> str | None:
    """Devuelve distrito canónico si el texto (o un fragmento) matchea un alias."""
    norm = normalize_place(text)
    if not norm:
        return None
    if norm in PLACE_ALIASES:
        return PLACE_ALIASES[norm]
    # Buscar alias contenido en la frase (más largo primero).
    for alias in sorted(PLACE_ALIASES.keys(), key=len, reverse=True):
        if alias in norm:
            return PLACE_ALIASES[alias]
    return None
