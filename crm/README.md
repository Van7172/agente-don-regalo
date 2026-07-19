# CRM PHP — Don Regalo (panel + API agente)

Inbox WhatsApp para asesores en el **servidor PHP del cliente**.
MySQL local (`crm_*` + `usuarios`/`roles`). Sin Remote MySQL.

## Documentación completa

Ver **[`../docs/SANDBOX_Y_CRM_PHP.md`](../docs/SANDBOX_Y_CRM_PHP.md)** — estado del agente sandbox + este CRM, flujos, API, fixes y checklist.

## Estructura

```
crm/
  public/           ← document root (o /crm/public)
    index.php       inbox
    login.php
    reports.php
    api/index.php   API del agente (X-CRM-Token)
  sql/              migraciones MySQL
  src/              PDO, Auth, Repository
  views/
  config.example.php
```

## Setup rápido

1. Copia `config.example.php` → `config.php` y completa `db`, tokens.
2. Asegura el schema `crm/sql/001_crm_schema.sql` (y migraciones posteriores) en la BD.
3. Publicación actual (carpeta): `https://donregalo.pe/crm/public/` con `base_path => '/crm/public'`.
4. Login con `login_usuario` de la tabla `usuarios`.

Guía de deploy: [`docs/DEPLOY.md`](docs/DEPLOY.md)

## Agente (VPS EasyPanel)

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=mismo-que-config.php
AGENT_INTERNAL_TOKEN=mismo-que-config.php
WATCHDOG_ENABLED=0
```

Health: `GET https://donregalo.pe/crm/public/api/health`
