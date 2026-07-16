# Arquitectura y estado de producción — Agente Don Regalo

Documento canónico de cómo está construido y desplegado el agente hoy. Sustituye a
[`docs/SANDBOX_Y_CRM_PHP.md`](docs/SANDBOX_Y_CRM_PHP.md), que quedó desactualizado
(describía el prompt monolítico `system.py` y el `sandbox/` como deploy).

> **Regla mental:** la fuente de verdad es la **raíz** (`app/`, `tests/`, `evals/`).
> `sandbox/` es solo un **espejo** que se sincroniza; ya no es el que se despliega.

Última actualización: julio 2026 (pedido temporal + taxonomía real `explorar_catalogo`).

---

## 1. Qué es

Agente de WhatsApp para **Don Regalo** (donregalo.pe), delivery de regalos en Lima.

```
Cliente WhatsApp
      │
      ▼
 Meta Cloud API
      │  webhook (200 inmediato + trabajo en background)
      ▼
 app/  (raíz del repo → EasyPanel, uvicorn app.main:app)
      ├─ buffer de mensajes
      ├─ HARNESS: orquestador → especialista → tools
      ├─ fillers de latencia (salvo saludos simples)
      └─ CRM_MODE=external ──HTTP+token──► crm-php (hosting Don Regalo)
                                              ├─ MySQL local (crm_* + usuarios)
                                              ├─ Inbox asesores (polling ~4s)
                                              └─ Outbox → push al agente → WhatsApp
```

**Pila:** FastAPI (Python 3.11), OpenAI, Qdrant, MySQL (CRM PHP), SQLite (local/tests).

| Pieza | Dónde vive | Rol |
|---|---|---|
| **Agente IA** | `app/` en la raíz (EasyPanel) | WhatsApp Cloud API, LLM, harness, tools, Qdrant |
| **CRM panel + API** | `crm-php/` en hosting PHP de Don Regalo | Inbox, login, reportes, persistencia MySQL |
| **Catálogo / pedidos** | `DONREGALO_API_BASE` → `clienteApiApp/api` | Productos, distritos, tipo de cambio, pedidos temporales |
| `sandbox/` | Espejo de la raíz | Se sincroniza; **no** es el deploy |
| `crm/` (Next.js) | Legado | **No** es el panel de producción |

---

## 2. El harness (arquitectura del agente)

Un turno del orquestador ([`app/harness/master.py`](app/harness/master.py)):

```
percibir → clasificar → delegar → reducir → persistir
```

```
Webhook Meta → buffer
                 │
                 ▼
        ORQUESTADOR  (harness/master.py)
        único que escribe estado · NO habla con el cliente
                 │
     ┌───────────┼────────────┐
     ▼           ▼            ▼
  router     especialistas   políticas
 (router.py)  (registry.py)  (policies.py)
                 │
                 ▼
              tools → render → WhatsApp
```

### Reglas que lo definen

1. **El orquestador no habla con el cliente.** Clasifica y delega. Todo texto de cara
   al cliente sale de un especialista, incluidos los saludos (los atiende `concierge`).
2. **Los especialistas devuelven `AgentResult`, nunca `str`**
   ([`contracts.py`](app/harness/contracts.py)): traen lo que dicen Y lo que aprendieron
   (`artifacts`, `state_patch`). Los ids de producto salen de los resultados de las
   tools, jamás de una regex sobre la prosa.
3. **El system message se compone por capas** ([`prompts/compose.py`](app/prompts/compose.py)):

   ```
   system(agente) = CORE + FACTS[agente] + PLAYBOOK[agente] + ESTADO
   ```

   - [`prompts/core.py`](app/prompts/core.py) — identidad, estilo y **RESTRICCIONES de
     seguridad**. Va en TODOS los agentes de cara al cliente.
   - [`prompts/facts.py`](app/prompts/facts.py) — datos del dominio, solo a quien los
     necesita (cobertura no lleva ids de ocasión; catálogo no lleva devoluciones).
   - [`prompts/playbooks.py`](app/prompts/playbooks.py) — el procedimiento de cada
     especialista.
