"""Cliente del MCP de catálogo de Don Regalo (Streamable HTTP, JSON-RPC 2.0).

Camino alterno para las lecturas de catálogo. Devuelve **exactamente la misma
forma canónica** que `catalog.*` (reusa `adapters`), así que el resto del harness
—`executor`, `render`, especialistas— no nota la diferencia. Las imágenes se
validan con la misma política defensiva del camino REST: una URL construida por
el servidor no se considera viva hasta comprobar contenido y formato.

Tres reglas que gobiernan este módulo:

1. **Opt-in.** Solo se usa si `settings.donregalo_use_mcp` (que exige además el
   token). Apagado, este módulo no se toca y el bot sigue con HTTP directo.
2. **Degrada, no calla.** Ante CUALQUIER fallo del MCP (red, protocolo, JSON) se
   cae a la función HTTP equivalente de `catalog`. Un MCP caído no debe tumbar un
   turno — misma filosofía que el claim del outbox.
3. **El MCP no reemplaza todo.** Cobertura sigue en HTTP porque el matcher
   determinista necesita la lista completa de distritos y la tool MCP resuelve
   solo un nombre. La navegación sí se adapta aquí a la forma REST que ya consume
   el menú, y la validación de ids activos usa la tool MCP antes de ofrecer
   resultados de Qdrant.
"""
from __future__ import annotations

import itertools
import json
import logging
import time
from typing import Any

import httpx

from app.config import settings
from app.observability import audit_event, record_operation
from app.resilience import circuit_breaker
from app.tools import adapters, catalog
from app.tools.image_validation import valid_products

log = logging.getLogger(__name__)


class McpError(RuntimeError):
    """Fallo a nivel de protocolo/transporte del MCP (no un isError de negocio)."""


# ─── Transporte ──────────────────────────────────────────────────────────────

_PROTOCOL_VERSION = "2025-06-18"
_IMAGE_CANDIDATE_POOL = 30  # máximo publicado por el MCP
_request_ids = itertools.count(1)


def _headers(*, initialized: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.donregalo_mcp_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if initialized:
        headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
    return headers


def _decode_response(response: httpx.Response, request_id: int) -> dict:
    """Decodifica una respuesta Streamable HTTP JSON o SSE."""
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise McpError("respuesta MCP no contiene JSON válido") from exc
        if not isinstance(body, dict):
            raise McpError("respuesta MCP no es un objeto JSON-RPC")
        return body

    # Un POST puede responder con un stream SSE. Cada evento puede tener varias
    # líneas data:; elegimos la respuesta JSON-RPC de nuestro request id.
    data_lines: list[str] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line == "" and data_lines:
            try:
                candidate = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                candidate = None
            data_lines = []
            if isinstance(candidate, dict) and candidate.get("id") == request_id:
                return candidate
    if data_lines:
        try:
            candidate = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict) and candidate.get("id") == request_id:
            return candidate
    raise McpError("stream SSE terminó sin la respuesta JSON-RPC solicitada")


async def _post(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    initialized: bool = False,
    expect_response: bool = True,
) -> dict:
    response = await client.post(
        settings.donregalo_mcp_url,
        json=payload,
        headers=_headers(initialized=initialized),
    )
    response.raise_for_status()
    if not expect_response:
        if response.status_code not in (200, 202, 204):
            raise McpError(f"notificación MCP rechazada con HTTP {response.status_code}")
        return {}
    return _decode_response(response, int(payload["id"]))


async def _ensure_initialized(client: httpx.AsyncClient) -> None:
    """Negocia lifecycle una vez por conexión httpx."""
    if getattr(client, "_donregalo_mcp_initialized", False):
        return

    request_id = next(_request_ids)
    body = await _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "agente-don-regalo",
                    "version": "1.0.0",
                },
            },
        },
    )
    if body.get("error"):
        raise McpError(str(body["error"]))
    result = body.get("result")
    if not isinstance(result, dict):
        raise McpError("initialize MCP sin `result`")
    if result.get("protocolVersion") != _PROTOCOL_VERSION:
        raise McpError(
            "versión MCP incompatible: "
            f"{result.get('protocolVersion')!r}; se requiere {_PROTOCOL_VERSION}"
        )

    await _post(
        client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        initialized=True,
        expect_response=False,
    )
    setattr(client, "_donregalo_mcp_initialized", True)


