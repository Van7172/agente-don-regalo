"""Documento canónico y metadatos del índice RAG de productos."""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

INDEX_SCHEMA_VERSION = 2

_SLUG_PARENT = {
    "desayunos-criollos": "desayunos",
    "desayunos-de-amor": "desayunos",
    "desayunos-light": "desayunos",
    "desayunos-tematicos": "desayunos",
    "arreglos-florales-variados": "arreglos-florales",
    "en-canasta": "arreglos-florales",
    "arreglos-florales-con-peluche": "arreglos-florales",
    "cajas": "arreglos-florales",
    "corporativos": "arreglos-florales",
    "ramos-de-flores": "arreglos-florales",
    "floreros": "arreglos-florales",
    "arreglos-florales-de-navidad": "arreglos-florales",
    "cruces-funebres": "arreglos-funebres",
    "lagrimas-funebres": "arreglos-funebres",
    "coronas-para-difuntos": "arreglos-funebres",
    "mantos-funebres": "arreglos-funebres",
    "terrarios": "plantas",
    "orquideas": "plantas",
    "suculentas": "plantas",
    "regalos-corporativos": "cestas",
}


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _category(product: dict) -> tuple[str, str, int | None]:
    raw = product.get("categoria")
    if isinstance(raw, dict):
        name = clean_text(raw.get("nombre"))
        slug = str(raw.get("url") or raw.get("url_categoria") or "")
        category_id = raw.get("id") or raw.get("id_categoria")
    else:
        name = clean_text(raw or product.get("nombre_categoria"))
        slug = str(
            product.get("url_categoria")
            or product.get("categoria_slug")
            or product.get("categoria_url")
            or ""
        )
        category_id = product.get("id_categoria")
    try:
        parsed_id = int(category_id) if category_id is not None else None
    except (TypeError, ValueError):
        parsed_id = None
    return name, _SLUG_PARENT.get(slug, slug), parsed_id


def _occasions(product: dict) -> tuple[list[str], list[int]]:
    names: list[str] = []
    ids: list[int] = []
    for item in product.get("ocasiones") or []:
        if isinstance(item, dict):
            name = clean_text(item.get("nombre_ocasion") or item.get("nombre"))
            raw_id = item.get("id_ocasion") or item.get("id")
            if raw_id is not None:
                try:
                    ids.append(int(raw_id))
                except (TypeError, ValueError):
                    pass
        else:
            name = clean_text(item)
        if name:
            names.append(name)
    for raw_id in product.get("ocasiones_ids") or []:
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError):
            pass
    return list(dict.fromkeys(names)), list(dict.fromkeys(ids))


def build_embedding_text(product: dict) -> str:
    category_name, _, _ = _category(product)
    occasion_names, _ = _occasions(product)
    parts = [
        f"Producto: {clean_text(product.get('nombre') or product.get('nombre_producto'))}",
        f"Categoría: {category_name}",
    ]
    if occasion_names:
        parts.append("Ocasiones: " + ", ".join(occasion_names))
    short = clean_text(
        product.get("descripcion_corta") or product.get("descripcion_corta_producto")
    )
    description = clean_text(
        product.get("descripcion") or product.get("descripcion_producto")
    )
    if short:
        parts.append("Descripción corta: " + short)
    if description and description != short:
        parts.append("Descripción: " + description)
    tags = product.get("tags") or product.get("tags_producto") or []
    if isinstance(tags, (list, tuple, set)):
        tags = ", ".join(clean_text(tag) for tag in tags if clean_text(tag))
    tags = clean_text(tags)
    if tags:
        parts.append("Tags: " + tags)
    return "\n".join(part for part in parts if part.partition(":")[2].strip())


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _payload_hash(payload: dict) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_payload(
    product: dict,
    semantic_hash: str,
    *,
    model: str,
    dimensions: int,
) -> dict:
    category_name, category_slug, category_id = _category(product)
    _, occasion_ids = _occasions(product)
    product_id = int(product.get("id_producto") or product.get("id"))
    images = product.get("imagenes") or []
    image_url = product.get("imagen_url")
    if not image_url and images and isinstance(images[0], dict):
        image_url = images[0].get("medium") or images[0].get("original")
    price = (
        product.get("precio_final")
        if product.get("precio_final") is not None
        else product.get("precio")
        if product.get("precio") is not None
        else product.get("precio_producto")
    )
    payload = {
        "id_producto": product_id,
        "nombre": clean_text(product.get("nombre") or product.get("nombre_producto")),
        "precio": float(price or 0),
        "categoria": category_name,
        "categoria_slug": category_slug,
        "categoria_id": category_id,
        "ocasiones_ids": occasion_ids,
        "es_funebre": category_slug == "arreglos-funebres"
        or bool(product.get("es_funebre")),
        "stock": int(product.get("stock") or product.get("stock_producto") or 0),
        "descripcion_corta": clean_text(
            product.get("descripcion_corta")
            or product.get("descripcion_corta_producto")
        ),
        "imagen_url": image_url,
        "url": product.get("url") or product.get("url_producto") or "",
        "content_hash": semantic_hash,
        "embedding_model": model,
        "embedding_dimensions": dimensions,
        "index_schema_version": INDEX_SCHEMA_VERSION,
    }
    payload["payload_hash"] = _payload_hash(payload)
    return payload


def needs_embedding(
    existing_payload: dict | None,
    semantic_hash: str,
    *,
    model: str,
    dimensions: int,
) -> bool:
    if not existing_payload:
        return True
    return (
        existing_payload.get("content_hash") != semantic_hash
        or existing_payload.get("embedding_model") != model
        or int(existing_payload.get("embedding_dimensions") or 0) != dimensions
        or int(existing_payload.get("index_schema_version") or 0)
        != INDEX_SCHEMA_VERSION
    )