4. **Prompt y toolset viven juntos** ([`registry.py`](app/harness/registry.py):`AgentSpec`).
   `test_prompts_architecture.py` obliga a que un playbook solo cite tools de su toolset.

### Los especialistas

| Agente | Atiende | ¿Determinista? | ¿Escala? |
|---|---|---|---|
| `concierge` | saludos, cortesía, fuera de alcance | no | no |
| `catalog` | búsqueda de productos, campañas | no | **no** |
| `detail` | detalle de un producto ya mostrado | no | no |
| `coverage` | distrito y tarifa | **sí** (código) | no |
| `checkout` | cierre del pedido (FSM) | **sí** (código) | sí |
| `policy` | políticas, pagos, objeciones | no | sí |
| `tracking` | estado de un pedido | no | sí |
| `escalate` | derivación a un asesor | no | sí |

`catalog` **no puede escalar** a propósito: buscar productos nunca es motivo de handoff.

### Determinista vs LLM

- **Sin LLM (código puro):** cobertura ([`coverage.py`](app/harness/coverage.py)),
  cierre ([`checkout.py`](app/harness/checkout.py), una FSM), políticas de negocio
  ([`policies.py`](app/harness/policies.py)), el saludo de bienvenida
  (`playbooks.WELCOME`) y el formato del listado de productos
  (`master.compose_product_reply` + [`render.py`](app/harness/render.py)).
- `coverage` y `checkout` están marcados `deterministic=True`: sus playbooks/tools NO
  los ve ningún modelo, el orquestador los resuelve en código.

### Router híbrido

[`router.py`](app/harness/router.py): reglas primero (rápidas, con confianza); por
debajo de `CONFIDENCE_FLOOR` decide un clasificador LLM barato (`ROUTER_MODEL`). Si el
LLM falla (timeout, 429, sin clave) o inventa una intención, mandan las reglas — el
router nunca tumba un turno. La `Trace` de cada turno registra `router`
(`rules`|`llm`|`fallback`) y `confidence`.

### La capa de adaptadores (dinero y formas)

La API devuelve **tres formas de producto** (listado, detalle, Qdrant) y una cuarta de
distrito, todo en **USD**. Todo pasa por [`tools/adapters.py`](app/tools/adapters.py),
que normaliza a una forma canónica y **convierte a soles en código**. `tipo_cambio`
NO es tool de ningún agente: los productos llegan con `precio_sol` y `precio_usd`.

Los tests de adapters corren contra **payloads reales grabados** en
`tests/fixtures/api/`.

---

## 3. Integración con la API de catálogo (`API.md`)

Contrato oficial: [`API.md`](API.md). Base: `DONREGALO_API_BASE`.

### Taxonomía real — no inventar categorías

- **`GET /catalogo/navegacion`** → tool `explorar_catalogo`
  ([`tools/catalog.py`](app/tools/catalog.py), cacheada). Es el **paso 0** del catálogo:
  categorías, subcategorías, filtros, ocasiones y landings tal como existen en la web.
  El bot ofrece SOLO nombres de ese payload y busca con esos slugs. Una lista
  hardcodeada en el prompt se desactualiza y el modelo extrapola subtipos que no existen.
- **`GET /productos/buscar`** con `categoria` / `filtro` (`url_filtro`) /
  `landing` (`url_categoria_filtro`) / `ocasion`: búsqueda estructurada por slug real
  (tool `buscar_productos`).
- **Si el cliente nombra una categoría, es un límite duro** ([`executor.enforce_category`](app/tools/executor.py)):
  solo si la API no tiene nada de esa categoría entra Qdrant, y esos productos van
  marcados `aproximado: true` para presentarlos como alternativas.
- El slug de categoría es **siempre `url_categoria`** (nunca `categoria_url`).