async def _call_unobserved(
    client: httpx.AsyncClient,
    tool: str,
    arguments: dict,
) -> dict:
    """Ejecuta `tools/call` y devuelve el `result` (con content/structuredContent/isError).

    Lanza McpError ante error de transporte, JSON-RPC `error`, o falta de `result`.
    Un `isError: true` de negocio NO es excepción: lo inspecciona quien llama.
    """
    await _ensure_initialized(client)
    request_id = next(_request_ids)
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    body = await _post(client, payload, initialized=True)
    if body.get("error"):
        raise McpError(str(body["error"]))
    result = body.get("result")
    if not isinstance(result, dict):
        raise McpError("respuesta MCP sin `result`")
    return result


async def _call(client: httpx.AsyncClient, tool: str, arguments: dict) -> dict:
    started = time.monotonic()
    try:
        result = await circuit_breaker("mcp").call(
            lambda: _call_unobserved(client, tool, arguments)
        )
    except Exception as error:
        latency_ms = (time.monotonic() - started) * 1000
        record_operation("mcp.call", "error", duration_ms=latency_ms)
        audit_event(
            "mcp.call",
            "error",
            backend="mcp",
            tool=tool,
            latency_ms=latency_ms,
            error_type=type(error).__name__,
        )
        raise
    latency_ms = (time.monotonic() - started) * 1000
    outcome = "business_error" if result.get("isError") else "ok"
    record_operation("mcp.call", outcome, duration_ms=latency_ms)
    audit_event(
        "mcp.call",
        outcome,
        backend="mcp",
        tool=tool,
        latency_ms=latency_ms,
    )
    return result


def _text(result: dict) -> str:
    """Texto humano del resultado (para logs de isError)."""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "")
    return ""


# ─── Mapeo MCP → forma que entiende `adapters` ───────────────────────────────
#
# El MCP ya devuelve productos casi canónicos; solo cambian dos claves respecto a
# lo que `adapters.product` espera de un item de la API REST. Renombrándolas, el
# adapter hace el resto (precio→soles, limpieza de HTML, forma canónica).

def _map_product(item: dict) -> dict:
    raw = dict(item)
    if "id" in raw and "id_producto" not in raw:
        raw["id_producto"] = raw.pop("id")
    if "en_oferta" in raw:
        raw["tiene_oferta"] = raw.pop("en_oferta")
    if "precio_usd" in raw and "precio_final" not in raw:
        raw["precio_final"] = raw["precio_usd"]
    if "precio_antes_usd" in raw and "precio_producto" not in raw:
        raw["precio_producto"] = raw["precio_antes_usd"]
    return raw


def _list_payload(structured: dict, rate: float) -> dict:
    productos = [p for p in (structured.get("productos") or []) if isinstance(p, dict)]
    raw = {"data": [_map_product(p) for p in productos]}
    return adapters.products_payload(raw, rate)


def _detail_payload(structured: dict, rate: float) -> dict:
    return adapters.products_payload({"data": _map_product(structured)}, rate)


def _payment_payload(structured: dict) -> dict:
    metodos = [m for m in (structured.get("metodos") or []) if isinstance(m, dict)]
    data = [
        {
            "id_metodo_pago": m.get("id"),
            "nombre_metodo_pago": m.get("nombre"),
            "descripcion_metodo_pago": m.get("descripcion"),
        }
        for m in metodos
    ]
    return adapters.payment_methods_payload({"data": data})


def _navigation_payload(structured: dict) -> dict:
    """Taxonomía MCP → sobre compatible con `/catalogo/navegacion`."""
    categorias = []
    for category in structured.get("categorias") or []:
        if not isinstance(category, dict):
            continue
        categorias.append({
            "nombre": category.get("nombre"),
            "url_categoria": category.get("slug"),
            "subcategorias": [
                {"nombre": sub.get("nombre"), "url_categoria": sub.get("slug")}
                for sub in category.get("subcategorias") or []
                if isinstance(sub, dict)
            ],
            "landings": [
                {"nombre": item.get("nombre"), "slug_landing": item.get("slug")}
                for item in category.get("landings") or []
                if isinstance(item, dict)
            ],
        })
    return {
        "success": True,
        "message": "OK",
        "data": {
            "categorias": categorias,
            "filtros": list(structured.get("filtros") or []),
            "ocasiones": list(structured.get("ocasiones") or []),
        },
    }


