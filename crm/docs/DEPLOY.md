# Deploy CRM PHP en el hosting del cliente

Documentación de producto/arquitectura completa: [`../../docs/SANDBOX_Y_CRM_PHP.md`](../../docs/SANDBOX_Y_CRM_PHP.md).

## Arquitectura

- **Panel + MySQL:** carpeta `public_html/crm` (o subdominio) en hosting del cliente.
- **Agente:** EasyPanel VPS → `CRM_BASE_URL=https://donregalo.pe/crm/public`

## 1. Publicación (elige una)

### Opción A — Carpeta (lo que tienes ahora)

Archivos en `public_html/crm/` → URL del panel:

```text
https://donregalo.pe/crm/public/
https://donregalo.pe/crm/public/api/health
https://donregalo.pe/crm/public/login.php
```

En `config.php`:

```php
'base_path' => '/crm/public',
```

En EasyPanel (sandbox):

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
```

### Opción B — Subdominio (opcional después)

`crm.donregalo.pe` → document root = `public_html/crm/public`  
`base_path` => `''`  
`CRM_BASE_URL=https://crm.donregalo.pe`

## 2. Archivos

Sube el contenido de `crm/` (FTP/Git). En el servidor:

```bash
cp config.example.php config.php
# editar config.php
```

Variables clave en `config.php`:

| Clave | Valor |
|-------|--------|
| `db.*` | MySQL local (`donregal_donregalo2019`, user/pass del hosting) |
| `crm_internal_token` | Igual que `CRM_INTERNAL_TOKEN` del sandbox |
| `agent_base_url` | URL pública del sandbox en EasyPanel |
| `agent_internal_token` | Igual que `AGENT_INTERNAL_TOKEN` del sandbox |
| `tenant_slug` | `don-regalo` |

## 3. Schema

Si aún no corriste el SQL de producción:

`crm/sql/001_crm_schema.sql` (y migraciones en `crm/sql/`) en phpMyAdmin (BD del cliente).

Si el asesor no puede adjuntar PDF/audio (`Data truncated for column 'type_outbox'`):

```sql
-- crm/sql/003_media_outbox.sql
ALTER TABLE crm_outbox
  MODIFY COLUMN type_outbox ENUM('text','image','audio','document')
  NOT NULL DEFAULT 'text';
```

Antes de publicar **Historial de ventas**, ejecutar en phpMyAdmin:

```text
crm/sql/004_sales_history.sql
```

La migración crea `crm_ventas_historiales` y copia como pendientes las fichas
`sale_*` que ya estén activas. Es repetible: usa `CREATE TABLE IF NOT EXISTS` e
`INSERT IGNORE`.

### Módulos del asesor (asignación, notas, seguimientos, venta manual)

Ejecutar **en este orden y antes de subir el PHP** — el panel consulta columnas
y tablas que estas migraciones crean, así que al revés el inbox devuelve 500:

```text
crm/sql/009_asignacion_asesor.sql   → quién tiene cada conversación
crm/sql/010_notas_internas.sql      → crm_conversation_notes
crm/sql/011_seguimientos.sql        → crm_seguimientos
crm/sql/012_venta_manual.sql        → origen/monto/asesor en el historial
```

Los `ALTER TABLE` (009 y 012) **no** son repetibles: si se corren dos veces dan
`Duplicate column name`. Eso es inofensivo, pero conviene saberlo antes de verlo
en phpMyAdmin.

Después hay que subir, además de los archivos de siempre:
`views/inbox.php`, `views/sales-history.php`, `public/sales-history.php`,
`public/assets/inbox.js`, `public/assets/app.css`, `src/Repository.php` y
`public/api/index.php`.

**No requiere tocar el agente.** La ventana de 24 h se calcula en el CRM desde
`crm_messages`, y ningún módulo nuevo llama al agente.

### Escritura condicional del estado (`POST /api/settings/cas`)

**Sin migración SQL.** Basta subir `src/Repository.php` y `public/api/index.php`.

El estado del harness (`harness_state_{id}`) lo escriben tres caminos del agente
—el turno del cliente, el releaser y el handoff— y el lock de Redis solo
serializa los turnos entrantes: sin esto, el releaser guardaba una foto vieja del
documento y borraba lo que el turno había avanzado. `casSetting` hace la
escritura condicional por versión con la fila bloqueada, que es el único sitio
donde puede ser atómica.

**El CRM va primero, pero el orden no rompe nada**: el agente detecta un 404 en
esa ruta, lo recuerda para no repetirlo y sigue guardando a pelo (mismo criterio
que el claim del outbox). Hasta que subas el CRM, el estado se guarda igual —
solo que sin cerrar la carrera.

## 4. Apache

`public/.htaccess` requiere `mod_rewrite`. En Nginx, reescribe `/api/*` a `api/index.php`.

## 5. Sandbox (tu VPS)

En EasyPanel → `app-agente-sandbox` → Entorno:

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=...mismo-token...
AGENT_INTERNAL_TOKEN=...mismo-que-crm/config.php...
```

Redeploy del sandbox.

## 6. Verificar

1. `https://donregalo.pe/crm/public/api/health` → `{"ok":true,...}`
2. Login en `https://donregalo.pe/crm/public/login.php`
3. WhatsApp → mensaje aparece en inbox (polling ~4s)
4. Bot pide ayuda / modo HUMAN → fila con estilo “AYUDA”
5. Asesor envía → outbox → WhatsApp (vía agente)

## 7. Panel operacional

1. Abrir `operations.php` y confirmar que el agente aparece **En línea**.
2. Verificar cola, DLQ, circuitos, latencias, handoffs y outbox.

No requiere una migración SQL adicional. Debes subir:

- `public/operations.php`
- `public/assets/operations.js`
- `views/operations.php`
- `src/OperationsClient.php`
- las versiones actualizadas de `bootstrap.php`, `src/Repository.php`,
  `views/layout.php`, `public/api/index.php` y `public/assets/app.css`.

El CRM consulta `GET /internal/operations` del agente usando
`agent_internal_token`; ese secreto nunca se entrega al navegador.

## Seguridad

- No abras MySQL remoto al VPS.
- Solo el header `X-CRM-Token` protege la API del agente.
- HTTPS obligatorio en el subdominio.
