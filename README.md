# Agente Don Regalo

Agente de WhatsApp para **Don Regalo** (donregalo.pe): delivery de regalos en Lima.

El cliente escribe por WhatsApp → Meta Cloud API → webhook → un **orquestador**
clasifica la intención y delega en un **especialista** → responde por Cloud API.
Los asesores humanos atienden desde un CRM PHP.

> **Fuente de verdad:** la **raíz** (`app/`, `tests/`, `evals/`).  
> `sandbox/` es solo un **espejo** sincronizado (CI falla si divergen).  
> Docs canónicos: [`ARQUITECTURA.md`](ARQUITECTURA.md) · [`CLAUDE.md`](CLAUDE.md) · [`API.md`](API.md)

Pila: FastAPI (Python ≥ 3.11; Docker 3.12), OpenAI, Qdrant, MySQL (CRM), SQLite (tests).

---

## Piezas

| Pieza | Ubicación | Rol |
|-------|-----------|-----|
| **Agente IA** | `app/` (EasyPanel) | Webhook Meta, harness, tools, Qdrant, envío WhatsApp |
| **CRM panel + API** | `crm/` (hosting PHP) | Inbox, login, reportes, persistencia MySQL |
| **Catálogo / pedidos** | `DONREGALO_API_BASE` | Productos, distritos, pedidos temporales |
| `sandbox/` | Espejo de la raíz | Se sincroniza; **no** es el deploy |
| `evals/` | Corpus de regresión | Routing, invariantes, handoff, adversarial |

---

## Arquitectura: Harness Engineering

Este proyecto no es “un prompt grande + un LLM”. Es un **harness**: el entorno
completo alrededor del modelo (orquestador, especialistas, tools, estado,
guardrails, evals y CI) para que el agente se comporte como un sistema de
entrega fiable.

Un turno:

```
percibir → clasificar → delegar → reducir → persistir
```

```
Webhook Meta → cola inbound → buffer
                 │
                 ▼
        ORQUESTADOR  (app/harness/master.py)
        escribe estado · NO habla con el cliente
                 │
     ┌───────────┼────────────┐
     ▼           ▼            ▼
  router     especialistas   guardrails
                 │
                 ▼
              tools → render → WhatsApp
```

### Especialistas

| Agente | Atiende | Determinista | Escala |
|--------|---------|--------------|--------|
| `concierge` | saludos, cortesía | no | no |
| `catalog` | búsqueda / campañas | no | **no** |
| `detail` | ficha de producto ya mostrado | no | no |
| `coverage` | distrito y tarifa | **sí** | no |
| `checkout` | cierre (FSM) | **sí** | sí |
| `policy` | pagos, horarios, objeciones | no | sí |
| `tracking` | estado de pedido | no | sí |
| `escalate` | derivación a asesor | no | sí |

Detalle: [`ARQUITECTURA.md`](ARQUITECTURA.md) · [`docs/HARNESS.md`](docs/HARNESS.md)

---

## Criterios y estándares (no negociables)

Nacieron de incidentes reales. Están en código, tests y evals — no solo en el prompt.

### 1. El orquestador no habla con el cliente
Clasifica y delega. Todo texto de cara al cliente sale de un especialista
(incluido el saludo: `concierge` / plantilla `WELCOME`).

### 2. `AgentResult`, nunca `str`
Los especialistas devuelven lo que dicen **y** lo que aprendieron (`artifacts`,
`state_patch`). Los ids de producto salen de las tools, nunca de una regex sobre
la prosa.

### 3. Prompts por capas
```
system = CORE + FACTS[agente] + PLAYBOOK[agente] + ESTADO
```
El CORE (identidad + **RESTRICCIONES**) va en todos los agentes de cara al cliente.
Prompt y toolset viven juntos en `AgentSpec` (`harness/registry.py`).

### 4. Determinista cuando el negocio lo exige
Cobertura, cierre (FSM), menú de taxonomía, listado de productos y saludo inicial
son **código**, no LLM. El modelo no formatea precios, URLs ni menús numerados.

### 5. Dinero y formas en el adapter
La API entrega tres formas de producto (+ distritos) en USD. Todo pasa por
`tools/adapters.py`. `tipo_cambio` **no** es tool del LLM.

### 6. Taxonomía real, no inventada
`explorar_catalogo` (`GET /catalogo/navegacion`) es la única puerta. Categoría
nombrada = límite duro. Menú lo arma el código (`taxonomy.render_menu`), máximo
dos niveles y luego fotos. **Mostrar > preguntar.**

### 7. Best-effort en el cierre
Al confirmar el resumen: pedido temporal (`POST /pedidos/temporales`) + venta
verde en CRM + handoff a humano para pagar. Si el panel falla, el handoff sigue;
el cliente listo para pagar no se bloquea.

### 8. Un fallo nuestro se deriva, no se narra
Marcadores internos, fuga de prompt o contacto de terceros → handoff. Precios
inventados o medios de pago inexistentes → se bloquean antes de salir al cliente.

### 9. Turno con dos intenciones = una sola voz
Si el cliente pide producto **y** cobertura/pago, lo comercial se queda el micrófono
y el dato logístico/político entra como hecho ya resuelto en el system.

### 10. Cada bug deja un caso en el corpus
`evals/corpus/*.yaml` + invariantes en `guardrails/` / `harness/invariants.py`.
El parche de hoy no puede romper el de la semana pasada.

Más reglas y anti-patrones: [`CLAUDE.md`](CLAUDE.md).

