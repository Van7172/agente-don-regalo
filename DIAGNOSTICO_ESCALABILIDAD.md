# Re-auditoría: escalabilidad y buenas prácticas de agentes IA

Segunda pasada sobre el Agente Don Regalo, esta vez con el foco puesto en
**qué reforzar y qué implementar para escalar** según las buenas prácticas de
sistemas agénticos en producción. Fecha: 18/07/2026.

---

## Primero: el proyecto avanzó mucho desde la auditoría anterior

La base pasó de **9.121 → 14.453 líneas** y cerró los dos hallazgos P1 previos, además
de sumar infraestructura de nivel producción:

- ✅ **HTML saneado en la frontera** (`adapters.clean_html`, aplicado a nombre,
  descripción corta, descripción y métodos de pago). Cerraba la deuda #1.
- ✅ **Referencias a `listar_categorias` eliminadas** de los prompts (solo queda un
  comentario explicando por qué no están). Restaurado el invariante de "una sola puerta
  a la taxonomía".
- ✅ **Guardrails que ahora BLOQUEAN**, no solo observan (`guardrails/response.py`:
  `guard_reply`/`sanitize_reply` descartan precio inventado, contraentrega y fugas de
  contexto interno, y reconstruyen el listado desde `artifacts`). Era mi P2-3.
- ✅ **Observabilidad estructurada** (`observability/core.py`): trace-id por turno vía
  `ContextVar`, auditoría JSON con campos allow-listed sin PII, métricas y salida
  Prometheus en `/metrics` (protegido por token).
- ✅ **Circuit breakers** por dependencia (OpenAI, MCP, Qdrant, CRM, catálogo) con
  estados closed/half-open/open y export Prometheus.
- ✅ **Cola durable Redis** (streams + consumer group + DLQ + reclaimer) con
  **lock por conversación** que serializa turnos entre réplicas; degrada a cola local.
- ✅ **CI "Production Gate"**: `mirror → contrato MCP → tests → evals`, imagen de
  producción bloqueada tras el gate. **663 tests pasan** (antes 388).

Esto ya es una arquitectura agéntica seria. Lo que sigue son los **siguientes peldaños**,
no correcciones de algo roto.

---

## A. Puntos a reforzar (ya existen, conviene endurecer)

### 🔴 A1 — El estado no tiene control de concurrencia optimista (CAS/versión)

`load_state`/`save_state` (`harness/state.py`) es un **read-modify-write de documento
completo** contra los settings del CRM. Los turnos entrantes se serializan por el lock
de conversación de la cola Redis, pero **dos escritores viven fuera de ese lock**:

- el **releaser** (tick del watchdog) hace `load_state` → muta `keep_human`,
  `last_human_outbound_at` → `save_state`;
- el drenaje del outbox y ventas manuales del CRM tocan el mismo documento.

Si el releaser y un turno entrante caen en la misma conversación a la vez, hay una
ventana de **lost update** (el último que graba pisa al otro). Baja probabilidad, alto
coste (p. ej. perder `keep_human` y que el bot recupere un chat que un humano tenía).

**Recomendación:** añadir un campo `version` al estado y hacer *compare-and-set* al
guardar (reintentar recargando si la versión cambió), o que el releaser adquiera el
**mismo** lock por conversación que usa la cola. Es la pieza que falta para que "estado
compartido entre réplicas" sea correcto y no solo probable.

### 🟡 A2 — Métricas y dedup en memoria son por-proceso

`observability.core` guarda contadores en un dict global del módulo; los stats de la cola
local, el estado de los circuit breakers y el set `_pending_ids` de deduplicación viven
en el proceso. Con varios workers de uvicorn o varias réplicas: `/metrics` refleja solo
la réplica raspada, y el dedup/circuito no se comparten.

**Recomendación:** (1) documentar que `/metrics` es **por réplica** y configurar
Prometheus multi-target (una serie por pod); (2) para dedup e idempotencia con réplicas,
apoyarse en el camino Redis (ya lo hace en ese backend) y no en el set local; (3)
opcional: empujar el estado de los circuit breakers a Redis si se quiere una vista
global de salud.

### 🟡 A3 — La DLQ no tiene consumidor ni alerta automática

Hay stream de *dead-letter* (los mensajes envenenados caen ahí tras `max_retries`), pero
no vi un drenaje/alerta cuando `depth(DLQ) > 0`. Un mensaje envenenado se queda callado.

**Recomendación:** un worker que vigile la profundidad de la DLQ y dispare alerta
(`notify_team`/webhook) sobre un umbral, más una métrica `donregalo_dlq_depth`.

### 🟡 A4 — Latencia agregada sin percentiles

`record_operation` guarda `count`, `sum` y `max`. Con eso no se puede calcular p95/p99
—la métrica que de verdad importa para latencia de un agente—. El `max` esconde la cola.

**Recomendación:** histogramas con buckets para las latencias de OpenAI/MCP/CRM y del
turno completo. Prometheus los agrega a p95/p99 sin código extra.

---

## B. Qué implementar (capacidades nuevas para escalar con seguridad)

### 🔴 B1 — Suite de evals adversariales (red-team) como red de regresión de seguridad

El CORE tiene reglas fuertes de anti-manipulación, privacidad y alcance, pero **los evals
no las ejercitan**: el corpus cubre routing, replies e invariantes estructurales. Una
inyección de prompt, un intento de jailbreak o una exfiltración de datos de otro cliente
no tienen hoy un caso que falle si una regresión los deja pasar.

