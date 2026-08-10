# Ecosistema LangChain / LangGraph / LangSmith / LangFlow y escalado a la nube

Guía de estudio aplicada a **agente-don-regalo**: qué es cada pieza, cómo se
relacionan, qué ya cubre este repo sin ellas, y cómo encajarían si el despliegue
pasa de EasyPanel + hosting PHP a **GCP, AWS o Azure**.

> Nota de nombres: es **LangGraph** (grafo de orquestación). No existe
> “LangGraft” en el ecosistema oficial.

Estado del documento: **9 de agosto de 2026**. Es material de estudio y diseño,
no un plan de migración aprobado ni código a implementar.

---

## 1. Mapa mental del ecosistema

```text
┌─────────────────────────────────────────────────────────────┐
│  LangFlow (UI low-code)                                     │
│  Diseñar / prototipar flujos visualmente                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ inspira o exporta ideas
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LangGraph (orquestación)                                   │
│  Grafo de estados: nodos, aristas, ciclos, handoffs         │
└──────────────────────────┬──────────────────────────────────┘
                           │ usa como SDK
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LangChain (biblioteca)                                     │
│  LLM, tools, prompts, retrievers, memoria, parsers          │
└──────────────────────────┬──────────────────────────────────┘
                           │ emite telemetría
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  LangSmith (plataforma)                                     │
│  Traces, datasets, evals, versiones de prompts, costos      │
└─────────────────────────────────────────────────────────────┘
```

| Pieza | Tipo | Pregunta que responde |
|---|---|---|
| **LangChain** | Librería Python/JS | ¿Cómo hablo con el LLM, las tools y el vector store con una API uniforme? |
| **LangGraph** | Librería de orquestación | ¿Cómo modelar un flujo con estados, bucles y decisiones sin un “agente libre” opaco? |
| **LangSmith** | SaaS (observabilidad + evals) | ¿Qué pasó en cada turno, cuánto costó, y cómo regreso si cambio el prompt? |
| **LangFlow** | Producto UI | ¿Puedo armar un prototipo arrastrando nodos sin tocar mucho código? |

Relación clave:

- **LangGraph** suele construirse **sobre** abstracciones de LangChain (o al menos
  convive con ellas), pero puedes usar LangGraph con clientes LLM propios.
- **LangSmith** observa **cualquier** app que instrumentes (incluido un FastAPI
  casero); no exige reescribir todo en LangChain.
- **LangFlow** es la capa más alejada de producción crítica: útil para demos y
  exploración, frágil como runtime de un bot de ventas con invariantes duras.

---

## 2. Qué hace cada uno (detalle de estudio)

### 2.1 LangChain

**Propósito:** reducir código repetido alrededor de:

- Chat models (OpenAI, Anthropic, Vertex, Bedrock…).
- **Tools** / function calling.
- **Retrievers** (Qdrant, PGVector, etc.).
- Cadenas simples (`prompt → LLM → parser`).
- Memoria de conversación (buffers, summaries).
- Document loaders y text splitters (RAG clásico).

**Conceptos a dominar:**

| Concepto | Idea |
|---|---|
| `ChatModel` | Interfaz al LLM |
| `Tool` / `StructuredTool` | Función que el modelo puede invocar |
| `Runnable` | Unidad componible (`invoke` / `ainvoke` / `stream`) |
| `Retriever` | “Dado un query, dame documentos” |
| Output parsers | Forzar JSON / Pydantic a la salida |

**Lo que NO es:** un producto de hosting. Es dependencia de aplicación.

**Riesgo en este repo:** reimplementar el harness “a lo LangChain Agent” y perder
las garantías que ya tienes en código (menú, precios, checkout FSM, invariantes).

---

### 2.2 LangGraph

**Propósito:** orquestar agentes como **grafo de estados**:

- Nodos = pasos (clasificar, buscar catálogo, checkout, escalate…).
- Aristas = transiciones (`si intent=checkout → nodo_checkout`).
- Estado compartido tipado (equivalente conceptual a `ConversationState`).
- Soporte para ciclos, interrupciones humanas (human-in-the-loop), persistencia
  de checkpoint.

**Analogía con este proyecto:**