### Cierre y pedido temporal

El cierre lo conduce una FSM ([`checkout.py`](app/harness/checkout.py)):

```
idle → district → date → schedule → card(dedicatoria)
     → recipient → address → contact → summary → [pedido temporal] → payment
```

Al confirmar el resumen, el sistema:

1. Crea un **pedido temporal** en el panel vía **`POST /pedidos/temporales`**
   ([`harness/orders.py`](app/harness/orders.py) arma el cuerpo desde el estado;
   [`tools/orders.py`](app/tools/orders.py) hace el POST). Es **best-effort**: si falta
   un dato, la API da 422 o el CRM cae, se registra y el handoff sigue — el cliente que
   espera para pagar no se bloquea. `delivery` se manda en PEN; la API lo reconvierte.
   Se puede apagar con `PEDIDO_TEMPORAL_ENABLED=0` (por defecto activo).
2. Anuncia la **venta cerrada** en el CRM ([`sale.py`](app/harness/sale.py)): pinta el
   chat en verde y avisa al equipo.
3. Escala a un humano para el pago (el agente **no** confirma pagos).

### Otros endpoints en uso

`/distritos` (cobertura + tarifa), `/pedidos/rastrear` (rastreo), `/metodos-pago`,
`/configuracion/tipo-cambio`, `/productos/activos` (valida ids tras Qdrant).

---

## 4. CRM PHP (`crm-php/`)

Panel de asesores + API de persistencia en el hosting de Don Regalo. MySQL local (no
se abre a Internet); el agente habla con él solo por **HTTP + token**.

- **Panel:** inbox con polling (~4s), highlight de `human_support` ("AYUDA"), toggle
  AI/HUMAN, resumen del lead, venta en verde. Assets: `public/assets/inbox.js`,
  `app.css`. Los **enlaces http/https del chat son clickeables** (linkify seguro:
  escapa y luego enlaza).
- **API interna** (`crm-php/public/api/index.php`), base `{CRM_BASE_URL}/api/...`:
  conversaciones, mensajes, memoria/leads, `mode` (AI↔HUMAN), settings, outbox,
  watchdog, reportes. Auth por `X-CRM-Token` / `Bearer` (salvo `/health`).
- **Outbox** (asesor → WhatsApp): el panel encola en MySQL → curl a
  `{agent_base_url}/internal/outbox/send` → el **agente** envía y persiste el outbound
  (el PHP ya no lo re-inserta, evita burbujas duplicadas).

El estado de la conversación del harness vive hoy como blob JSON en `settings`
(`harness_state_{conversation_id}`) — ver Deuda conocida.

---

## 5. Despliegue (son DOS)

- **Agente:** EasyPanel, servicio `agente-donregalo`, `uvicorn app.main:app`. Despliega
  **desde GitHub**: el código local no llega a producción hasta hacer **push Y redeploy**.
  Webhook Meta: `.../whatsapp/webhook`.
- **CRM PHP:** hosting de Don Regalo, carpeta `crm-php/`. Se sube **aparte** (el verde de
  venta, el sonido del handoff, los emojis y los enlaces clickeables viven aquí). Tras
  cambios solo de PHP no hace falta redeploy en EasyPanel.

### Variables de entorno clave (`.env.example`)

| Variable | Para qué |
|---|---|
| `WHATSAPP_APP_SECRET` | Valida la firma del webhook — sin él, cualquiera inyecta mensajes |
| `CRM_MODE=external` | Usa el CRM PHP (no SQLite local) |
| `CRM_BASE_URL` | URL del CRM PHP (`https://donregalo.pe/crm/public`) |
| `CRM_INTERNAL_TOKEN` / `AGENT_INTERNAL_TOKEN` | Tokens agente↔CRM (idénticos en ambos lados) |
| `DONREGALO_API_BASE` | API de catálogo/pedidos (`https://donregalo.pe/clienteApiApp/api`) |
| `PEDIDO_TEMPORAL_ENABLED` | Crear pedido temporal al cerrar (opcional; default `1`) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `ROUTER_MODEL` | LLM principal y clasificador del router |
| `QDRANT_URL` / `QDRANT_API_KEY` / `QDRANT_COLLECTION` | Búsqueda semántica |
| `WATCHDOG_ENABLED` / `ALERT_WHATSAPP` | Vigía de conversaciones sin respuesta y avisos |

