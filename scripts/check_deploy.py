#!/usr/bin/env python
"""Comprueba que un despliegue del CRM llegó entero.

El orden es SQL → CRM PHP → agente, y los dos primeros pasos se hacen a mano
contra el hosting. Saltarse uno **no da un error**: da una pantalla vacía. Y una
tabla de Oportunidades vacía se lee exactamente igual que "no falta nada en el
catálogo" — la conclusión contraria a la verdadera. Esto convierte ese silencio
en un fallo ruidoso.

    python scripts/check_deploy.py \\
        --base-url https://donregalo.pe/crm/public \\
        --token EL_CRM_INTERNAL_TOKEN

Sin argumentos toma `CRM_BASE_URL` y `CRM_INTERNAL_TOKEN` del entorno.

**No escribe nada.** El endpoint de demanda se prueba con un cuerpo inválido a
propósito: un 400 "query required" demuestra que la ruta existe y que el token
vale, sin meter una fila de mentira en una tabla que luego se lee como señal de
negocio.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

TIMEOUT = 20.0


class Check:
    """Un resultado con su explicación. El texto es para quien despliega."""

    def __init__(self, nombre: str, ok: bool, detalle: str) -> None:
        self.nombre = nombre
        self.ok = ok
        self.detalle = detalle

    def render(self) -> str:
        marca = "OK  " if self.ok else "FALLA"
        return f"  [{marca}] {self.nombre}\n         {self.detalle}"


def _json(r: httpx.Response) -> dict | None:
    """El cuerpo como dict, o `None` si no es JSON.

    Hace falta porque PHP responde **200 con un fatal en HTML** cuando revienta
    después de mandar las cabeceras — es lo que pasa, por ejemplo, si el MySQL
    del hosting no acepta la conexión. Sin esto el propio check moría con un
    JSONDecodeError y quien despliega se quedaba sin saber qué pasó, que es
    justo lo que este script existe para evitar.
    """
    try:
        data = r.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _no_es_json(nombre: str, r: httpx.Response) -> Check:
    pista = " ".join(r.text.split())[:160]
    return Check(
        nombre,
        False,
        f"respondió {r.status_code} con algo que no es JSON — casi siempre un "
        f"fatal de PHP. Mira el log de errores del hosting. Empieza por: {pista!r}",
    )


def _check_health(client: httpx.Client, base: str) -> Check:
    try:
        r = client.get(f"{base}/api/health")
    except Exception as error:
        return Check("CRM accesible", False, f"no respondió: {type(error).__name__}: {error}")
    if r.status_code != 200:
        return Check("CRM accesible", False, f"/api/health devolvió {r.status_code}")
    data = _json(r)
    if data is None:
        return _no_es_json("CRM accesible", r)
    tenant = data.get("tenant", "?")
    return Check("CRM accesible", bool(data.get("ok")), f"/api/health responde; tenant={tenant}")


def _check_schema(client: httpx.Client, base: str, token: str) -> Check:
    try:
        r = client.get(f"{base}/api/schema", headers={"X-CRM-Token": token})
    except Exception as error:
        return Check("Migraciones corridas", False, f"no respondió: {type(error).__name__}")

    if r.status_code == 404:
        return Check(
            "Migraciones corridas",
            False,
            "no existe /api/schema: el CRM PHP subido es anterior a este check "
            "(falta el paso 3, subir public/api/index.php y src/Repository.php)",
        )
    if r.status_code in (401, 403):
        return Check(
            "Migraciones corridas",
            False,
            "el token no vale: revisa que CRM_INTERNAL_TOKEN coincida con "
            "`crm_internal_token` de config.php",
        )
    if r.status_code != 200:
        return Check("Migraciones corridas", False, f"/api/schema devolvió {r.status_code}")

    data = _json(r)
    if data is None:
        return _no_es_json("Migraciones corridas", r)

    schema = data.get("schema") or {}
    faltan = schema.get("faltan") or []
    if faltan:
        return Check(
            "Migraciones corridas",
            False,
            "faltan por correr contra el MySQL del hosting: " + ", ".join(faltan),
        )
    corridas = len(schema.get("migraciones") or {})
    return Check("Migraciones corridas", True, f"las {corridas} esperadas están aplicadas")


def _check_demand_endpoint(client: httpx.Client, base: str, token: str) -> Check:
    """Sin escribir: un cuerpo inválido basta para saber si la ruta está viva."""
    try:
        r = client.post(
            f"{base}/api/demand",
            headers={"X-CRM-Token": token},
            json={},
        )
    except Exception as error:
        return Check("Endpoint de demanda", False, f"no respondió: {type(error).__name__}")

    if r.status_code == 404:
        return Check(
            "Endpoint de demanda",
            False,
            "POST /api/demand no existe: el agente no podrá registrar la demanda "
            "no cubierta y Oportunidades se quedará vacía en silencio",
        )
    if r.status_code == 400:
        return Check(
            "Endpoint de demanda",
            True,
            "la ruta existe y rechaza un cuerpo sin `query`, que es lo correcto",
        )
    if r.status_code in (401, 403):
        return Check("Endpoint de demanda", False, "la ruta existe pero el token no vale")
    if _json(r) is None:
        return _no_es_json("Endpoint de demanda", r)
    return Check(
        "Endpoint de demanda",
        False,
        f"respondió {r.status_code}; se esperaba 400 ante un cuerpo vacío",
    )


# PHP escupe el fatal en el cuerpo con status 200 si ya mandó las cabeceras, así
# que el código de estado por sí solo daría la página por buena.
_FATAL = ("Fatal error", "Uncaught", "Parse error")


def _check_page(client: httpx.Client, base: str, page: str) -> Check:
    """Basta con que la página exista y no reviente: el login es cosa aparte."""
    try:
        r = client.get(f"{base}/{page}", follow_redirects=False)
    except Exception as error:
        return Check(f"Página {page}", False, f"no respondió: {type(error).__name__}")

    if r.status_code == 404:
        return Check(f"Página {page}", False, "no está subida (falta el paso 3)")
    if r.status_code >= 500:
        return Check(
            f"Página {page}",
            False,
            f"error {r.status_code} del servidor: mira el log de PHP del hosting",
        )

    cuerpo = r.text or ""
    if any(marca in cuerpo for marca in _FATAL):
        pista = " ".join(cuerpo.split())[:160]
        return Check(
            f"Página {page}",
            False,
            f"devolvió {r.status_code} pero el cuerpo trae un error de PHP: {pista!r}",
        )

    # 302 al login es el caso normal desde fuera de una sesión.
    return Check(f"Página {page}", True, f"responde {r.status_code}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("CRM_BASE_URL", ""))
    parser.add_argument("--token", default=os.getenv("CRM_INTERNAL_TOKEN", ""))
    args = parser.parse_args()

    base = args.base_url.strip().rstrip("/")
    token = args.token.strip()
    if not base:
        print(
            "[deploy-check] falta --base-url (o CRM_BASE_URL), "
            "p. ej. https://donregalo.pe/crm/public",
            file=sys.stderr,
        )
        return 2
    if not token:
        print(
            "[deploy-check] falta --token (o CRM_INTERNAL_TOKEN)",
            file=sys.stderr,
        )
        return 2

    print(f"[deploy-check] {base}")
    with httpx.Client(timeout=TIMEOUT) as client:
        checks = [
            _check_health(client, base),
            _check_schema(client, base, token),
            _check_demand_endpoint(client, base, token),
            _check_page(client, base, "campaigns.php"),
            _check_page(client, base, "opportunities.php"),
        ]

    for check in checks:
        print(check.render())

    fallidos = [c for c in checks if not c.ok]
    if fallidos:
        print(
            f"\n[deploy-check] BLOQUEADO: {len(fallidos)} de {len(checks)} comprobaciones "
            "fallaron. El despliegue está a medias.",
            file=sys.stderr,
        )
        return 1

    print("\n[deploy-check] despliegue completo.")
    print(
        "  Recuerda que la demanda no cubierta empieza a contar AHORA: no hay "
        "nada anterior que recuperar."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
