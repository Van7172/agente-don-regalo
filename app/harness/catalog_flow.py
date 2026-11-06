"""Catálogo secuencial para pedidos concretos; devuelve hechos, no efectos externos.

Reutiliza executor, relevancia, atributos, presupuesto y render del harness.
LangGraph y el experimento local no forman parte de este runtime.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable

from app.harness.budget import extract_budget
from app.harness.checkout import checkout_active
from app.harness.contracts import AgentResult, Turn, extract_products
from app.harness.product_links import extract_product_url_slug
from app.harness.state import ConversationState
from app.harness.quoting import internal_leak
from app.guardrails.input import sanitize_untrusted_text
from app.guardrails.privacy import redact_personal_data
from app.observability import record_operation
from app.services import preempt
from app.tools import attributes
from app.tools.executor import enforce_category
from app.tools.relevance import continue_query, parse_request, select_products, wants_more

log = logging.getLogger(__name__)
Execute = Callable[[str, dict], Awaitable[str]]
_COMPLEX = re.compile(r"\b(sin|solamente|excepto|exclusivamente|personalizar|cambiar|quitar|agregar)\b", re.I)


def eligible(turn: Turn, state: ConversationState, *, extra_system: str = "", was_checkout: bool = False) -> bool:
    if turn.has_media or turn.quoted or extra_system or was_checkout or checkout_active(state):
        return False
    if not turn.text or len(turn.text) > 300 or extract_product_url_slug(turn.text) or _COMPLEX.search(turn.text):
        return False
    query = continue_query(turn.text, state.catalog_query)
    request = parse_request(query)
    # En v1, una categoría reconocible y una característica concreta. Cantidades
    # y consultas compuestas siguen con el especialista hasta validarlas aparte.
    if re.search(r"\d", turn.text) and extract_budget(turn.text) is None:
        return False
    return bool(request.category and request.specific)


def _items(payload) -> list[dict] | None:
    if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("data"), list):
        return None
    return [p for p in payload["data"] if isinstance(p, dict)]


def _available(product: dict) -> bool:
    stock = product.get("stock")
    if stock is None:
        return True
    try:
        return float(stock) > 0
    except (TypeError, ValueError):
        return True  # desconocido, no equivale a agotado


def _safe_text(product: dict) -> dict | None:
    safe = dict(product)
    for key in ("nombre", "descripcion_corta", "descripcion"):
        value = str(safe.get(key) or "")
        _, removed = sanitize_untrusted_text(value)
        if removed or redact_personal_data(value).redacted_count or internal_leak(value):
            record_operation("catalog.product_text", "sanitized")
            if key == "nombre":
                return None
            # Las etiquetas de redacción son internas; omitir la descripción
            # contaminada conserva nombre/precio/foto sin mostrar marcadores.
            safe[key] = ""
    return safe


async def run(turn: Turn, state: ConversationState, execute: Execute) -> AgentResult | None:
    """None pide al master continuar por el camino actual ante un fallo técnico."""
    # Imports diferidos para reutilizar las funciones actuales sin un ciclo de imports.
    from app.harness.master import _apply_budget, compose_product_reply

    started = time.monotonic()
    query = continue_query(turn.text, state.catalog_query)
    request = parse_request(query)
    text_token = attributes.set_turn_text(query)
    budget_token = attributes.set_turn_budget((state.budget_amount, state.budget_currency) if state.budget_amount else None)
    excluded = state.shown_product_ids if wants_more(turn.text) else []
    excluded_token = attributes.set_turn_excluded_ids(excluded)
    outcome = "error"
    used = []
    try:
        preempt.check()
        used.append("buscar_productos")
        payload = json.loads(await execute("buscar_productos", {"q": query, "categoria": request.category}))
        items = _items(payload)
        if items is None:
            return None
        items = [safe for item in items if (safe := _safe_text(item)) is not None]
        data = enforce_category({"data": items}, request.category)
        data = attributes.enforce_attributes(data, attributes.extract_color_attributes(query))
        products, near = select_products([p for p in data["data"] if _available(p) and p.get("id_producto") not in excluded], request, limit=3)
        approximate = bool(payload.get("aproximado") or near)
        if len(products) == 1 and not approximate:
            pid = products[0].get("id_producto")
            used.append("detalle_producto")
            try:
                detail = json.loads(await execute("detalle_producto", {"id_producto": pid})).get("data")
            except (ValueError, TypeError, AttributeError):
                detail = None
            if isinstance(detail, dict) and detail.get("id_producto") == pid:
                detail = _safe_text(detail)
            if isinstance(detail, dict) and detail.get("id_producto") == pid:
                if not _available(detail):
                    products = []
                else:
                    checked = enforce_category({"data": [detail]}, request.category)
                    checked = attributes.enforce_attributes(checked, attributes.extract_color_attributes(query))
                    verified, near_detail = select_products(checked["data"], request, limit=1)
                    if verified and not near_detail:
                        products = verified
        preempt.check()
        patch = {"catalog_query": request.text, "recent_options": [], "menu_depth": 0}
        if not products:
            outcome = "empty"
            return AgentResult(user_facing="No encontré opciones disponibles que pueda verificar para ese pedido. ¿Me compartes el nombre o el enlace del producto, o prefieres ajustar alguna característica? 😊",
                               state_patch=patch, tools_used=used, catalog_strategy="deterministic")
        intro = ("No pude confirmar todas las características pedidas. Estas son alternativas de la categoría:"
                 if approximate else "Te muestro las opciones que coinciden con lo que buscas 😊")
        artifacts, intro, amounts = _apply_budget(state, extract_products({"data": products}), intro)
        outcome = "approximate" if approximate else "exact"
        return AgentResult(user_facing=compose_product_reply(intro, artifacts), artifacts=artifacts,
                           state_patch=patch, tools_used=used, sourced_prices=amounts,
                           catalog_approximate=approximate, unmet_requirements=list(request.terms) if approximate else [],
                           catalog_strategy="deterministic")
    except (preempt.TurnAborted, asyncio.CancelledError):
        outcome = "cancelled"
        raise
    except Exception as error:
        log.warning("[catalog-flow] continúa el especialista actual (%s)", type(error).__name__)
        return None
    finally:
        record_operation("catalog.flow", outcome, duration_ms=(time.monotonic() - started) * 1000)
        attributes.reset_turn_excluded_ids(excluded_token)
        attributes.reset_turn_budget(budget_token)
        attributes.reset_turn_text(text_token)
