"""Plantillas deterministas de salida (productos / cobertura) — reduce ruido del LLM."""
from __future__ import annotations

from typing import Any


def render_product_list(
    products: list[dict[str, Any]],
    *,
    closing: str = "¿Quieres más detalles de alguno, o prefieres que busque más opciones? 😊",
) -> str:
    """Arma el paquete WhatsApp: URL + viñeta por producto, sin duplicados."""
    blocks: list[str] = []
    seen: set[int] = set()
    for p in products:
        pid = p.get("id_producto")
        if pid is not None:
            try:
                pid_i = int(pid)
            except (TypeError, ValueError):
                pid_i = None
            if pid_i is not None:
                if pid_i in seen:
                    continue
                seen.add(pid_i)

        name = (p.get("nombre") or p.get("name") or "Producto").strip()
        precio_sol = p.get("precio_sol") or p.get("precio_soles")
        precio_usd = p.get("precio") or p.get("precio_usd")
        desc = (p.get("descripcion_corta") or p.get("descripcion") or "").strip()
        if desc and len(desc) > 120:
            desc = desc[:117] + "…"

        price_bit = ""
        if precio_sol is not None and precio_usd is not None:
            price_bit = f" — S/{_fmt_money(precio_sol)} (${_fmt_money(precio_usd)})"
        elif precio_sol is not None:
            price_bit = f" — S/{_fmt_money(precio_sol)}"
        elif precio_usd is not None:
            price_bit = f" — ${_fmt_money(precio_usd)}"

        line = f"• 🎁 *{name}*{price_bit}"
        if desc:
            line = f"{line}\n  {desc}"

        url = (p.get("imagen_url") or "").strip()
        if url:
            blocks.append(f"{url}\n{line}")
        else:
            blocks.append(line)

    body = "\n\n".join(blocks)
    if closing and body:
        return f"{body}\n\n{closing}"
    return body or closing


def render_coverage(
    *,
    district: str | None = None,
    covered: bool | None = None,
    fee_sol: float | None = None,
    fee_usd: float | None = None,
    ask: str = "",
    suggest_maps: bool = False,
    place_query: str = "",
) -> str:
    """Una sola burbuja de cobertura (nunca confirmar + preguntar + reconfirmar)."""
    if suggest_maps or covered is None:
        place = place_query or "ese lugar"
        return (
            f"No ubico “{place}” en nuestra lista 😊 "
            "¿Lo buscas un momento en Google Maps y me dices el "
            "distrito que aparece? Con eso te confirmo la tarifa al toque."
        )

    if covered is False:
        return (
            "Lo sentimos, por el momento solo realizamos delivery dentro de "
            "Lima Metropolitana 🙏 ¿Tienes otra dirección en Lima?"
        )

    fee = ""
    if fee_sol is not None and fee_usd is not None:
        fee = f" El envío es S/{_fmt_money(fee_sol)} (${_fmt_money(fee_usd)})."
    elif fee_sol is not None:
        fee = f" El envío es S/{_fmt_money(fee_sol)}."

    dist = district or "tu distrito"
    base = f"¡Sí llegamos a {dist}!{fee}"
    if ask:
        return f"{base} {ask}".strip()
    return f"{base} ¿Qué regalo quieres enviar? 🎁"


def _fmt_money(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n == int(n):
        return f"{int(n)}.00"
    return f"{n:.2f}"
