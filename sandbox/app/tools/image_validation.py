"""Validación defensiva de imágenes devueltas por el catálogo."""
from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image

from app.config import settings

log = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_MAX_REDIRECTS = 3
_IMAGE_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
_TOTAL_VALIDATION_SECONDS = 10.0


def _is_trusted_host(hostname: str) -> bool:
    """Limita imágenes al dominio configurado del catálogo y sus subdominios."""
    configured = (urlsplit(settings.donregalo_api_base).hostname or "").lower()
    configured = configured.removeprefix("www.")
    candidate = (hostname or "").lower().rstrip(".").removeprefix("www.")
    return bool(configured) and (
        candidate == configured or candidate.endswith("." + configured)
    )


async def _is_public_url(url: str) -> bool:
    """Bloquea localhost y redes internas antes de descargar o redirigir."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or not _is_trusted_host(parsed.hostname)
    ):
        return False
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        ips = {ipaddress.ip_address(item[4][0]) for item in addresses}
        return bool(ips) and all(ip.is_global for ip in ips)
    except (OSError, ValueError):
        return False


async def _download_limited(
    client: httpx.AsyncClient, initial_url: str
) -> tuple[bytes, str]:
    current_url = initial_url
    for _ in range(_MAX_REDIRECTS + 1):
        if not await _is_public_url(current_url):
            raise ValueError("destino no público")
        async with client.stream(
            "GET",
            current_url,
            follow_redirects=False,
            timeout=_IMAGE_TIMEOUT,
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirección sin destino")
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            mime = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .lower()
            )
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > _MAX_IMAGE_BYTES:
                    raise ValueError("imagen excede 12 MB")
            return bytes(content), mime
    raise ValueError("demasiadas redirecciones")


async def is_valid_image(
    client: httpx.AsyncClient, product: dict[str, Any]
) -> bool:
    """Comprueba que la URL responda con una imagen real, no HTML con HTTP 200."""
    url = str(product.get("imagen_url") or "").strip()
    product_id = product.get("id_producto") or product.get("id") or "?"
    if not url:
        log.warning("[imagen-invalida] producto=%s sin URL", product_id)
        return False

    try:
        content, mime = await _download_limited(client, url)
        if not mime.startswith("image/"):
            raise ValueError(f"content-type={mime or 'ausente'}")
        if not content:
            raise ValueError(f"tamano={len(content)}")
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        return True
    except Exception as exc:
        log.warning(
            "[imagen-invalida] producto=%s url=%s motivo=%s",
            product_id,
            url,
            exc,
        )
        return False


async def valid_products(
    client: httpx.AsyncClient,
    products: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Conserva el orden y sigue buscando hasta completar el cupo válido."""
    valid: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(_TOTAL_VALIDATION_SECONDS):
            cursor = 0
            target = max(0, limit)
            while len(valid) < target and cursor < len(products):
                needed = target - len(valid)
                batch = products[cursor : cursor + needed]
                cursor += len(batch)
                checks = await asyncio.gather(
                    *(is_valid_image(client, product) for product in batch)
                )
                valid.extend(
                    product
                    for product, accepted in zip(batch, checks)
                    if accepted
                )
    except TimeoutError:
        log.warning(
            "[imagen-invalida] validación agotó %.1fs; se muestran %s productos",
            _TOTAL_VALIDATION_SECONDS,
            len(valid),
        )
    return valid
