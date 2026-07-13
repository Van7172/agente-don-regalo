# Sandbox (agente IA) + CRM PHP — estado de producción

Documento de referencia de lo aplicado en el rework: agente en VPS (EasyPanel) + panel/API CRM en PHP en el hosting del cliente.

Última actualización relevante: commit `bbdb4ff` (julio 2026).

---

## 1. Visión general

```text
Cliente WhatsApp
       │
       ▼
 Meta Cloud API  ◄──── envío texto / imágenes (producto)
       │
       ▼ webhook (200 inmediato + proceso en background)
  sandbox/  (FastAPI en EasyPanel: app-agente-sandbox)
       │
       ├─ buffer de mensajes
       ├─ OpenAI + tools (catálogo, Qdrant, handoff, memoria)
       ├─ fillers de latencia (excepto saludos simples)
       └─ CRM_MODE=external ──HTTP──► crm-php (hosting cliente)
                                          │
                                          ├─ MySQL local (crm_* + usuarios)
                                          ├─ Inbox asesores (polling)
                                          ├─ Reportes / KPIs
                                          └─ Outbox → push al agente → WhatsApp
```


| Pieza                    | Dónde vive                                 | Rol                                        |
| ------------------------ | ------------------------------------------ | ------------------------------------------ |
| **Agente IA**            | `sandbox/` en EasyPanel                    | WhatsApp Cloud API, LLM, tools, Qdrant     |
| **CRM panel + API**      | `crm-php/` en hosting PHP del cliente      | Inbox, login, reportes, persistencia MySQL |
| **Catálogo**             | `DONREGALO_API_BASE` → `clienteApiApp/api` | Productos reales Don Regalo                |
| **CRM Next.js** (`crm/`) | Legado                                     | No es el panel de producción               |


URLs típicas actuales:


| Servicio                   | URL                                                          |
| -------------------------- | ------------------------------------------------------------ |
| Panel CRM                  | `https://donregalo.pe/crm/public/`                           |
| Health API CRM             | `https://donregalo.pe/crm/public/api/health`                 |
| Agente (ejemplo EasyPanel) | `https://don-regalo-rags-app-agente-sandbox.…easypanel.host` |


---



## 2. Por qué esta arquitectura (Opción C)

- El MySQL del cliente **no se abre a Internet** (sin Remote MySQL al VPS).
- El agente en el VPS habla con el CRM solo por **HTTP + token** (`X-CRM-Token` / Bearer).
- Los asesores usan el panel PHP contra **MySQL local** del hosting.
- El agente sigue enviando WhatsApp por Meta Cloud API; el CRM no necesita credenciales Meta.

---



## 3. Agente sandbox (`sandbox/`)



### 3.1 Capas


| Capa            | Ubicación               | Responsabilidad                               |
| --------------- | ----------------------- | --------------------------------------------- |
| Canal WhatsApp  | `app/channels/whatsapp` | Webhook Meta, envío Graph API, citas          |
| CRM             | `app/crm`               | Cliente HTTP externo o SQLite local           |
| Buffer / agente | `app/services`          | Agrupa mensajes, loop LLM, fillers, typing    |
| Tools           | `app/tools`             | Catálogo, Qdrant, memoria, `escalar_a_humano` |
| Prompt          | `app/prompts/system.py` | Comportamiento comercial y guardrails         |




### 3.2 Modo CRM externo (producción)

