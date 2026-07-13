# Arquitectura del rework (sandbox)

## Visión

Producción Don Regalo (CRM externo PHP): ver
[`docs/SANDBOX_Y_CRM_PHP.md`](../../docs/SANDBOX_Y_CRM_PHP.md).

```text
Cliente WhatsApp
       │
       ▼
 Meta Cloud API  ◄──────── envío texto/media
       │
       ▼ webhook
  sandbox/app (FastAPI)
       │
       ├─► channels/whatsapp   (parse + send Graph API)
       ├─► crm                 (local SQLite O CRM_MODE=external → crm-php)
       ├─► agent/services      (buffer, LLM loop, tools, fillers)
       ├─► Qdrant              (productos + conocimiento)
       └─► API donregalo.pe    (catálogo real)
       │
       ▼
  Panel CRM PHP (asesor humano / handoff)  ← producción
  (crm/ Next.js = legado)
```

## Capas

| Capa | Paquete | Responsabilidad |
|---|---|---|
| Canal | `app.channels.whatsapp` | Verify webhook, inbound Meta, send text/image, citas `context` |
| CRM | `app.crm` | Persistencia, labels `bot_active` / `human_support`, API inbox |
| Agente | `app.services` + `app.tools` + `app.prompts` | Buffer, LLM, tools, Qdrant |
| Panel | `web/` | Bandeja mínima para asesores |

## Multi-tenant (diseño, single-tenant en v1)

Todas las tablas CRM llevan `tenant_id`. Don Regalo es el tenant por defecto
(`DEFAULT_TENANT_SLUG=don-regalo`). Planes de precio / facturación = fase 2.

## Gates del bot

1. Si la conversación tiene label/estado `human_support` → el bot no responde.
2. Si no tiene `bot_active` → el bot no responde (opt-in).
3. Tras handoff, el asesor escribe desde el CRM; el CRM envía por Cloud API.

## Diferencias vs legacy

| Legacy | Sandbox |
|---|---|
| Chatwoot webhook | Meta webhook |
| Evolution sendMedia | Graph API `/messages` |
| Labels Chatwoot | Labels CRM |
| Historial Chatwoot | Tabla `messages` |
| Contact attrs Chatwoot | `contacts.attributes` JSON |

## Coexistencia Meta

No incluida en v1. Número Cloud API dedicado. Coexistencia (app Business + API
en el mismo número) queda como mejora futura.
