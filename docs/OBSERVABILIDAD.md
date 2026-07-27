# Observabilidad y auditoría

## Objetivo

Reconstruir qué ocurrió en un turno sin guardar lo que escribió el cliente ni
exponer secretos. El flujo correlacionado es:

```text
Webhook → cola inbound → worker → arnés → especialista → tool/MCP/CRM
   └──────────────────────── trace_id ────────────────────────────┘
```

La implementación vive en `app/observability/` y no depende de un proveedor.
EasyPanel recibe los logs; `/metrics` publica agregados compatibles con
Prometheus.

## Contrato de privacidad

Los eventos de auditoría usan una lista cerrada de campos. No se admiten:

- mensajes, prompts ni respuestas;
- nombres, teléfonos, direcciones o correos;
- `wa_id` o `wamid` completos;
- tokens, cabeceras o argumentos de herramientas;
- valores del estado de checkout.

El `trace_id` de un mensaje se deriva con SHA-256 del `wamid`: permite reconocer
una redelivery sin revelar el identificador original. La traza del arnés registra
longitud de entrada y nombres de campos modificados, nunca sus valores.

## Eventos

Cada evento es una línea JSON prefijada con `[audit]`.

| Evento | Resultado típico | Qué permite auditar |
|---|---|---|
| `webhook.signature` | `rejected` | Firma inválida |
| `webhook.payload` | `rejected` | JSON inválido |
| `webhook.inbound` | `ok`, `rejected` | Aceptación, duplicados y saturación |
| `inbound.worker` | `ok`, `error`, `cancelled` | Procesamiento y apagado |
| `harness.turn` | `ok`, `handoff`, `guardrail_blocked` | Enrutamiento y decisiones |
| `openai.request` | `ok`, `error`, `quota`, `invalid` | Router y especialistas |
| `tool.execute` | `ok`, `blocked`, `error` | Herramientas sin sus argumentos |
| `mcp.call` | `ok`, `business_error`, `error` | Transporte MCP real |
| `crm.http` | `ok`, `error` | Integración agente↔CRM |

Ejemplo seguro:

```json
{
  "event": "harness.turn",
  "outcome": "ok",
  "trace_id": "wa-a17b...",
  "intent": "catalog_search",
  "agent": "catalog",
  "tool_count": 1,
  "latency_ms": 842
}
```

## Métricas

`GET /metrics`, protegido con `X-Agent-Token`, devuelve:

- `donregalo_operations_total`;
- `donregalo_operation_duration_ms_bucket{le}` — histograma de latencia;
- `donregalo_operation_duration_ms_sum`;
- `donregalo_operation_duration_ms_count` — solo las observaciones
  **cronometradas**, que es lo que exige el histograma (hay operaciones que se
  registran sin medir tiempo);
- `donregalo_operation_duration_ms_max`;
- `donregalo_audit_events_total`;
- `donregalo_dlq_depth{scope}` — mensajes en la cola de descartados;
- `donregalo_llm_tokens_total{agent,type}` — `type` es `prompt`, `completion` o
  `cached`;
- `donregalo_llm_cost_usd_total{agent}` — solo si se configuró `LLM_PRICES`.

Las únicas etiquetas son operación, evento, resultado y agente. No se usan
`conversation_id`, productos ni identificadores de clientes como etiquetas, para
evitar cardinalidad ilimitada.

### Operación con varias réplicas

Todo el estado de métricas vive en memoria del proceso, así que **cada réplica
publica lo suyo**. Para tener una vista del servicio hay que raspar *todas* las
réplicas (scrape multi-target: un target por pod) y agregar con la regla de cada
serie — la tabla de abajo.

Lo que **no** depende de esto, porque ya está coordinado en Redis:

- **Deduplicación de mensajes.** Con `INBOUND_QUEUE_BACKEND=redis`, la cola
  descarta un `wamid` repetido con un `SET NX` antes de encolarlo, así que una
  redelivery de Meta que caiga en otro pod se descarta igual. El set en memoria
  de `buffer._already_seen` es una segunda red del proceso, no la que sostiene
  la garantía.
- **Un turno a la vez por conversación.** El lock por conversación también es de
  Redis.

Con la cola local (`backend=local`) ninguna de las dos cosas cruza réplicas: ese
backend es para desarrollo y para degradar si Redis cae, **no** para correr
varias réplicas en producción.

### Cómo agregar cada una entre réplicas

