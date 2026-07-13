# Sandbox → CRM PHP

En producción Don Regalo el CRM es **PHP en el hosting del cliente** (`crm-php/`), no Next.js.

## Documentación completa

Ver **[`docs/SANDBOX_Y_CRM_PHP.md`](../../docs/SANDBOX_Y_CRM_PHP.md)** (arquitectura, env, API, fillers, handoff, deploy, checklist).

## Env mínimo (EasyPanel)

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=mismo-token-que-crm-php-config
AGENT_INTERNAL_TOKEN=mismo-que-crm-php-agent
WATCHDOG_ENABLED=0
```

Deploy del panel: [`../../crm-php/docs/DEPLOY.md`](../../crm-php/docs/DEPLOY.md)

Health: `GET {CRM_BASE_URL}/api/health`
