"""Smoke test del MCP desplegado de Don Regalo.

Uso:
  DONREGALO_MCP_TOKEN=... python scripts/smoke_mcp.py

Opcionalmente valida rastreo protegido:
  ... --tracking-email cliente@example.com --tracking-code ABC123
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import os
from typing import Any

import httpx


PROTOCOL_VERSION = "2025-06-18"
EXPECTED_TOOLS = {
    "donregalo_navegacion_catalogo",
    "donregalo_buscar_productos",
    "donregalo_detalle_producto",
    "donregalo_cobertura_distrito",
    "donregalo_validar_activos",
    "donregalo_productos_destacados",
    "donregalo_productos_ofertas",
    "donregalo_metodos_pago",
    "donregalo_rastrear_pedido",
}


class SmokeFailure(RuntimeError):
    pass


class McpSmoke:
    def __init__(self, client: httpx.AsyncClient, url: str, token: str) -> None:
        self.client = client
        self.url = url
        self.token = token
        self.ids = itertools.count(1)
        self.initialized = False

    def headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.initialized:
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        return headers

    async def request(self, method: str, params: dict | None = None) -> dict:
        request_id = next(self.ids)
        response = await self.client.post(
            self.url,
            headers=self.headers(),
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                **({"params": params} if params is not None else {}),
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            raise SmokeFailure(f"{method}: {body['error']}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise SmokeFailure(f"{method}: respuesta sin result")
        return result

    async def initialize(self) -> None:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "donregalo-smoke", "version": "1.0.0"},
            },
        )
        if result.get("protocolVersion") != PROTOCOL_VERSION:
            raise SmokeFailure(f"versión negociada inesperada: {result.get('protocolVersion')}")
        self.initialized = True
        response = await self.client.post(
            self.url,
            headers=self.headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        if response.status_code != 202:
            raise SmokeFailure(f"notifications/initialized devolvió HTTP {response.status_code}")

    async def tool(self, name: str, arguments: dict[str, Any]) -> dict:
        result = await self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if result.get("isError"):
            text = ((result.get("content") or [{}])[0]).get("text", "")
            raise SmokeFailure(f"{name}: {text}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise SmokeFailure(f"{name}: falta structuredContent")
        return structured


async def run(args: argparse.Namespace) -> None:
    token = args.token or os.getenv("DONREGALO_MCP_TOKEN", "")
    if not token:
        raise SmokeFailure("falta DONREGALO_MCP_TOKEN o --token")

    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
        smoke = McpSmoke(client, args.url, token)
        await smoke.initialize()

        definitions = await smoke.request("tools/list")
        names = {tool["name"] for tool in definitions.get("tools", [])}
        if names != EXPECTED_TOOLS:
            raise SmokeFailure(
                f"tools/list difiere: faltan={sorted(EXPECTED_TOOLS - names)}, "
                f"sobran={sorted(names - EXPECTED_TOOLS)}"
            )

        await smoke.tool("donregalo_navegacion_catalogo", {})
        search = await smoke.tool(
            "donregalo_buscar_productos",
            {"categoria": "desayunos", "limite": 6},
        )
        products = search.get("productos") or []
        if not products:
            raise SmokeFailure("buscar_productos no devolvió desayunos")
        first = products[0]
        await smoke.tool("donregalo_detalle_producto", {"id": first["id"]})
        await smoke.tool("donregalo_cobertura_distrito", {"distrito": "Miraflores"})
        await smoke.tool("donregalo_validar_activos", {"ids": [first["id"]]})
        await smoke.tool("donregalo_productos_destacados", {"limite": 3})
        await smoke.tool("donregalo_productos_ofertas", {"limite": 3})
        await smoke.tool("donregalo_metodos_pago", {})

        image_url = str(first.get("imagen_url") or "")
        if not image_url:
            raise SmokeFailure("el primer producto no trae imagen_url")
        image = await client.get(image_url)
        image.raise_for_status()
        mime = image.headers.get("content-type", "").split(";", 1)[0].lower()
        if not mime.startswith("image/") or not image.content:
            raise SmokeFailure(f"imagen inválida: content-type={mime!r}")

        if args.tracking_email and args.tracking_code:
            await smoke.tool(
                "donregalo_rastrear_pedido",
                {"email": args.tracking_email, "codigo": args.tracking_code},
            )

    print("OK: lifecycle, 9 tools, catálogo, detalle, cobertura, ofertas, pagos e imagen")
    if not (args.tracking_email and args.tracking_code):
        print("AVISO: rastreo omitido; proporciona --tracking-email y --tracking-code")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="https://www.donregalo.pe/clienteApiApp/mcp/",
    )
    parser.add_argument("--token", default="")
    parser.add_argument("--tracking-email")
    parser.add_argument("--tracking-code")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