---

## 6. Tests, evals y el espejo `sandbox/`

```bash
python -m pytest tests/ -q        # suite offline (no sale a la red)
python -m evals.runner            # corpus determinista de regresión
python -m evals.runner --llm      # incluye el clasificador LLM (llama a OpenAI)
```

- **La suite NO sale a la red.** `tests/conftest.py` fuerza `crm_enabled=False`,
  stub de `/productos/activos` y apaga la creación de pedidos temporales.
- **Invariantes** ([`harness/invariants.py`](app/harness/invariants.py)): cada una nació
  de un incidente real (contraentrega, URLs pegadas al texto, precios inventados,
  productos repetidos). Se evalúan en runtime (a la `Trace`) y en el corpus.
- **Cada bug arreglado deja un caso en el corpus** (`evals/corpus/*.yaml`).

### Sincronizar el espejo tras editar la raíz

```bash
rm -rf sandbox/app sandbox/tests sandbox/evals
mkdir -p sandbox/app sandbox/tests sandbox/evals
cp -r app/. sandbox/app/ && cp -r tests/. sandbox/tests/ && cp -r evals/. sandbox/evals/
diff -rq app sandbox/app --exclude=__pycache__
```

(En PowerShell: `Remove-Item -Recurse -Force` + `Copy-Item -Recurse -Force`.)

---

## 7. Las cosas que se rompieron y NO hay que repetir

1. **El formato de productos NO va en el prompt.** Lo compone el código; cada desvío del
   modelo llegaba al cliente como un muro de enlaces en vez de fotos.
2. **La taxonomía NO va en el prompt.** Sale solo de `explorar_catalogo`; hardcodearla
   hizo que el modelo inventara "desayuno clásico/premium", "globos y kits".
3. **La API tiene varias formas de producto, todo en USD.** Todo pasa por `adapters.py`;
   el dinero lo calcula el adapter, no el LLM.
4. **Categoría nombrada = límite duro.** La API manda; los parecidos van `aproximado`.
5. **Mostrar > preguntar.** Máximo una pregunta de contexto antes de mostrar productos;
   prohibido encadenar menús/subtipos.

---

## 8. Dónde mirar en el código

| Tema | Archivo |
|---|---|
| Orquestador (turno) | `app/harness/master.py` |
| Router híbrido | `app/harness/router.py` |
| Agentes + toolsets | `app/harness/registry.py` |
| Cierre (FSM) | `app/harness/checkout.py` |
| Pedido temporal | `app/harness/orders.py`, `app/tools/orders.py` |
| Cobertura | `app/harness/coverage.py` |
| Venta cerrada | `app/harness/sale.py` |
| Estado | `app/harness/state.py` |
| Composición de prompts | `app/prompts/compose.py` (`core.py`, `facts.py`, `playbooks.py`) |
| Tools (esquemas) | `app/tools/definitions.py` |
| Tools (HTTP catálogo) | `app/tools/catalog.py`, `search.py`, `executor.py`, `adapters.py` |
| Loop LLM / fillers | `app/services/agent.py` |
| Buffer / flush CRM | `app/services/buffer.py` |
| Cliente HTTP CRM | `app/crm/http_client.py` |
| API CRM (PHP) | `crm-php/public/api/index.php` |
| Inbox UI | `crm-php/views/inbox.php`, `crm-php/public/assets/inbox.js` |

Ver también [`CLAUDE.md`](CLAUDE.md) (guía corta para agentes) y [`API.md`](API.md).
