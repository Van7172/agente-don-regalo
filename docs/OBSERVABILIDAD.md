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
- `donregalo_operation_duration_ms_sum`;
- `donregalo_operation_duration_ms_count`;
- `donregalo_operation_duration_ms_max`;
- `donregalo_audit_events_total`.

Las únicas etiquetas son operación, evento y resultado. No se usan
`conversation_id`, productos ni identificadores de clientes como etiquetas, para
evitar cardinalidad ilimitada.

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
4. latencia máxima de `openai.specialist` o `harness.turn` por encima del SLA;
5. aumento de `harness.turn:guardrail_blocked`;
6. cuota de OpenAI (`openai.specialist:quota`).

## Operación y retención

Los contadores actuales viven en memoria y se reinician en cada despliegue. Los
eventos de auditoría se conservan según la retención de logs configurada en
EasyPanel. Para retención histórica y paneles se debe conectar un recolector
externo (Prometheus + Grafana/Loki, OpenTelemetry u otro equivalente).

No se deben exponer los logs completos en un endpoint HTTP ni enviarlos al CRM:
el CRM contiene datos comerciales y la observabilidad contiene metadatos
operacionales; sus responsabilidades permanecen separadas.