| LangGraph | Don Regalo (hoy) |
|---|---|
| State | `ConversationState` + settings CRM |
| Node | especialista (`catalog`, `checkout`, `escalate`…) |
| Conditional edge | `router.classify` + `master` |
| Checkpoint | persistencia de estado / memoria |
| Human interrupt | `mode=HUMAN` + handoff |

**Cuándo estudiar LangGraph de verdad:** cuando un flujo nuevo tenga muchos
ramales y ciclos que ya no quepan cómodos en `master.py` + router.  
**Cuándo no:** para reescribir el checkout determinista; esa FSM debe seguir
siendo código puro.

---

### 2.3 LangSmith

**Propósito:** plataforma de **observabilidad y evaluación** para apps LLM:

- Traces por turno (latencia, tokens, tool calls, errores).
- Datasets y experiments (parecido a tu `evals/corpus`).
- Versionado de prompts.
- Feedback humano sobre respuestas.
- Costos agregados.

**Analogía con este proyecto:**

| LangSmith | Don Regalo (hoy) |
|---|---|
| Trace | `harness/trace.py` + `observability` |
| Dataset / experiment | `evals/runner` + YAML corpus |
| Feedback | CRM / notas / handoffs |
| Prompt hub | `app/prompts/*` en git |

**Es la pieza con mejor ROI** si quieres “parecer / ser” ecosistema Lang* sin
tirar el harness: instrumentar las llamadas OpenAI actuales y seguir
ejecutando el mismo agente.

---

### 2.4 LangFlow

**Propósito:** editor visual de flujos (nodos LLM, tools, vector stores).

**Útil para:**

- Prototipar una idea de RAG o de clasificador con el equipo no-dev.
- Demos comerciales (“así se ve un flujo”).

**Poco adecuado para:**

- Producción de WhatsApp con invariantes de negocio (precios, menú, cobro).
- El CRM PHP + outbox + claim atómico: LangFlow no reemplaza eso.

Regla de estudio: **LangFlow ≠ runtime de Don Regalo**. Como mucho, laboratorio.

---

## 3. Arquitectura actual del proyecto (ancla)

Un turno hoy (simplificado):

```text
Meta Cloud API
    → webhook FastAPI
    → cola inbound (local o Redis)
    → buffer / debounce
    → harness master
         → router (reglas + LLM barato)
         → especialista (AgentResult)
         → tools (HTTP/MCP/Qdrant)
         → guardrails / invariantes
    → messenger → WhatsApp
    → CRM PHP (MySQL) para inbox, outbox, demanda, competencia…
```

Principios que **no deben romperse** al hablar de Lang*:

1. El orquestador no habla con el cliente; hablan los especialistas (o el código
   determinista).
2. Formato de productos, menú y dinero: **código**, no prompt.
3. Checkout / cobertura: FSM determinista.
4. Sandbox espejo de la raíz.
5. Evals e invariantes nacidos de incidentes reales.

Cualquier propuesta LangChain/LangGraph que viole eso es un antipatrón aquí.

---

## 4. Mapeo: “ya lo tenemos” vs “Lang* lo empaqueta”

| Capacidad | Implementación actual | Pieza Lang* cercana | ¿Migrar? |
|---|---|---|---|
| Clasificar intención | `harness/router.py` | LangGraph conditional edges / classifier chain | No prioritario |
| Orquestar turno | `harness/master.py` | LangGraph | Solo flujos nuevos complejos |
| Tools de catálogo | `app/tools/*` + MCP | LangChain Tools | Bajo ROI |
| RAG productos | Qdrant + embeddings worker | LangChain retriever | Opcional (wrapper) |
| Memoria lead | CRM + `memory` | LangChain memory | No necesario |
| Observabilidad | `observability`, traces | **LangSmith** | Sí, como capa |
| Evals | `evals/` + pytest | LangSmith datasets | Complementario |
| Competencia crawl | `competition_*` + watchdog | Job aparte (no LangFlow) | Mantener en código |
| Prototipo visual | — | LangFlow | Solo lab |

---

## 5. Escenarios de adopción (de menos a más invasivo)

### Escenario A — Solo LangSmith (recomendado para estudiar e integrar)

```text
FastAPI actual ──traces──► LangSmith
     │
     └── sin cambiar router/master/tools
```