Variables en EasyPanel (`app-agente-sandbox`):

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=<mismo que crm-php config.php → crm_internal_token>
AGENT_INTERNAL_TOKEN=<mismo que crm-php → agent_internal_token>
WATCHDOG_ENABLED=0
```

- Con `CRM_MODE=external`, el sandbox **no** usa SQLite de tenants locales para el flujo WhatsApp; proxy/cliente HTTP hacia el PHP.
- Si los tokens no coinciden → `401 Unauthorized` en llamadas CRM.
- `WATCHDOG_ENABLED=0`: el vigía de conversaciones sin respuesta queda apagado (hoy solo avisos; no auto-mejora de prompts).

Código clave:

- `sandbox/app/config.py` — lectura de env
- `sandbox/app/crm/http_client.py` — cliente hacia CRM PHP
- `sandbox/app/services/buffer.py` — `_flush_external` / enqueue externo
- `sandbox/app/crm/api.py` — API local o proxy según modo



### 3.3 Flujo de un mensaje inbound

1. Meta POST al webhook del sandbox → respuesta **200 inmediata**; procesamiento en background.
2. Buffer agrupa mensajes cercanos (`BUFFER_SECONDS`).
3. Persistencia vía CRM PHP (conversación, mensaje inbound).
4. Si modo AI / bot activo y sin `human_support` → `run_agent`.
5. OpenAI decide tools; opcionalmente fillers; respuesta segmentada (texto + cards producto).
6. Outbound se guarda en CRM y se envía por Cloud API.



### 3.4 Fillers de latencia

En `sandbox/app/services/agent.py`:


| Tipo                  | Cuándo                                                      | Ejemplo                      |
| --------------------- | ----------------------------------------------------------- | ---------------------------- |
| **Early filler**      | ~0,7 s si el 1.er round LLM tarda                           | “Un momento, ya te ayudo 😊” |
| **Tool filler**       | Al invocar tools de catálogo/búsqueda                       | “¡Genial! Déjame buscarte…”  |
| **Excepción saludos** | Último mensaje = saludo breve (`Hola`, `Buenos días`, etc.) | **No** se envía early filler |


Así se evita el doble mensaje “Un momento…” + saludo real en un “Hola” simple. Si el texto pide algo más (“Hola quiero flores”), el early filler sí puede aparecer.

### 3.5 Handoff y pagos / comprobantes

Cambios de prompt y tool (`system.py`, `definitions.py`):

- El bot **no puede** ver ni confirmar comprobantes en otro WhatsApp/email.
- **Prohibido** prometer “te confirmo cuando lo recibamos”.
- En pago / comprobante / descuento / cancelación / frustración → tool `escalar_a_humano`.
- Tras escalar, el sistema manda el mensaje de espera y marca la conversación para el equipo (`HUMAN` / `human_support`).



### 3.6 Otras mejoras del sandbox (rework)

- Webhook Meta: ack rápido + trabajo async.
- Cards de producto: WebP → JPEG para WhatsApp.
- Early filler + menos round-trips de catálogo en turns con tools.
- Diagnósticos de envío / health orientados a Meta Cloud API.
- Coexistencia Chatwoot/Evolution: **fuera** del path sandbox de producción actual.



### 3.7 Deploy agente

Tras cambios en `sandbox/`: **redeploy** del servicio en EasyPanel. El CRM PHP no hace falta tocarlo salvo que también haya cambios PHP.

Guías cortas:

- `[sandbox/docs/CRM_PHP.md](../sandbox/docs/CRM_PHP.md)`
- `[sandbox/docs/ARCHITECTURE.md](../sandbox/docs/ARCHITECTURE.md)`
- `[sandbox/docs/E2E_META.md](../sandbox/docs/E2E_Meta.md)` (si existe checklist E2E)

---



## 4. CRM PHP (`crm-php/`)



### 4.1 Estructura

```text
crm-php/
  public/                 ← document root (o /crm/public en el hosting)
    index.php             inbox
    login.php / logout.php
    reports.php
    api/index.php         API agente + JSON del panel
    assets/               app.css, inbox.js, logo
  src/                    Database, Auth, Repository, Http, helpers
  views/                  layout, login, inbox, reports
  config.example.php      → copiar a config.php (no versionar secretos)
  docs/DEPLOY.md
