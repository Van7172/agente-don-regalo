# =============================================================================
# CRM en EasyPanel (VPS) + MySQL del cliente (donregalo.pe)
# =============================================================================

## Arquitectura

```
WhatsApp → sandbox (agente) → CRM_BASE_URL → crm.donregalo.pe (EasyPanel)
                                              ↓
                                    MySQL hosting cliente (remoto)
```

- **CRM (Next.js):** servicio nuevo en EasyPanel, dominio `crm.donregalo.pe` (DNS A/CNAME al VPS).
- **MySQL:** la BD de producción Don Regalo (donde corriste `002_crm_schema_produccion.sql`).
- **Agente sandbox:** `CRM_MODE=external` + `CRM_BASE_URL=https://crm.donregalo.pe`.

## 1. MySQL remoto (imprescindible)

El hosting del cliente debe permitir conexiones MySQL **desde la IP pública de tu VPS**.

En cPanel del cliente suele ser:
1. **Remote MySQL** / “Hosts de acceso remoto” → agregar IP del VPS.
2. Usuario MySQL con permisos sobre la BD (SELECT/INSERT/UPDATE/DELETE en tablas `crm_*` y SELECT en `usuarios`/`roles`).
3. Puerto: casi siempre **3306** (no 3307).

Prueba desde el VPS (o tu PC con la IP permitida):

```bash
mysql -h HOST_MYSQL_CLIENTE -P 3306 -u USUARIO -p NOMBRE_BD -e "SHOW TABLES LIKE 'crm_%';"
```

Si “Access denied” / timeout: el CRM en EasyPanel no podrá conectar aunque el código esté bien.

## 2. Servicio EasyPanel — CRM

1. Proyecto `don_regalo_rags` → **+ Service** → App.
2. Nombre: `app-crm` (o similar).
3. Source: mismo repo Git.
4. **Build path / Context:** `/crm`
5. Dockerfile: `crm/Dockerfile`
6. Puerto interno: **3000** (el contenedor escucha 3000).
7. Dominio: `crm.donregalo.pe` (o el subdominio que apunte al VPS).

### Variables de entorno (CRM)

```env
MYSQL_HOST=host.del.cliente.o.ip
MYSQL_PORT=3306
MYSQL_USER=usuario_mysql
MYSQL_PASSWORD=***
MYSQL_DATABASE=donregalo_bd

CRM_TENANT_SLUG=don-regalo
CRM_INTERNAL_TOKEN=cambia-este-token-compartido-con-el-agente
SESSION_SECRET=cambia-este-secreto-jwt-largo

# URL pública del sandbox (outbox asesor → WhatsApp)
AGENT_BASE_URL=https://don-regalo-rags-app-agente-sandbox.XXXX.easypanel.host
AGENT_INTERNAL_TOKEN=cambia-este-token-agente
```

## 3. DNS

En el DNS de donregalo.pe:

```text
crm.donregalo.pe  →  CNAME o A  →  IP / dominio del VPS EasyPanel
```

SSL: EasyPanel suele emitir el certificado al asignar el dominio.

## 4. Cablear el agente (sandbox)

En `app-agente-sandbox` → Entorno, cambia/añade:

```env
CRM_MODE=external
CRM_BASE_URL=https://crm.donregalo.pe
CRM_INTERNAL_TOKEN=el-mismo-que-en-el-crm
AGENT_INTERNAL_TOKEN=el-mismo-que-en-el-crm
```

Quita la dependencia de SQLite local para conversaciones (el inbox HTML del sandbox dejará de ser la fuente de verdad; usa `https://crm.donregalo.pe/login`).

Redeploy sandbox + CRM.

## 5. Verificar

1. `https://crm.donregalo.pe/api/health`
2. Login con un usuario de la tabla `usuarios` del cliente.
3. WhatsApp “Hola” → conversación visible en el CRM.
4. Logs sandbox: sin errores HTTP al pegarle a `CRM_BASE_URL`.

## Notas

- Chatwoot/Evolution en el mismo proyecto EasyPanel pueden seguir; el CRM nuevo **no** los usa.
- No subas `crm/` al cPanel PHP del cliente; solo DNS + MySQL remoto + este servicio en el VPS.