**Estudias:** tracing, datasets, online/offline evals, prompt versions.  
**Ganas:** narrativa “observabilidad de agentes” + datos reales de prod.  
**No tocas:** CRM, WhatsApp, checkout.

### Escenario B — LangChain como SDK puntual

Usar LangChain solo para:

- Cliente de embeddings / chat unificado.
- Retriever Qdrant tipado.
- Parsers estructurados en el clasificador LLM.

El harness sigue mandando.

### Escenario C — LangGraph para un subflujo nuevo

Ejemplo candidato (no crítico de venta):

```text
crawl competencia → normalizar → embed → match → reportar huecos
```

Modelado como grafo LangGraph, invocado desde un job del watchdog, **sin**
reemplazar el path de WhatsApp.

### Escenario D — Reescritura total en LangGraph/LangChain (no recomendado)

Alto costo, alto riesgo de regresión en invariantes. Solo tendría sentido en un
producto greenfield, no en este bot con cicatrices de producción documentadas.

### Escenario E — LangFlow

Usar en paralelo para talleres internos. Nunca como dueño del webhook de Meta.

---

## 6. Escalado a la nube (GCP / AWS / Azure)

### 6.1 Idea central

**La nube hospeda infraestructura.**  
**Lang* es capa de aplicación / observabilidad.**  
Ningún proveedor “incluye LangGraph de fábrica” como reemplazo de tu agente.

```text
                    Internet
                       │
              WhatsApp Cloud API
                       │
              ┌────────▼────────┐
              │ API Gateway /   │
              │ Load Balancer   │
              └────────┬────────┘
                       │
         ┌─────────────▼─────────────┐
         │ Servicio agente (container│
         │ FastAPI ± LangGraph/Chain)│
         └──────┬──────────┬─────────┘
                │          │
         ┌──────▼───┐  ┌───▼────────┐
         │ Redis    │  │ Vector DB  │
         │ (cola)   │  │ (Qdrant /  │
         └──────────┘  │ managed)   │
                       └─────┬──────┘
                             │
                    ┌────────▼────────┐
                    │ CRM PHP + MySQL │
                    │ (hosting o DB   │
                    │  gestionada)    │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ LangSmith SaaS  │
                    │ (traces/evals)  │
                    └─────────────────┘
```

### 6.2 Equivalencias por proveedor

| Necesidad | GCP | AWS | Azure |
|---|---|---|---|
| Contenedor del agente | Cloud Run o GKE | ECS Fargate o EKS | Container Apps o AKS |
| Secretos | Secret Manager | Secrets Manager | Key Vault |
| Redis / cola | Memorystore for Redis | ElastiCache | Azure Cache for Redis |
| Vectores | Vertex AI Vector Search **o** Qdrant en GCE/GKE | OpenSearch Serverless / Qdrant en ECS | AI Search / Qdrant |
| Jobs periódicos (crawl, embeddings) | Cloud Scheduler + Cloud Run Jobs | EventBridge Scheduler + ECS/Lambda | Azure Scheduler / Container Apps Jobs |
| Logs/métricas infra | Cloud Logging / Monitoring | CloudWatch | Azure Monitor |
| Observabilidad LLM | **LangSmith** (SaaS) + métricas propias | igual | igual |
| MySQL CRM | Cloud SQL | RDS | Azure Database for MySQL |
| Objectos estáticos / media CRM | Cloud Storage | S3 | Blob Storage |

### 6.3 Qué se mueve y qué no

| Componente | ¿A la nube? | Notas |
|---|---|---|
| Agente FastAPI | Sí (contenedor) | Mismo `Dockerfile` de hoy |
| Redis inbound | Sí (gestionada) | Ya contemplado en el diseño durable |
| Qdrant | Sí (VM/K8s o managed vector) | El índice RAG sigue siendo crítico |
| Worker embeddings | Sí (job + cola) | Tick independiente del webhook |
| Crawl competencia | Sí (job programado) | No en el request del CRM |
| CRM PHP | Opcional | Puede quedarse en hosting Don Regalo; el agente solo necesita `CRM_BASE_URL` |
| MySQL CRM | Opcional migrar | Migraciones `crm/sql/*` siguen siendo la fuente de verdad de esquema |
| LangSmith | SaaS externo | El agente envía traces; no hace falta VPC al inicio |
| LangFlow | No en el path crítico | Lab aparte |

