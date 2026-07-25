# Resiliencia y circuit breakers

## Objetivo

`app/resilience/` evita insistir sobre una dependencia externa que ya está
fallando. No reemplaza los reintentos ni los respaldos: los coordina para que el
agente falle rápido, conserve capacidad y use el camino alterno disponible.

## Estados

1. **Cerrado:** deja pasar las llamadas y cuenta fallos consecutivos.
2. **Abierto:** rechaza de inmediato, sin hacer red.
3. **Semiabierto:** al vencer la pausa permite una sola llamada de prueba.
4. Una prueba sana cierra el circuito; una fallida vuelve a abrirlo y reinicia la
   pausa.

Una cancelación interna no cuenta como fallo. Los HTTP `4xx` tampoco deterioran
el circuito porque normalmente representan un error de solicitud o negocio,
excepto `408` y `429`, que sí son fallos transitorios de la dependencia.

## Circuitos

| Circuito | Límite protegido | Degradación existente |
|---|---|---|
| `openai.router` | Clasificador de intención | Reglas deterministas |
| `openai.specialist` | Respuesta de especialistas | Flujo de error/handoff existente |
| `openai.embeddings` | Vectores de búsqueda | Error controlado de la herramienta |
| `mcp` | Herramientas MCP de Don Regalo | API REST del catálogo |
| `catalog.rest` | API REST de Don Regalo | Error controlado de la herramienta |
| `qdrant` | Productos y conocimiento | Búsqueda textual cuando no está configurado; vacío seguro en KB |
| `crm` | API y medios del CRM externo | Comportamiento de error ya definido por cada consumidor |

MCP y REST tienen circuitos independientes. Así, una caída de MCP abre solo ese
camino y el respaldo REST continúa disponible.

## Configuración

```env
CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
CIRCUIT_BREAKER_RECOVERY_SECONDS=30
```

Los valores se aplican a todos los circuitos. El umbral cuenta solicitudes
lógicas: los reintentos internos de una llamada de especialista a OpenAI no
inflan el contador.

## Operación

`GET /health` incluye `circuit_breakers` con estado, fallos consecutivos y tiempo
restante. El endpoint continúa respondiendo `status=ok` si un circuito está
abierto para evitar ciclos de reinicio; el detalle señala la degradación.

`GET /metrics`, protegido por `X-Agent-Token`, publica:

- `donregalo_circuit_breaker_state` (`0` cerrado, `1` semiabierto, `2` abierto).
- `donregalo_circuit_breaker_consecutive_failures`.
- contadores `circuit.<nombre>` para fallos, rechazos y transiciones.

Las transiciones y rechazos producen auditoría estructurada sin prompts, PII,
tokens ni argumentos de herramientas.

El registro vive en memoria y es independiente por proceso. Con varios workers,
cada proceso protege su propia capacidad; Prometheus debe consultar cada réplica
o agregarlas en la plataforma.
