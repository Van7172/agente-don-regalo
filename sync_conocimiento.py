"""Revisión explícita del conocimiento del equipo desde el CRM activo.

Uso en el servidor del agente:
  python sync_conocimiento.py proponer 123 --salida propuestas.json
  # Revisar y editar el JSON; quitar respuestas no vigentes.
  python sync_conocimiento.py aprobar propuestas.json --revisor "Nombre del responsable"

Las propuestas no son visibles para el bot. La aprobación indexa los items
revisados con vencimiento a 90 días; se deben revalidar al caducar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.services import knowledge


async def main() -> None:
    parser = argparse.ArgumentParser(description="Curar conocimiento del equipo")
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser("proponer")
    propose.add_argument("conversation_id", type=int)
    propose.add_argument("--salida", required=True, type=Path)
    approve = commands.add_parser("aprobar")
    approve.add_argument("archivo", type=Path)
    approve.add_argument("--revisor", required=True)
    args = parser.parse_args()

    if args.command == "proponer":
        items = await knowledge.proponer_de_conversacion(args.conversation_id)
        payload = {"conversation_id": args.conversation_id, "items": items}
        args.salida.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(items)} propuestas guardadas en {args.salida}. Revisar antes de aprobar.")
        return

    payload = json.loads(args.archivo.read_text(encoding="utf-8"))
    conversation_id = int(payload["conversation_id"])
    items = payload["items"]
    if not isinstance(items, list):
        raise ValueError("El archivo debe contener una lista de items")
    count = await knowledge.aprobar_items(items, conversation_id, args.revisor)
    print(f"{count} respuestas aprobadas; caducan en 90 días.")


if __name__ == "__main__":
    asyncio.run(main())