### 6.4 Variables de entorno típicas en la nube

Además de las actuales (`.env.example`):

```text
# Runtime
CRM_MODE=external
CRM_BASE_URL=https://…
CRM_INTERNAL_TOKEN=…
AGENT_INTERNAL_TOKEN=…
REDIS_URL=redis://…
QDRANT_URL=…
OPENAI_API_KEY=…   # o proveedor cloud (Vertex/Bedrock/Azure OpenAI)

# Observabilidad LangSmith (si se adopta Escenario A)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=…
LANGCHAIN_PROJECT=don-regalo-prod

# Competencia / jobs
COMPETITION_CRAWL_ENABLED=1
WATCHDOG_ENABLED=1
EMBEDDING_WORKER_ENABLED=1
```

En GCP/AWS/Azure esas claves viven en el **secret manager**, no en el repo.

### 6.5 Patrones de escala (estudia estos términos)

1. **Stateless containers + estado externo**  
   El pod del agente no guarda conversación en disco; estado en Redis/MySQL/Qdrant.

2. **Webhook rápido, trabajo lento aparte**  
   Meta exige respuesta rápida → cola (ya la tienes) → workers.

3. **Separar paths**  
   - Path A: WhatsApp (latencia y disponibilidad).  
   - Path B: jobs (crawl, embeddings, reportes).  
   No mezclar un crawl de 3 minutos dentro del webhook.

4. **Circuit breakers y degradación**  
   Ya existen (`app/resilience`). En la nube siguen siendo válidos frente a
   OpenAI, CRM, Qdrant, Meta.

5. **Multi-región (avanzado)**  
   Solo cuando haya requisito real; WhatsApp y el CRM suelen anclar a una
   región. No es el primer paso.

---

## 7. Diseño de referencia “híbrido” (estudio)

Si en el futuro se combina ecosistema Lang* + nube sin tirar el repo:

```text
                    [LangFlow: solo laboratorio]
                              │
                              ✗ no despliega a prod
                              │
[Meta] → [Cloud Load Balancer] → [Cloud Run / Fargate: FastAPI]
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              [Router+Master]   [LangGraph job]   [LangSmith]
              harness actual     (competencia /      traces
              (WhatsApp)          research)         evals
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
   [Redis]     [Qdrant]    [CRM PHP/MySQL]
```

Contrato mental:

- **WhatsApp path** = harness actual (determinista + LLM acotado).  
- **Research/batch path** = candidato a LangGraph.  
- **Observabilidad LLM** = LangSmith.  
- **Infra** = GCP/AWS/Azure.

---

## 8. Comparativa rápida para el CV / entrevistas

Puedes explicar con honestidad:

> “El agente en producción es una orquestación tipo LangGraph hecha a medida
> (router + especialistas + estado), con RAG sobre Qdrant, tools/MCP, cola
> Redis, guardrails e invariantes. LangChain sería un SDK opcional; LangSmith
> la capa de observabilidad/evals; LangFlow no está en el camino crítico. El
> despliegue a GCP/AWS/Azure es contenedorizar ese runtime y externalizar
> Redis, secretos y jobs — no reescribir el negocio en un builder visual.”

Palabras clave técnicas (sin inventar métricas):

`Python` `FastAPI` `OpenAI` `function calling` `RAG` `Qdrant` `embeddings`
`Redis` `circuit breaker` `webhook` `WhatsApp Cloud API` `PHP` `MySQL`
`MCP` `evals` `LangGraph (conceptualmente)` `LangSmith (observabilidad)`
`Cloud Run / ECS / Container Apps`

---

## 9. Plan de estudio sugerido (orden)

1. **LangChain (2–3 días):** Runnables, tools, retrievers. Arma un notebook
   mínimo: “pregunta → embed → Qdrant → respuesta” **sin** WhatsApp.
2. **LangGraph (3–5 días):** Tutorial oficial del grafo con estado; redibuja
   en papel el `router → specialist` de este repo como nodos.
3. **LangSmith (1–2 días):** Instrumenta un script local; sube 10 traces;
   crea un dataset con 5 casos del corpus `evals/`.
