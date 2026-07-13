# Práctica sandbox → promoción → producción

## Estado actual

**Promoción realizada (julio 2026):** el código del agente vive en la **raíz**
(`app/`, `web/`, `Dockerfile`, `requirements.txt`). Tag de seguridad del legacy
Chatwoot/Evolution: `legacy-chatwoot-evolution`.

La carpeta `sandbox/` se conserva como referencia histórica / espejo; la fuente
de verdad del agente desplegable es la raíz.

Documentación operativa: [`SANDBOX_Y_CRM_PHP.md`](SANDBOX_Y_CRM_PHP.md)  
Checklist de corte: [`MIGRATION_CHECKLIST.md`](MIGRATION_CHECKLIST.md)

## Por qué existió `sandbox/`

| Zona | Rol histórico |
|---|---|
| Raíz (`app/` Chatwoot/Evolution) | Producción legacy hasta el corte |
| `sandbox/` | Rework Meta Cloud API + CRM hasta promoción |

## Tras la promoción

1. EasyPanel construye desde la **raíz** del repo (mismo `Dockerfile` promovido).
2. Env: Meta + `CRM_MODE=external` + tokens alineados con `crm-php`.
3. Webhook Meta → `https://tu-dominio-agente/whatsapp/webhook`.
4. Panel asesores → `crm-php` en el hosting del cliente (no el `web/` mínimo).
5. Monitorear 24–48 h; rollback = checkout del tag `legacy-chatwoot-evolution`.

## Desarrollo futuro

- Features del agente: editar `app/` en la raíz (ya no hace falta `sandbox/` salvo que quieras un entorno paralelo).
- Panel asesores: editar `crm-php/`.
- No reintroducir Chatwoot/Evolution salvo rollback explícito.
