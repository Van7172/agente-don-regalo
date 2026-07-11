# Práctica sandbox → promoción → producción

Este documento define cómo desarrollamos el rework (WhatsApp Cloud API + CRM propio)
**sin romper** el stack de producción actual (Chatwoot + Evolution).

## Por qué existe `sandbox/`

| Zona | Rol |
|---|---|
| Raíz del repo (`app/`, `Dockerfile`, …) | **Producción / legacy**. Sigue desplegable en EasyPanel. |
| `sandbox/` | **Rework**. Código nuevo independiente hasta que se apruebe la mudanza. |

Reglas:

1. Todo el rework vive en `sandbox/` hasta revisión explícita.
2. No mezclar imports entre `sandbox/` y `app/` legacy (dos árboles independientes).
3. Hotfixes urgentes de producción van en la raíz; features del rework van en `sandbox/`.
4. Secretos: `.env` (prod/legacy) y `sandbox/.env` (pruebas). Ambos están en `.gitignore`.
5. El **código** de `sandbox/` **sí se versiona** en Git para no perder el trabajo.

## Estructura esperada

```text
agente-don-regalo/
├── app/                      ← legacy (NO tocar salvo hotfix)
├── docs/
│   └── REWORK_SANDBOX.md     ← este archivo
└── sandbox/                  ← zona de desarrollo del rework
    ├── README.md
    ├── app/
    ├── web/
    ├── docs/
    ├── tests/
    └── scripts/
```

## Flujo diario de desarrollo

1. Trabajar solo bajo `sandbox/`.
2. Correr el sandbox en un puerto distinto al de producción (ej. `8100`).
3. Probar con webhook de Meta apuntando a un túnel (ngrok / Cloudflare Tunnel) → `sandbox`.
4. Commits normales a `main` (o rama de feature) incluyendo `sandbox/`.

```bash
cd sandbox
copy .env.example .env   # configurar credenciales de prueba
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8100
```

## Criterios para promover a producción

Antes de mudarse a la raíz:

- [ ] Checklist de [`sandbox/docs/MIGRATION_CHECKLIST.md`](../sandbox/docs/MIGRATION_CHECKLIST.md) en verde
- [ ] Tests en `sandbox/tests/` pasando
- [ ] Webhook Meta de staging verificado (texto, imagen, cita, handoff)
- [ ] Revisión explícita del responsable del proyecto

## Promoción (mudanza a raíz)

Procedimiento resumido (detalle en `sandbox/docs/MIGRATION_CHECKLIST.md`):

1. Tag de seguridad del legacy: `git tag legacy-chatwoot-evolution`
2. Ejecutar `sandbox/scripts/promote.ps1` (o seguir el checklist manual)
3. Actualizar variables en EasyPanel (quitar Evolution/Chatwoot; añadir Meta + `DATABASE_URL`)
4. Apuntar el webhook de Meta al dominio de producción
5. `git push` + redeploy
6. Monitorear 24–48 h
7. Archivar o eliminar restos legacy / `sandbox/` residual

## Qué NO hacer

- No desplegar `sandbox/` a producción “a medias” sin checklist.
- No compartir el mismo `.env` entre legacy y sandbox.
- No borrar Chatwoot/Evolution del entorno de prod hasta confirmar el corte.