4. **LangFlow (medio día):** Replica el clasificador visualmente; anota por
   qué no lo pondrías en prod.
5. **Nube (3–5 días):** Despliega el `Dockerfile` actual en **un** proveedor
   (Cloud Run o Fargate o Container Apps), con secretos y una URL HTTPS;
   apunta un webhook de prueba (no producción).

Entregable de estudio (para ti): un diagrama de una página + este archivo
anotado con “qué probaría / qué no tocaría en Don Regalo”.

---

## 10. Anti-patrones (para no tropezar)

1. **Meter el menú o los precios en un prompt LangChain** — ya se rompió tres
   veces; el repo lo prohíbe de facto.
2. **Usar LangFlow como orquestador de WhatsApp** — sin outbox claim, sin
   buffer, sin modo HUMAN.
3. **Reemplazar el router híbrido por un AgentExecutor “autónomo”** — sube
   costo y pierde confianza en reglas.
4. **Creer que “estar en AWS” implica LangChain** — son ejes ortogonales.
5. **Duplicar evals**: corpus local + LangSmith sin criterio. Mejor: corpus
   sigue siendo la red de regresión; LangSmith añade traces de prod.
6. **Ignorar el espejo `sandbox/`** si algún día se integra código Lang* en
   `app/`: CI sigue exigiendo sincronía.

---

## 11. Glosario corto

| Término | Significado |
|---|---|
| Runnable | Pieza componible LangChain (`invoke`/`ainvoke`) |
| Graph / StateGraph | Modelo LangGraph de nodos y estado |
| Checkpoint | Snapshot del estado del grafo para reanudar |
| Trace | Árbol de spans de una ejecución LLM/tools |
| Dataset | Conjunto de ejemplos para evals |
| Retriever | Interfaz RAG “query → documentos” |
| Tool calling | El LLM elige funciones; el runtime las ejecuta |
| Human-in-the-loop | Pausar el grafo hasta input humano (≈ handoff CRM) |
| Managed service | Redis/DB/vector operados por el cloud provider |

---

## 12. Referencias internas del repo

- Arquitectura del harness: [`docs/HARNESS.md`](HARNESS.md), [`ARQUITECTURA.md`](../ARQUITECTURA.md)
- Observabilidad propia: [`docs/OBSERVABILIDAD.md`](OBSERVABILIDAD.md)
- Resiliencia / breakers: [`docs/RESILIENCIA.md`](RESILIENCIA.md)
- Cola Redis: [`docs/COLA_DURABLE_REDIS.md`](COLA_DURABLE_REDIS.md)
- RAG productos: [`docs/RAG_PRODUCTOS.md`](RAG_PRODUCTOS.md)
- Inteligencia comercial / competencia: [`crm/docs/INTELIGENCIA_COMERCIAL.md`](../crm/docs/INTELIGENCIA_COMERCIAL.md)
- Playbook de agente: [`docs/PLAYBOOK_AGENTE_IA.md`](PLAYBOOK_AGENTE_IA.md)

Documentación externa (para profundizar; URLs oficiales del ecosistema):

- LangChain: https://python.langchain.com/
- LangGraph: https://langchain-ai.github.io/langgraph/
- LangSmith: https://docs.smith.langchain.com/
- LangFlow: https://docs.langflow.org/

---

## 13. Resumen ejecutivo

| Pregunta | Respuesta corta |
|---|---|
| ¿Hace falta LangChain para este bot? | No. Ya hay tools, RAG y orquestación. |
| ¿Qué pieza Lang* aporta más? | **LangSmith** (traces/evals) sin reescritura. |
| ¿Dónde encaja LangGraph? | Subflujos nuevos tipo grafo; no el checkout. |
| ¿Y LangFlow? | Laboratorio / demos, no producción WhatsApp. |
| ¿Escalar a GCP/AWS/Azure? | Contenedor + Redis + secretos + jobs; Lang* es opcional encima. |
| ¿Orden sensato? | Nube del runtime actual → LangSmith → (opcional) LangGraph en batch. |

Este documento es la base para estudiar el ecosistema **en el contexto de un
agente real en producción**, no como tutorial genérico desconectado del negocio.