`/metrics` es **por réplica**: cada pod publica lo suyo. La regla de agregación
no es la misma para todas, y equivocarla da cifras falsas sin avisar:

| Métrica | Agregación | Por qué |
|---|---|---|
| `donregalo_operations_total`, `..._audit_events_total`, `donregalo_llm_tokens_total`, `donregalo_llm_cost_usd_total`, `..._duration_ms_bucket` | `sum` | Contadores: cada réplica cuenta solo su parte del total |
| `donregalo_dlq_depth` | **`max`** | Todas las réplicas leen el MISMO `XLEN` del stream de Redis. Sumarlo lo multiplicaría por el número de pods |
| `donregalo_operation_duration_ms_max` | **`max`** | El peor caso del servicio es el peor de cualquier réplica, no la suma |

### Percentiles de latencia

Los buckets son lo que permite p95/p99 **del servicio**, no de un pod al azar:

```promql
histogram_quantile(
  0.95,
  sum by (operation, le) (rate(donregalo_operation_duration_ms_bucket[5m]))
)
```

Por eso es un histograma y no un *summary*: los cuantiles de un summary vienen
ya calculados por réplica y promediarlos no significa nada.

El panel de operaciones del CRM muestra el p95 sin necesidad de Prometheus: sale
de `metrics_snapshot()`, que lo estima de los mismos buckets. Es una
**estimación** acotada por los bordes del bucket y por el máximo real — con
pocos cortes se nota, y por eso `_max` sigue publicándose aparte.

### Coste del LLM

Los **tokens** se cuentan siempre: salen del bloque `usage` de OpenAI, son
exactos y no caducan. El **dinero** solo se calcula si se declara el tarifario:

```env
LLM_PRICES={"gpt-4o-mini":{"in":0.15,"out":0.60,"cached_in":0.075}}
```

USD por cada 1.000 tokens. No hay precios por defecto en el código a propósito:
un tarifario hardcodeado se queda viejo en silencio y produce una cifra
equivocada, que es peor que no dar ninguna. Sin `cached_in`, los tokens servidos
desde la caché se cobran como prompt normal — la estimación conservadora, porque
nunca conviene prometer un ahorro que no se ha comprobado.

`GET /health` informa que el contexto de trazas, la auditoría y `/metrics` están
disponibles; además conserva las métricas propias de la cola inbound.

Los estados de las dependencias se exponen como métricas gauge y en `/health`.
La política y operación de esos circuitos está documentada en
[`RESILIENCIA.md`](RESILIENCIA.md).

```bash
curl -H "X-Agent-Token: $AGENT_INTERNAL_TOKEN" https://agente/metrics
```

## Alertas recomendadas

Al conectar Prometheus/Grafana o el recolector elegido:

1. `webhook.request:rejected` o `inbound.submit:full` mayor que cero;
2. errores de `inbound.worker`;
3. incremento sostenido de `mcp.call:error` o `crm.http:error`;
4. **p95** de `openai.specialist` o `harness.turn` por encima del SLA — antes
   esta alerta miraba el máximo, que es UN caso y casi siempre un pico raro: se
   disparaba por ruido o no se disparaba nunca;
5. aumento de `harness.turn:guardrail_blocked`;
6. cuota de OpenAI (`openai.specialist:quota`);
7. `max(donregalo_dlq_depth) > 0` — un mensaje ahí es un cliente al que **no
   contestó nadie**: el bot se rindió y el fallo ocurrió antes del CRM, así que
   tampoco aparece en el inbox. El watchdog ya avisa por WhatsApp; esta alerta es
   la que sirve cuando el propio canal del aviso es lo que está caído;
8. subida de `sum(donregalo_llm_tokens_total{type="prompt"})` sin subida
   equivalente de turnos — es *prompt bloat*, y se nota en la factura un mes
   después de haberlo causado.

## Operación y retención

Los contadores actuales viven en memoria y se reinician en cada despliegue. Los
eventos de auditoría se conservan según la retención de logs configurada en
EasyPanel. Para retención histórica y paneles se debe conectar un recolector
externo (Prometheus + Grafana/Loki, OpenTelemetry u otro equivalente).

No se deben exponer los logs completos en un endpoint HTTP ni enviarlos al CRM:
el CRM contiene datos comerciales y la observabilidad contiene metadatos
operacionales; sus responsabilidades permanecen separadas.
