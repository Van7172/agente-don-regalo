# CRM PHP — Don Regalo (panel + API agente)

Inbox WhatsApp para asesores en el **servidor PHP del cliente** (subdominio).
MySQL local (`crm_*` + `usuarios`/`roles`). Sin Remote MySQL.

## Estructura

```
crm-php/
  public/           ← document root del subdominio
    index.php       inbox
    login.php
    reports.php
    api/index.php   API del agente (X-CRM-Token)
  src/              PDO, Auth, Repository
  views/
  config.example.php
```

## Setup rápido

1. Copia `config.example.php` → `config.php` y completa `db`, tokens.
2. Asegura que el schema `crm/sql/002_crm_schema_produccion.sql` ya está en la BD.
3. Apunta el subdominio (ej. `crm.donregalo.pe`) al folder **`public/`**.
4. Login con un usuario de la tabla `usuarios` del cliente.

Guía completa: [`docs/DEPLOY.md`](docs/DEPLOY.md)

## Agente (VPS)

```env
CRM_MODE=external
CRM_BASE_URL=https://crm.donregalo.pe
CRM_INTERNAL_TOKEN=mismo-que-config.php
```

Health: `GET https://crm.donregalo.pe/api/health`

## Nota sobre `crm/` (Next.js)

Esa carpeta es **legado**. El panel de producción es este CRM PHP.
