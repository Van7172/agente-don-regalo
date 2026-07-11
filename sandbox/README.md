# Sandbox — Rework Agente + CRM + WhatsApp Cloud API

Zona de desarrollo del rework. El stack legacy (Chatwoot + Evolution) sigue en la raíz
del repo y **no** se importa desde aquí.

Documentación:

- Práctica general: [`../docs/REWORK_SANDBOX.md`](../docs/REWORK_SANDBOX.md)
- Arquitectura: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Checklist de mudanza: [`docs/MIGRATION_CHECKLIST.md`](docs/MIGRATION_CHECKLIST.md)

## Qué incluye

- **WhatsApp Cloud API** (Meta): webhook inbound + envío de texto/imagen
- **CRM propio**: conversaciones, contactos, mensajes, labels, handoff humano
- **Agente**: buffer, tools, Qdrant, prompt (portado del legacy)
- **Panel web mínimo**: bandeja para asesores (`web/`)

## Arranque

```bash
cd sandbox
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8100
```

| URL | Uso |
|---|---|
| `http://localhost:8100/health` | Healthcheck |
| `http://localhost:8100/whatsapp/webhook` | Webhook Meta |
| `http://localhost:8100/crm/...` | API CRM |
| `http://localhost:8100/` | Panel inbox |

## Tests

```bash
cd sandbox
python -m pytest tests/ -q
```

## Promoción

Cuando esté aprobado: seguir el checklist y ejecutar `scripts/promote.ps1`.
