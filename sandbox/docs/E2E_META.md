# E2E Meta Cloud API — Opción C

## Modos

| Modo | Cuándo | Config |
|---|---|---|
| **Dry-run** (local) | Sin número Meta aún | `WHATSAPP_DRY_RUN=1` — simula envíos en logs |
| **Live** | Webhook público + credenciales Graph | `WHATSAPP_DRY_RUN=0` + token/phone id |

## 1. Dry-run local (ya soportado)

Terminal A — CRM:
```powershell
cd crm
npm run dev
```

Terminal B — Agente:
```powershell
cd sandbox
# .env con CRM_MODE=external y WHATSAPP_DRY_RUN=1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8100
```

Terminal C — script:
```powershell
cd sandbox
python scripts/e2e_meta_sim.py
```

Flujo validado: `POST /whatsapp/webhook` → buffer → LLM → CRM MySQL (`crm_messages`) → log `[WA-DRY]`.

## 2. Live Meta

1. En [Meta Developers](https://developers.facebook.com/) → tu App → WhatsApp → API Setup:
   - Temporary/permanent **Access Token** → `WHATSAPP_TOKEN`
   - **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
2. En sandbox `.env`:
   ```
   WHATSAPP_DRY_RUN=0
   WHATSAPP_TOKEN=...
   WHATSAPP_PHONE_NUMBER_ID=...
   WHATSAPP_VERIFY_TOKEN=donregalo-verify-e2e
   WHATSAPP_APP_SECRET=...   # opcional pero recomendado
   ```
3. Expón el agente (ngrok / EasyPanel / Cloudflare Tunnel):
   ```powershell
   ngrok http 8100
   ```
4. En Meta → WhatsApp → Configuration → Callback URL:
   - URL: `https://<tu-host>/whatsapp/webhook`
   - Verify token: el mismo `WHATSAPP_VERIFY_TOKEN`
5. Suscribe el campo `messages`.
6. Envía un WhatsApp real al número de prueba / producción.

## Checklist

- [ ] CRM `:3100` healthy
- [ ] Sandbox `:8100` `crm_mode=external`
- [ ] `e2e_meta_sim.py` → OK (dry-run)
- [ ] Credenciales Meta en `.env`
- [ ] `WHATSAPP_DRY_RUN=0`
- [ ] Webhook verificado en Meta
- [ ] Mensaje real aparece en CRM panel (`/login`)
