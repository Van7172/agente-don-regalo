# Sandbox → CRM PHP

En producción Don Regalo el CRM es **PHP en el subdominio del cliente** (`crm-php/`), no Next.js.

```env
CRM_MODE=external
CRM_BASE_URL=https://crm.donregalo.pe
CRM_INTERNAL_TOKEN=mismo-token-que-crm-php-config
```

Deploy del panel: [`../../crm-php/docs/DEPLOY.md`](../../crm-php/docs/DEPLOY.md)

Health: `GET {CRM_BASE_URL}/api/health`
