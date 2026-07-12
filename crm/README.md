# CRM Don Regalo (`crm/`)

Panel + APIs HTTP sobre MySQL `donregalo_bd` (Opción C).

## Requisitos

- Node >= 20
- MySQL XAMPP con BD `donregalo_bd`
- En este entorno el puerto MySQL es **3307** (`C:\xampp\mysql\bin\my.ini`)

## Setup

```powershell
cd crm
copy .env.example .env.local   # si aún no existe
npm install
# Schema local:
# mysql -u root -h 127.0.0.1 -P 3307 donregalo_bd < sql/001_crm_schema.sql
npm run dev
```

Panel: http://127.0.0.1:3100  
Health: http://127.0.0.1:3100/api/health

## Deploy EasyPanel (VPS) + MySQL del cliente

Guía completa: [`docs/DEPLOY_EASYPANEL.md`](docs/DEPLOY_EASYPANEL.md)

Resumen: servicio App con build path `/crm`, dominio `crm.donregalo.pe`, env MySQL remoto del hosting del cliente, y sandbox con `CRM_MODE=external`.

## Schema MySQL

| Archivo | Uso |
|---|---|
| `sql/001_crm_schema.sql` | Desarrollo local (XAMPP) |
| `sql/002_crm_schema_produccion.sql` | **Producción** — mismo schema + verificación |

En producción (phpMyAdmin o CLI), ejecuta `002_crm_schema_produccion.sql` sobre la BD Don Regalo.
Crea solo tablas `crm_*`; el login sigue usando `usuarios` + `roles` existentes.

```powershell
# Ejemplo CLI (ajusta host/user/bd):
mysql -u root -p -h 127.0.0.1 donregalo_bd < sql/002_crm_schema_produccion.sql
```

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/health` | Healthcheck |
| GET/POST | `/api/conversations` | Listar / crear+inbound (agente) |
| GET/POST | `/api/conversations/:id` | Detalle + append mensaje |
| PATCH | `/api/conversations/:id/mode` | AI / HUMAN |
| GET/POST | `/api/leads` | Leads (reemplazo Airtable) |
| GET/PUT | `/api/memory/:phone` | Memoria largo plazo |
| GET/PUT | `/api/settings` | paused, wd_* |
| GET/POST/PATCH | `/api/outbox` | Envío asesor → agente |
| GET | `/api/watchdog/unanswered` | Mute detector |

Header interno agente↔CRM: `X-CRM-Token` = `CRM_INTERNAL_TOKEN`.

## Auth del panel

Login contra la tabla `usuarios` (+ `roles`) de `donregalo_bd`.

- Usuario: `login_usuario` **o** `email_usuario`
- Contraseña: comparación con `password_usuario` (mismo formato que el panel PHP actual)
- Sesión: cookie HTTP-only JWT (`SESSION_SECRET`), 7 días
- El agente sandbox sigue entrando con header `X-CRM-Token` (sin cookie)

Rutas:

| Método | Ruta | Uso |
|---|---|---|
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/me` | Usuario actual |

Panel: http://127.0.0.1:3100/login
