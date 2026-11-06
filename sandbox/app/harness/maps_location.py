"""Read a Google Maps link as location evidence, never as instructions.

Only Google Maps origins are fetched, including every redirect. No arbitrary
URLs, geocoding vendor, or LLM is used. Ambiguous/coordinate-only links abstain.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.harness.aliases import PLACE_ALIASES, normalize_place
from app.harness.delivery_request import URL_RE


def allowed_maps_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
            return False
        if p.hostname == "maps.app.goo.gl":
            return True
        if p.hostname == "goo.gl":
            return p.path.startswith("/maps/")
        if p.hostname == "maps.google.com":
            return True
        return p.hostname in {"google.com", "www.google.com", "google.com.pe", "www.google.com.pe"} and p.path.startswith("/maps")
    except ValueError:
        return False


def extract_maps_url(text: str) -> str:
    return next((url.rstrip(".,);]") for url in URL_RE.findall(text or "") if allowed_maps_url(url.rstrip(".,);]"))), "")


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and (data.get("property") or data.get("name")) in {"og:title", "og:description", "description"}:
            self.values.append(data.get("content") or "")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.values.append(data)


def district_from_metadata(values: list[str]) -> str:
    # Only canonical district names. No informal aliases such as 'Palao'.
    names = sorted(set(PLACE_ALIASES.values()), key=len, reverse=True)
    found: set[str] = set()
    for value in values:
        raw = normalize_place(value)
        for name in names:
            norm = normalize_place(name)
            pattern = rf"(?<!\w){re.escape(norm)}(?!\w)"
            if re.search(pattern, raw):
                found.add(name)
                raw = re.sub(pattern, " ", raw)
    # Province 'Callao' can accompany its explicitly named district.
    if len(found) > 1 and "Callao" in found:
        found.remove("Callao")
    return next(iter(found)) if len(found) == 1 else ""


async def resolve_maps_district(url: str, *, client: httpx.AsyncClient | None = None) -> str:
    if not allowed_maps_url(url):
        return ""
    own = client is None
    client = client or httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False)
    try:
        for _ in range(5):
            if not allowed_maps_url(url):
                return ""
            async with client.stream("GET", url, follow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    return ""
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 512_000:
                        return ""
                metadata = _Metadata()
                metadata.feed(chunks.decode("utf-8", errors="replace"))
                path = unquote(urlsplit(url).path).replace("+", " ")
                return district_from_metadata([path, *metadata.values])
    except (httpx.HTTPError, ValueError):
        return ""
    finally:
        if own:
            await client.aclose()
    return ""
