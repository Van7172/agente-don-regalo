# Checklist de migración: sandbox → raíz / producción

Usar este checklist **antes** de reemplazar el código legacy en la raíz del repo.

## A. Aceptación funcional (staging)

- [ ] `GET /health` responde ok
- [ ] Verificación del webhook Meta (challenge GET) ok
- [ ] Mensaje de texto entrante → respuesta del agente
- [ ] Imagen de producto enviada por Cloud API visible en el celular
- [ ] Mensaje citado (reply) inyecta contexto al agente
- [ ] Label `bot_active` off → bot ignora
- [ ] Handoff → estado `human_support` + mensaje de espera + alerta opcional
- [ ] Asesor responde desde panel CRM y el cliente lo recibe en WhatsApp
- [ ] Búsqueda semántica Qdrant funciona
- [ ] Tools de catálogo (donregalo.pe) funcionan
- [ ] Tests: `python -m pytest tests/ -q` en verde

## B. Preparación Git

- [ ] Working tree limpio o cambios commiteados
- [ ] Tag de seguridad: `git tag legacy-chatwoot-evolution && git push origin legacy-chatwoot-evolution`
- [ ] Backup del `.env` de producción (fuera del repo)

## C. Promoción de código

Opción recomendada (script):

```powershell
cd sandbox
.\scripts\promote.ps1
```

Manual:

1. Mover/archivar legacy: `app/` → `legacy_app_backup/` (o borrar tras tag).
2. Copiar `sandbox/app` → `app/`.
3. Copiar `sandbox/web` → `web/`.
4. Reemplazar `requirements.txt`, `Dockerfile`, `.env.example` por los de sandbox.
5. Actualizar `README.md` raíz.
6. Eliminar o vaciar `sandbox/` residual si ya no hace falta.

## D. EasyPanel / entorno

Quitar:

- `CHATWOOT_*`
- `EVOLUTION_*`

Añadir / verificar:

- `WHATSAPP_TOKEN`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_VERIFY_TOKEN`
- `WHATSAPP_APP_SECRET`
- `DATABASE_URL` (Postgres en prod)
- `OPENAI_*`, `QDRANT_*`, `DONREGALO_API_BASE`
- `BOT_ACTIVE_LABEL` / estados CRM equivalentes
- `ALERT_WEBHOOK_URL` (opcional)

Comando de arranque:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}
```

## E. Meta / DNS

- [ ] Webhook de producción apunta a `https://tu-dominio/whatsapp/webhook`
- [ ] Campos suscritos: `messages`
- [ ] Número Cloud API en modo live (si aplica)
- [ ] Ventana de 24 h / plantillas outbound entendidas (el bot responde en sesión)

## F. Corte y monitoreo

- [ ] Push a GitHub + deploy
- [ ] Probar un hilo real de punta a punta
- [ ] Monitorear logs 24–48 h (`[WA]`, `[CRM]`, `[TOOL]`, `[HANDOFF]`)
- [ ] Desactivar webhooks legacy Chatwoot/Evolution
- [ ] Comunicar al equipo el nuevo panel CRM

## Rollback

Si falla en las primeras horas:

1. Redeploy del tag `legacy-chatwoot-evolution`
2. Restaurar env Chatwoot/Evolution
3. Reactivar webhooks legacy
4. Investigar en sandbox sin tocar prod