def _buscar_args(args: dict) -> dict:
    """Args estilo `catalog.buscar_productos` → args de la tool MCP."""
    args = args or {}
    # Se pide un pool mayor que el cupo visible para poder reemplazar fotos
    # rotas sin dejar la respuesta a medias.
    out: dict[str, Any] = {"limite": _IMAGE_CANDIDATE_POOL}
    if args.get("q"):
        out["q"] = args["q"]
    for key in ("categoria", "filtro", "landing"):
        value = (args.get(key) or "").strip("/")
        if value:
            out[key] = value
    if args.get("orden") in ("asc", "desc"):
        out["orden"] = args["orden"]
    if args.get("id_ocasion"):
        out["ocasion"] = int(args["id_ocasion"])
    return out


async def _validated_list(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    limit: int = catalog.DEFAULT_PER_PAGE,
) -> dict:
    items = payload.get("data")
    if not isinstance(items, list):
        raise McpError("respuesta MCP de listado sin `data` array")
    validated = await valid_products(client, items, limit=limit)
    return {**payload, "data": validated, "total": len(validated)}


async def _validated_detail(client: httpx.AsyncClient, payload: dict) -> dict:
    detail = payload.get("data")
    if not isinstance(detail, dict) or not detail.get("id_producto"):
        raise McpError("respuesta MCP de detalle sin producto canónico")
    validated = await valid_products(client, [detail], limit=1)
    if validated:
        return {**payload, "data": validated[0]}
    # Igual que REST: en detalle se conserva la ficha y se retira únicamente
    # una imagen inválida, porque el cliente ya preguntó por ese producto.
    return {**payload, "data": {**detail, "imagen_url": ""}}


# ─── Funciones compatibles con `catalog.*` ───────────────────────────────────
#
# Misma firma `(client, args)` y misma salida canónica. Cada una degrada a HTTP.

async def explorar_catalogo(client: httpx.AsyncClient, args: dict):
    try:
        result = await _call(
            client,
            "donregalo_navegacion_catalogo",
            {"incluir_campanas": bool((args or {}).get("incluir_temporales"))},
        )
        if result.get("isError"):
            raise McpError(_text(result))
        return _navigation_payload(result.get("structuredContent") or {})
    except Exception as err:
        log.warning("[mcp] explorar_catalogo degradó a HTTP (%s)", type(err).__name__)
        return await catalog.explorar_catalogo(client, args)


async def buscar_productos(client: httpx.AsyncClient, args: dict):
    try:
        result = await _call(client, "donregalo_buscar_productos", _buscar_args(args))
        if result.get("isError"):
            log.info("[mcp] buscar_productos isError")
            return {"data": [], "total": 0}
        rate = await adapters.usd_pen_rate(client)
        payload = _list_payload(result.get("structuredContent") or {}, rate)
        return await _validated_list(client, payload)
    except Exception as err:
        log.warning("[mcp] buscar_productos degradó a HTTP (%s)", type(err).__name__)
        return await catalog.buscar_productos(client, args)


async def catalogo_categoria(client: httpx.AsyncClient, args: dict):
    slug = (args.get("slug") or "").strip("/")
    try:
        mcp_args: dict[str, Any] = {"limite": _IMAGE_CANDIDATE_POOL}
        if slug:
            mcp_args["categoria"] = slug
        result = await _call(client, "donregalo_buscar_productos", mcp_args)
        if result.get("isError"):
            return {"data": [], "total": 0}
        rate = await adapters.usd_pen_rate(client)
        payload = await _validated_list(
            client,
            _list_payload(result.get("structuredContent") or {}, rate),
        )

        # Igual que catalog.catalogo_categoria: una categoría fúnebre pedida
        # explícitamente devuelve 0 sin el flag, así que se reintenta.
        if slug and not payload.get("data"):
            mcp_args["incluir_funebre"] = True
            result = await _call(client, "donregalo_buscar_productos", mcp_args)
            if not result.get("isError"):
                payload = await _validated_list(
                    client,
                    _list_payload(result.get("structuredContent") or {}, rate),
                )
        return payload
    except Exception as err:
        log.warning("[mcp] catalogo_categoria degradó a HTTP (%s)", type(err).__name__)
        return await catalog.catalogo_categoria(client, args)


async def detalle_producto(client: httpx.AsyncClient, args: dict):
    try:
        pid = int(args["id_producto"])
        result = await _call(client, "donregalo_detalle_producto", {"id": pid})
        if result.get("isError"):
            log.info("[mcp] detalle %s isError", pid)
            return {"data": {}}
        rate = await adapters.usd_pen_rate(client)
        payload = _detail_payload(result.get("structuredContent") or {}, rate)
        return await _validated_detail(client, payload)
    except Exception as err:
        log.warning("[mcp] detalle_producto degradó a HTTP (%s)", type(err).__name__)
        return await catalog.detalle_producto(client, args)