---

## Flujo de un mensaje

1. Meta POST al webhook → aceptación inmediata; cola inbound → worker.
2. Buffer agrupa la ráfaga de WhatsApp en un turno.
3. Persistencia en CRM (`CRM_MODE=external`).
4. Si bot activo y sin `human_support` → harness (`run_master`).
5. Router híbrido (reglas → LLM barato si hace falta) → especialista / FSM.
6. Guardrails de entrada/salida; render de productos en código.
7. Respuesta por Cloud API (texto + fotos).
8. Asesor responde desde `crm/` → outbox → agente → WhatsApp (con cita si aplica).

---

## Instalación local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # editar tokens Meta, OpenAI, Qdrant, CRM_*
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Python **≥ 3.11** (en 3.10 fallan tests por `asyncio.timeout`).

---

## Endpoints del agente

| Endpoint | Uso |
|----------|-----|
| `GET /health` | Healthcheck (+ breakers, firma webhook) |
| `GET/POST /whatsapp/webhook` | Webhook Meta |
| `GET /metrics` | Prometheus (`X-Agent-Token`) |
| `/crm/*` | API CRM local o proxy |
| `/internal/*` | Outbox, operaciones, tokens agente↔CRM |

---

## Variables clave (producción)

```env
CRM_MODE=external
CRM_BASE_URL=https://donregalo.pe/crm/public
CRM_INTERNAL_TOKEN=...
AGENT_INTERNAL_TOKEN=...
WHATSAPP_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_VERIFY_TOKEN=...
WHATSAPP_APP_SECRET=...          # sin esto, cualquiera inyecta webhooks
OPENAI_API_KEY=...
ROUTER_MODEL=gpt-4o-mini
QDRANT_URL=...
DONREGALO_API_BASE=https://donregalo.pe/clienteApiApp/api
PEDIDO_TEMPORAL_ENABLED=1        # opcional; default activo
WATCHDOG_ENABLED=1
ALERT_WHATSAPP=...
```

Detalle: [`.env.example`](.env.example).

---

## Despliegue (son DOS)

| Pieza | Dónde | Cómo llega a prod |
|-------|--------|-------------------|
| **Agente** | EasyPanel (`agente-donregalo`) | Push a GitHub **+ redeploy** |
| **CRM PHP** | Hosting Don Regalo (`crm/`) | Subir archivos **aparte** |

Webhook Meta: `…/whatsapp/webhook`. Deploy CRM: [`crm/docs/DEPLOY.md`](crm/docs/DEPLOY.md).

---

## Tests, evals y calidad

```bash
python scripts/check_mirror.py    # espejo sandbox/ OK
python -m pytest tests/ -q        # suite offline (no sale a la red)
python -m evals.runner            # corpus determinista
python scripts/quality_gate.py    # espejo + MCP + tests + evals (fail-closed)
```

El gate corre en CI y en el `Dockerfile`: una regresión no produce imagen elegible.
Operación: [`docs/CI_PRODUCTION_GATE.md`](docs/CI_PRODUCTION_GATE.md).

Tras editar la raíz:

```bash
python scripts/check_mirror.py --fix
```

---

## Documentación

| Documento | Contenido |
|-----------|-----------|
| [`ARQUITECTURA.md`](ARQUITECTURA.md) | Estado de producción, harness, CRM, deploy |
| [`CLAUDE.md`](CLAUDE.md) | Guía operativa: qué no repetir, flujo de trabajo |
| [`API.md`](API.md) | Contrato de `clienteApiApp` |
| [`docs/HARNESS.md`](docs/HARNESS.md) | Orquestador, especialistas, invariantes |
| [`CATALOGO.md`](CATALOGO.md) | Taxonomía y búsqueda de productos |
| [`docs/OBSERVABILIDAD.md`](docs/OBSERVABILIDAD.md) | Traces, métricas, auditoría |
| [`docs/RESILIENCIA.md`](docs/RESILIENCIA.md) | Circuit breakers y degradación |
| [`docs/SEGURIDAD_PROMPT_INJECTION.md`](docs/SEGURIDAD_PROMPT_INJECTION.md) | Guardrails de entrada |
| [`docs/PROTECCION_DATOS_Y_VALIDACION_MCP.md`](docs/PROTECCION_DATOS_Y_VALIDACION_MCP.md) | PII y contratos MCP |
| [`docs/COLA_DURABLE_REDIS.md`](docs/COLA_DURABLE_REDIS.md) | Cola inbound Redis |
| [`Harness Engineering …`](Harness%20Engineering%20%20documentaci%C3%B3n%2C%20buenas%20pr%C3%A1cticas%2C%20t%C3%A9cnicas%20y%20reglas.md) | Fundamentos de la disciplina |

`docs/SANDBOX_Y_CRM_PHP.md` está **obsoleto** (histórico del rework); usa `ARQUITECTURA.md`.

---

## Escalación humana

Tool `escalar_a_humano` (o handoff determinista) cuando: pide persona, frustración,
pago/comprobante, descuento, cancelación, o algo no verificable. El bot **no**
confirma comprobantes ni inventa “te confirmo cuando lo recibamos”. Tras escalar,
el sistema manda el mensaje de espera y cede el chat (`human_support`).

---

## Rollback legacy (Chatwoot / Evolution)

```bash
git checkout legacy-chatwoot-evolution
# restaurar env CHATWOOT_* / EVOLUTION_* y webhooks legacy
```