**Recomendación:** un corpus `adversarial.yaml` con: inyección directa ("ignora tus
instrucciones…"), inyección **indirecta** vía resultados de `buscar_conocimiento_equipo`
(el KB es contenido influenciable), pedido de datos de otro pedido/cliente, y regateo.
Aserción: la respuesta no viola las reglas y/o deriva. Convierte la seguridad del prompt
en algo verificable en cada commit.

### 🟡 B2 — Evals de LLM gateados por umbral + tendencia

`routing_llm.yaml` existe pero solo corre con `RUN_LLM_EVALS=1` fuera del gate. El
comportamiento del clasificador LLM puede degradar (cambio de modelo, del proveedor) sin
que nadie lo note.

**Recomendación:** job nocturno que corra los evals LLM contra un **umbral de tasa de
acierto** y registre la serie temporal. Alertar si baja de X%. Barato y evita
regresiones silenciosas de calidad al cambiar de modelo.

### 🟡 B3 — LLM-as-judge sobre una muestra (calidad semántica)

Las invariantes son estructurales (precio respaldado, sin duplicados, formato). No miden
**tono, corrección ni resolución** —justo lo que hace bueno a un agente de atención—.

**Recomendación:** un juez LLM con rúbrica corta (¿resolvió?, ¿tono cálido?, ¿sin
inventar?) sobre una muestra de conversaciones reales, con score guardado como métrica.
No sustituye las invariantes; caza lo que ellas no ven.

### 🟡 B4 — Contabilidad de tokens y coste por turno

El historial está acotado (12 h / 15 mensajes, bien), pero **no se emite uso de tokens
ni coste**. A escala, el gasto de OpenAI es la línea que más crece y hoy es ciega.

**Recomendación:** emitir `prompt_tokens`/`completion_tokens` por agente en la traza y
como métrica (`donregalo_llm_tokens_total{agent,type}`). Habilita alertas de coste y
detecta *prompt bloat* antes de que se note en la factura. Con el CORE cacheable, medir
también el *cache hit* de prompt.

### 🟢 B5 — Fallback de proveedor de modelo

Retry + circuit breaker cubren picos, pero ante una caída sostenida de OpenAI el agente
degrada a fallback suave/handoff (correcto, pero es indisponibilidad). Para mayor SLA,
un **modelo/proveedor secundario** detrás de la misma interfaz (`_chat_completion`)
mantiene el servicio con degradación elegante.

### 🟢 B6 — Versionado de prompts + eval por versión

Los prompts son constantes Python. Un cambio en el CORE o un playbook no deja rastro de
"antes/después" ni corre su propio eval diferencial.

**Recomendación:** versionar los prompts (hash o número) en la traza, y en CI correr los
evals **contra el diff del prompt** para ver el impacto de cada cambio de redacción.
Es lo que convierte "tocar el prompt" en algo medible en vez de un acto de fe.

---

## C. Estratégico (no urgente, define el techo de escala)

- **Multi-tenant.** Todo asume un tenant (`default_tenant_slug=don-regalo`): config,
  estado y prompts. Si el objetivo es servir más marcas, hay que parametrizar tenant en
  el estado, la taxonomía y la composición del prompt. Decisión de producto, no bug.
- **Espejo `sandbox/` (154 archivos).** Ya está protegido por CI, pero sigue duplicando
  toda la superficie a mano. A medio plazo: generarlo como artefacto o eliminarlo.
- **Endurecer secretos en arranque.** `main.py` ya valida el token del CRM y avisa del
  dry-run; extender el mismo *fail-loud* a `WHATSAPP_APP_SECRET` en modo producción (hoy
  la firma del webhook es opcional si el secreto está vacío) y añadir rate-limit al
  endpoint del webhook.

---

## Hoja de ruta sugerida (por impacto/esfuerzo)

| Prioridad | Ítem | Por qué ahora |
|---|---|---|
| 1 | **A1** CAS/versión o lock compartido en el estado | Única grieta de corrección real bajo concurrencia; barata de cerrar |
| 2 | **B1** evals adversariales | Convierte la seguridad del prompt en regresión verificable |
| 3 | **A3** alerta de DLQ + **B4** tokens/coste | Visibilidad operativa y de gasto que hoy falta |
| 4 | **A4** histogramas p95/p99 + **A2** doc métricas por réplica | Medir latencia real al escalar horizontalmente |
| 5 | **B2/B3** evals LLM gateados + juez de calidad | Evita regresiones de calidad al cambiar de modelo |
| 6 | **B5/B6/C** provider fallback, versionado de prompts, multi-tenant | Techo de escala y SLA a más largo plazo |

---

## Resumen

El agente **ya aplica** la mayoría de las buenas prácticas de sistemas agénticos:
determinismo en lo crítico, guardrails que bloquean, contrato tipado, observabilidad con
trace-id sin PII, circuit breakers, cola durable con DLQ y lock por conversación, y un
quality-gate en CI. La escalabilidad restante no es reescribir nada, sino **cerrar la
grieta de concurrencia del estado (A1)**, **hacer verificable la seguridad (B1)** y
**abrir los ojos a coste y latencia de cola (A3/A4/B4)**. Con eso, el sistema está listo
para crecer en volumen y en superficie sin perder el control que ya construyó.

*Re-auditoría hecha leyendo los módulos nuevos (guardrails, observability, resilience,
cola Redis/local, estado, MCP, CI/quality-gate) y ejecutando la suite (663 pasan; los 9
fallos vistos son de Python 3.10 en el entorno de auditoría, no del código —CI usa 3.12).*