```



### 4.2 Configuración (`config.php`)


| Clave                  | Uso                                                               |
| ---------------------- | ----------------------------------------------------------------- |
| `db.*`                 | MySQL local del hosting (`donregal_donregalo2019`, etc.)          |
| `base_path`            | `'/crm/public'` si la URL es carpeta; `''` si docroot = `public/` |
| `crm_internal_token`   | Token agente → CRM (header)                                       |
| `agent_base_url`       | URL pública del sandbox EasyPanel                                 |
| `agent_internal_token` | Token CRM → agente (outbox push)                                  |
| `tenant_slug`          | `don-regalo`                                                      |
| `catalog_api_base`     | Opcional, corroborar reportes                                     |


Schema tablas `crm_*`: `crm/sql/002_crm_schema_produccion.sql`.

### 4.3 Autenticación

**Panel (asesores)**

- Login con `usuarios.login_usuario` + `usuarios.password_usuario` (comparación directa `hash_equals`, como en el sistema del cliente).
- Sesión PHP (`session_name` configurable).

**API agente**

- Header `X-CRM-Token` o `Authorization: Bearer …`.
- Lectura robusta de headers (proxies / CGI).
- Trim del token; aviso en boot si el valor es placeholder.



### 4.4 API interna (compatible con `http_client.py`)

Base: `{CRM_BASE_URL}/api/...`


| Método         | Ruta                       | Quién               | Descripción                             |
| -------------- | -------------------------- | ------------------- | --------------------------------------- |
| GET            | `/health`                  | Público / monitoreo | `{ ok, service: crm-php, … }`           |
| GET/POST       | `/conversations`           | Agente              | Listar / crear por `wa_id`              |
| GET            | `/conversations/{id}`      | Agente / panel      | Detalle + mensajes + **lead** (memoria) |
| POST           | `/conversations/{id}`      | Agente              | Append mensaje                          |
| PATCH          | `/conversations/{id}/mode` | Agente / panel      | AI ↔ HUMAN, flags bot/human_support     |
| GET/PUT        | `/memory/{wa_id}`          | Agente              | Memoria larga del contacto              |
| GET/POST       | `/leads`                   | Agente              | Lead por teléfono / upsert              |
| GET/PUT        | `/settings`                | Agente              | Pausas / settings key-value             |
| GET/POST/PATCH | `/outbox`                  | Panel → agente      | Encolar y empujar envío WhatsApp        |
| GET            | `/watchdog/unanswered`     | Agente              | Conversaciones sin respuesta            |
| GET            | `/reports/overview`        | Panel               | KPIs                                    |
| GET            | `/reports/conversations`   | Panel               | Listado con métricas                    |


Auth: token interno en casi todas las rutas (salvo health y, según diseño, algunas lecturas de panel con sesión).

### 4.5 Outbox (asesor → WhatsApp) — fix duplicados

Flujo correcto:

1. Panel POST `/api/outbox` → encola en MySQL.
2. CRM hace curl a `{agent_base_url}/internal/outbox/send`.
3. El **agente** envía por WhatsApp, marca outbox y **persiste** el mensaje outbound.

El PHP **ya no** inserta el mensaje otra vez tras OK del agente (evita burbujas duplicadas en el inbox). Commit `50c9cd0`.

### 4.6 Panel UI (asesores)

- **Login / layout** con marca Don Regalo (`logo-don-regalo.png` o fallback SVG).
- **Inbox**: lista de chats, polling ~4 s, highlight de `human_support` (“AYUDA”), toggle AI/HUMAN, envío de texto, panel resumen del lead (memoria).
- **Burbujas** más compactas (CSS/JS).
- **Reportes**: overview + listado conversaciones / KPIs de handoff y leads.
- Assets: `public/assets/app.css`, `inbox.js`.



### 4.7 Deploy CRM

1. Subir `crm-php/` al hosting (`public_html/crm/…`).
2. `config.example.php` → `config.php` con tokens alineados al sandbox.
3. Verificar `api/health` y login.
4. Detalle: `[crm-php/docs/DEPLOY.md](../crm-php/docs/DEPLOY.md)`.

Tras cambios solo de PHP: **subir archivos al hosting** (no hace falta redeploy EasyPanel).

---



## 5. Contratos agente ↔ CRM

```text
Agente                          CRM PHP
──────                          ───────
Inbound WA ──append msg──────►  crm_messages
Memory tools ──upsert────────►  crm_memory / leads
Handoff ──mode HUMAN─────────►  human_support = 1
Asesor escribe ──outbox POST─►  enqueue + push
                 ◄── send WA ── /internal/outbox/send
                 ──append out──►  (solo el agente)
