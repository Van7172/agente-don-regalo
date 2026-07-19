# Agente Don Regalo

Agente de WhatsApp con **Meta Cloud API**, CRM PHP en el hosting del cliente,
OpenAI, Qdrant y catálogo real de Don Regalo.

> **Estado (julio 2026):** el rework de `sandbox/` fue **promovido a la raíz**.
> Tag de rollback legacy (Chatwoot/Evolution): `legacy-chatwoot-evolution`.
> Arquitectura completa: [`docs/SANDBOX_Y_CRM_PHP.md`](docs/SANDBOX_Y_CRM_PHP.md).
> Checklist post-corte: [`docs/MIGRATION_CHECKLIST.md`](docs/MIGRATION_CHECKLIST.md).

## Piezas

| Pieza | Ubicación | Rol |
|-------|-----------|-----|
| Agente (esta raíz) | `app/` | Webhook Meta, LLM, tools, envío WhatsApp |
| Panel asesores | `crm/` | Inbox + reportes en hosting PHP / MySQL local |
| Panel mínimo local | `web/` | Solo si `CRM_MODE=local` |
| Copia histórica rework | `sandbox/` | Referencia; la fuente de verdad del agente es la raíz |

## Requisitos

- Python 3.11+ (imagen Docker: 3.12 + ffmpeg para audio del asesor)
- WhatsApp Cloud API (Meta)
- OpenAI + Qdrant
- CRM PHP desplegado (`CRM_MODE=external`) o SQLite local

## Instalación local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edita `.env` (tokens Meta, OpenAI, Qdrant, `CRM_*`).

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Docker / EasyPanel:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}
```

## Endpoints

| Endpoint | Uso |
|----------|-----|
| `GET /health` | Healthcheck |
| `GET/POST /whatsapp/webhook` | Webhook Meta (verify + mensajes) |
| `GET /` | Panel web mínimo (si existe `web/`) |
| `/crm/*` | API CRM local o proxy a CRM PHP |
| `/internal/*` | Outbox / tokens internos agente↔CRM |

## Flujo

1. Cliente escribe en WhatsApp → Meta → webhook del agente.
2. Buffer agrupa mensajes; se persiste en CRM (`external` → PHP).
3. OpenAI + tools (catálogo, Qdrant, memoria, handoff).
4. Respuesta por Cloud API (texto / imágenes de producto).
5. Asesor toma el chat en `crm/` → outbox → agente → WhatsApp.

## Activación del bot

Gates en conversación CRM: `bot_active` / `human_support` (equivalentes a labels legacy).

## Escalación humana

Tool `escalar_a_humano` ante pedido de persona, frustración, pagos/comprobantes,
descuentos o acciones no verificables. El bot **no** promete confirmar comprobantes
en otros canales.

## Variables clave (producción)

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=...
AGENT_INTERNAL_TOKEN=...
WHATSAPP_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_VERIFY_TOKEN=...
OPENAI_API_KEY=...
QDRANT_URL=...
DONREGALO_API_BASE=https://donregalo.pe/clienteApiApp/api
WATCHDOG_ENABLED=0
```

Detalle: `.env.example` y [`docs/SANDBOX_Y_CRM_PHP.md`](docs/SANDBOX_Y_CRM_PHP.md).

## Tests

```bash
python -m pytest tests/ -q
```

## Rollback al legacy Chatwoot/Evolution

```bash
git checkout legacy-chatwoot-evolution
# restaurar env CHATWOOT_* / EVOLUTION_* y webhooks legacy
```

## CRM PHP

Panel de producción: [`crm/`](crm/). Deploy: [`crm/docs/DEPLOY.md`](crm/docs/DEPLOY.md).
