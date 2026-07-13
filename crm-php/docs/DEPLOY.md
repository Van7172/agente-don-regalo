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

Sube el contenido de `crm-php/` (FTP/Git). En el servidor:

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

`crm/sql/002_crm_schema_produccion.sql` en phpMyAdmin (BD del cliente).

Si el asesor no puede adjuntar PDF/audio (`Data truncated for column 'type_outbox'`):

```sql
-- crm-php/sql/003_media_outbox.sql
ALTER TABLE crm_outbox
  MODIFY COLUMN type_outbox ENUM('text','image','audio','document')
  NOT NULL DEFAULT 'text';
```

## 4. Apache

`public/.htaccess` requiere `mod_rewrite`. En Nginx, reescribe `/api/*` a `api/index.php`.

## 5. Sandbox (tu VPS)

En EasyPanel → `app-agente-sandbox` → Entorno:

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=...mismo-token...
AGENT_INTERNAL_TOKEN=...mismo-que-crm-php...
```

Redeploy del sandbox.

## 6. Verificar

1. `https://donregalo.pe/crm/public/api/health` → `{"ok":true,...}`
2. Login en `https://donregalo.pe/crm/public/login.php`
3. WhatsApp → mensaje aparece en inbox (polling ~4s)
4. Bot pide ayuda / modo HUMAN → fila con estilo “AYUDA”
5. Asesor envía → outbox → WhatsApp (vía agente)

## Seguridad

- No abras MySQL remoto al VPS.
- Solo el header `X-CRM-Token` protege la API del agente.
- HTTPS obligatorio en el subdominio.