async def productos_por_ocasion(client: httpx.AsyncClient, args: dict):
    try:
        result = await _call(
            client,
            "donregalo_buscar_productos",
            {"ocasion": int(args["id_ocasion"]), "limite": _IMAGE_CANDIDATE_POOL},
        )
        if result.get("isError"):
            return {"data": [], "total": 0}
        rate = await adapters.usd_pen_rate(client)
        payload = _list_payload(result.get("structuredContent") or {}, rate)
        return await _validated_list(client, payload)
    except Exception as err:
        log.warning("[mcp] productos_por_ocasion degradó a HTTP (%s)", type(err).__name__)
        return await catalog.productos_por_ocasion(client, args)


async def productos_destacados(client: httpx.AsyncClient, _args: dict):
    try:
        result = await _call(
            client, "donregalo_productos_destacados", {"limite": _IMAGE_CANDIDATE_POOL}
        )
        if result.get("isError"):
            return {"data": [], "total": 0}
        rate = await adapters.usd_pen_rate(client)
        payload = _list_payload(result.get("structuredContent") or {}, rate)
        return await _validated_list(client, payload)
    except Exception as err:
        log.warning("[mcp] productos_destacados degradó a HTTP (%s)", type(err).__name__)
        return await catalog.productos_destacados(client, _args)


async def productos_oferta(client: httpx.AsyncClient, _args: dict):
    try:
        result = await _call(
            client, "donregalo_productos_ofertas", {"limite": _IMAGE_CANDIDATE_POOL}
        )
        if result.get("isError"):
            return {"data": [], "total": 0}
        rate = await adapters.usd_pen_rate(client)
        payload = _list_payload(result.get("structuredContent") or {}, rate)
        return await _validated_list(client, payload)
    except Exception as err:
        log.warning("[mcp] productos_oferta degradó a HTTP (%s)", type(err).__name__)
        return await catalog.productos_oferta(client, _args)


async def metodos_pago(client: httpx.AsyncClient, _args: dict):
    try:
        result = await _call(client, "donregalo_metodos_pago", {})
        if result.get("isError"):
            raise McpError(_text(result))
        return _payment_payload(result.get("structuredContent") or {})
    except Exception as err:
        log.warning("[mcp] metodos_pago degradó a HTTP (%s)", type(err).__name__)
        return await catalog.metodos_pago(client, _args)


async def rastrear_pedido(client: httpx.AsyncClient, args: dict):
    try:
        result = await _call(
            client,
            "donregalo_rastrear_pedido",
            {"email": args.get("email", ""), "codigo": args.get("codigo", "")},
        )
        # El rastreo mantiene el sobre {success, message, data} del REST, que es lo
        # que consume el agente de tracking.
        if result.get("isError"):
            return {"success": False, "message": _text(result), "data": None}
        return {"success": True, "message": "OK", "data": result.get("structuredContent") or {}}
    except Exception as err:
        log.warning("[mcp] rastrear_pedido degradó a HTTP (%s)", type(err).__name__)
        return await catalog.rastrear_pedido(client, args)


async def productos_activos(
    client: httpx.AsyncClient, ids: list[int]
) -> set[int] | None:
    """Valida ids de Qdrant/estado vía MCP; `None` si tampoco responde REST."""
    normalized = sorted({int(pid) for pid in ids if pid is not None and int(pid) > 0})
    if not normalized:
        return set()
    try:
        result = await _call(client, "donregalo_validar_activos", {"ids": normalized})
        if result.get("isError"):
            raise McpError(_text(result))
        activos = (result.get("structuredContent") or {}).get("activos")
        if not isinstance(activos, list):
            raise McpError("respuesta MCP de activos sin `activos` array")
        return {int(pid) for pid in activos}
    except Exception as err:
        log.warning("[mcp] productos_activos degradó a HTTP (%s)", type(err).__name__)
        return await catalog.productos_activos(client, normalized)


# Nombres de tool del harness que este módulo sabe resolver por MCP. El resto
# (`distritos_cobertura`, `tipo_cambio`) se queda en HTTP a propósito.
SUPPORTED = {
    "explorar_catalogo":     explorar_catalogo,
    "buscar_productos":     buscar_productos,
    "catalogo_categoria":   catalogo_categoria,
    "detalle_producto":     detalle_producto,
    "productos_por_ocasion": productos_por_ocasion,
    "productos_destacados": productos_destacados,
    "productos_oferta":     productos_oferta,
    "metodos_pago":         metodos_pago,
    "rastrear_pedido":      rastrear_pedido,
}