```

Tokens deben ser **idénticos** en ambos lados (nunca el texto literal del placeholder de la doc).

---



## 6. Checklist operativo



### Sandbox (EasyPanel)

- [x] `CRM_MODE=external`
- [x] `CRM_BASE_URL=https://donregalo.pe/crm/public` (sin slash final problemático; el código hace `rstrip`)
- [x] Tokens CRM y agente alineados con `config.php`
- [x] `WATCHDOG_ENABLED=0` si se deja el vigía apagado
- [x] Webhook Meta apunta al sandbox
- [x] Redeploy tras cambios de código Python



### CRM PHP (hosting)

- [ ] `base_path` = `/crm/public` (modo carpeta) o `''` (subdominio)
- [ ] MySQL local + schema `crm_*`
- [ ] `agent_base_url` = URL pública del sandbox
- [ ] Login con usuario real de `usuarios`
- [ ] Health OK
- [ ] Subir PHP tras cambios de panel/API



### Prueba rápida E2E

1. WhatsApp: “Hola” → **una** respuesta de saludo (sin “Un momento…”).
2. Pedido de producto → filler de búsqueda + cards.
3. Mensaje aparece en inbox (~4 s).
4. Pedir asesor / pago → handoff + fila destacada.
5. Asesor responde desde CRM → **un** mensaje en WhatsApp e inbox (sin duplicado).

---



## 7. Historial de commits clave (rework CRM PHP)


| Commit    | Qué aportó                                       |
| --------- | ------------------------------------------------ |
| `53212e2` | CRM PHP + cableado sandbox `external`            |
| `c77efd1` | Auth token robusta + prompt handoff/comprobantes |
| `651c77f` | Burbujas inbox más compactas                     |
| `50c9cd0` | Fix mensajes duplicados en outbox                |
| `bbdb4ff` | UI/branding CRM + skip early filler en saludos   |


---



## 8. Qué no es producción


| Carpeta / servicio               | Estado                                   |
| -------------------------------- | ---------------------------------------- |
| Raíz `app/` + Chatwoot/Evolution | Legacy / otra línea                      |
| `crm/` Next.js                   | Legado; no desplegar como panel actual   |
| SQLite CRM del sandbox           | Solo si `CRM_MODE=local` (dev/tests)     |
| Watchdog como “auto-mejora”      | Futuro; hoy solo vigía/avisos y está off |


---



## 9. Dónde mirar en el código


| Tema                   | Archivo                                             |
| ---------------------- | --------------------------------------------------- |
| Early filler / saludos | `sandbox/app/services/agent.py`                     |
| Prompt + pagos         | `sandbox/app/prompts/system.py`                     |
| Flush CRM externo      | `sandbox/app/services/buffer.py`                    |
| Cliente HTTP CRM       | `sandbox/app/crm/http_client.py`                    |
| API CRM                | `crm-php/public/api/index.php`                      |
| Auth token / login     | `crm-php/src/Auth.php`                              |
| Queries                | `crm-php/src/Repository.php`                        |
| Inbox UI               | `crm-php/views/inbox.php`, `public/assets/inbox.js` |
| Deploy                 | `crm-php/docs/DEPLOY.md`                            |


