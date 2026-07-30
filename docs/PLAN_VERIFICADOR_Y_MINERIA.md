# Plan: verificador de utilidad y minería de conversaciones

Dos capas de mejora continua sobre el harness, diseñadas el **30-07-2026** y
**pendientes de implementar**. Son independientes: cada una vale por sí sola.

Nada de esto existe todavía en el código. Lo que sí existe y hay que reusar está
enlazado en cada punto.

---

## Parte 1 — Agente verificador de utilidad

### Qué es y qué no es

No es un verificador de reglas: eso ya existe y es determinista
([`app/guardrails/`](../app/guardrails/), invocado en
[`master.py:301`](../app/harness/master.py#L301) con 9 invariantes, 10 reglas
bloqueantes y 7 que además ceden el chat).

Esto es un **juez de utilidad**: un LLM que se pone en el rol del cliente y
dictamina si la respuesta le sirve. Cubre lo que las invariantes no ven — una
respuesta puede cumplirlas todas y no resolver nada.

### Encaje en la jerarquía

El orquestador **no es el vendedor**: no produce prosa, clasifica y delega
([`master.py:283`](../app/harness/master.py#L283)). El verificador lo llama
`master` después de `_handle` y juzga la salida del **especialista**. Eso
mantiene a `master` como el único sitio que decide.

### Rúbrica: reusar la que ya existe

[`evals/judge.py:31`](../evals/judge.py#L31) ya define los tres criterios:
`resolvio`, `tono`, `sin_inventar` (0-2 cada uno). **Reusar literal.** Dos
definiciones de "buena respuesta" —una runtime y otra offline— es el peor
resultado: cuando discrepen, no se sabrá cuál está mal.

### Contrato

`Verdict` en [`contracts.py`](../app/harness/contracts.py), al lado de `Decision`:

```
resolvio: int      # 0-2
tono: int          # 0-2
sin_inventar: int  # 0-2
instruccion: str   # qué hacer distinto, en IMPERATIVO OPERATIVO
```

`instruccion` no es una crítica ("tu respuesta fue vaga") sino una orden
("muestra productos en vez de preguntar"). Ver la trampa de fuga más abajo.

El verificador **nunca devuelve prosa para el cliente**.

### Qué hacer con un veredicto malo

**Un reintento como máximo**, con `instruccion` como `extra_system`. Si el
segundo también falla → handoff. Nunca un tercero.

Es la lección de `step_retries` en
[`checkout.py:202`](../app/harness/checkout.py#L202): un paso que no entiende y
se repite igual perdió a una clienta con cuatro mensajes idénticos. Un
verificador sin tope reconstruye ese bucle, pero pagando tokens.

### Cuándo NO ejecutarlo

Aquí está el ahorro y la mitad de la calidad:

- Rutas deterministas: `coverage`, `checkout`, `_answer_menu`, y cualquier texto
  que salga de `render_product_list`. Verificar con un LLM lo que escribió el
  código es verificarse a uno mismo — y el juez marcaría "no resuelve" en las
  preguntas secas del cierre, que son secas a propósito.
- Turnos donde `guard_reply` ya degradó o derivó: la decisión ya está tomada.

Queda aprox. la mitad de los turnos: los de prosa del modelo, que son los que
pueden salir mal.

### Modo de fallo

Timeout o error del verificador → **se envía la respuesta original**. Misma regla
que el router LLM: nunca tumba un turno.

### Dos trampas concretas de este repo

1. **El verificador NO debe llevar CORE.** `test_prompts_architecture.py` exige
   `CORE + FACTS + PLAYBOOK` en todo agente de cara al cliente. El verificador no
   lo es, y darle la identidad de vendedor es exactamente al revés: juzgaría si la
   respuesta suena como él la habría escrito. Necesita una exclusión explícita, o
   se rompe el test o se rompe el verificador.

2. **`instruccion` es un vector de fuga.** Si se pasa como `extra_system` para el
   reintento, el modelo puede citarla: *"Mi respuesta anterior no resolvió
   porque…"* llegando a la burbuja del cliente. Mitigación: redactarla en
   imperativo operativo **y** añadir su marcador a lo que detecta
   `no_internal_context` ([`response.py:103`](../app/guardrails/response.py#L103)).

3. **El "rol de lead" alucina necesidades.** Un LLM al que se le pide simular al
   cliente inventa carencias que el cliente nunca expresó ("no le ofreció
   personalización" — que ni existe en la API). Darle solo `turn.text`, **nunca**
   `turn.quoted`, que es contexto del sistema.

### Despliegue

**Modo sombra primero, dos semanas.** Corre, emite
`donregalo_verifier_verdict{criterio}` y `llm_cost_usd_total{agent="verifier"}`
—el etiquetado por `ContextVar` de
[`observability/llm_usage.py`](../app/observability/llm_usage.py) lo soporta sin
tocar firmas—, y **no actúa**.

Lo que se busca en esos datos es una cifra: **qué porcentaje de turnos habría
bloqueado**. Si pasa del ~10%, o el umbral está mal o el problema son los
playbooks de los especialistas — y ahí reintentar es pagar tokens por tapar la
causa.

Después, promover a bloqueante **solo `resolvio == 0`**. `tono` y `sin_inventar`
quedan observacionales: el tono es subjetivo, y lo inventado ya lo cazan
`prices_are_sourced` y `unsupported_capability_claim` gratis y sin LLM.

### Entregable del primer paso

`app/harness/verifier.py` + `Verdict` en contracts + gate de rutas deterministas
+ tests, **sin tocar el camino de envío**. Reversible con una env var.

---

## Parte 2 — Minería de conversaciones reales

### La joya que ya está en la base y nadie mina

[`crm_ventas_historiales`](../crm/sql/004_sales_history.sql) tiene
`id_conversation`, `fecha_cierre_venta_historial`, `snapshot` JSON y
`estado_venta_historial`. **La etiqueta de resultado ya existe.** Un JOIN contra
[`crm_messages`](../crm/sql/001_crm_schema.sql#L53) parte el universo en
conversaciones que vendieron y conversaciones que no, con SQL puro y coste cero.

Sin etiqueta de resultado, un minero LLM produce observaciones interesantes que
nadie sabe si valen dinero. Con ella produce "este patrón aparece en el 40% de
las que NO cerraron y en el 3% de las que sí". Lo segundo cambia prioridades.

**Ningún LLM da una etiqueta mejor que la venta real.**

### La pieza que falta: hoy no hay dónde minar

`Trace.emit()` en [`trace.py:76`](../app/harness/trace.py#L76) hace
`pop("user_text")`, `pop("state_patch")` y `pop("handoff_reason")` **a
propósito** — deja `input_chars`, `state_patch_keys` y `handoff_reason_present`,
y lo escribe en una línea de log a stdout de EasyPanel.

Consecuencia: el **contenido** está en `crm_messages` y el **diagnóstico**
(intent, router, confidence, violations, prompt_version, checkout_step) está en
un log no consultable, **y no hay forma de unirlos**. Sin ese join el minero
puede decir *qué* dijo el cliente, nunca *por qué* el agente respondió así.

Hace falta una tabla `crm_turn_traces` (migración `013`): conversation_id,
message_id, intent, router, confidence, violations, prompt_version,
checkout_step, latency, tokens. **Sin `user_text`** — ya está en `crm_messages` y
duplicarlo duplica el problema de PII.

### El orden correcto: limpieza primero, LLM último

Un LLM sobre volcados crudos quema tokens en marcadores `[image]`, reenvíos de
outbox y PII, y encima paga por leerlos.

1. **Extracción determinista** — SQL. Conversación + etiqueta de venta + traza.
2. **Limpieza determinista** — ya está escrita:
   `redact_personal_data`, `find_contacts`, `minimize_historical_messages` en
   [`app/guardrails/privacy.py`](../app/guardrails/privacy.py). No escribir otra.
3. **Muestreo estratificado** — aquí está el ahorro. Estratos con señal:
   - **Abandono silencioso**: el último mensaje del bot antes de que el cliente
     no vuelva a escribir. SQL puro sobre `fecha_creacion`. Hoy es
     completamente invisible y es, literalmente, "qué mató la conversación".
     **Empezar por aquí.**
   - Turnos con `violations` no vacío.
   - Handoffs cuyo motivo fue un fallo nuestro.
   - Router por debajo de `CONFIDENCE_FLOOR`.
   - `step_retries >= 1`.
   - DLQ.
4. **LLM solo sobre ese residuo**, y solo para **agrupar y nombrar**. No para
   juzgar (eso es la Parte 1) ni para decidir.

### Qué debe producir — y el riesgo

**Fine-tuning con las salidas del agente: NO, todavía.** El comportamiento de
Regalito vive en código, no en pesos: la numeración del menú, el formato de
productos, la FSM del cierre, los adaptadores. Destilar a pesos hornea los bugs
de hoy en un artefacto que ya no se arregla editando un playbook, y se pierde
justo lo que ha funcionado cinco veces según CLAUDE.md.

**Lo que sí:** el minero **nomina casos candidatos para `evals/corpus/`** y
**reglas deterministas candidatas**. Un humano confirma y el caso entra como test
de regresión. Cierra el bucle con la maquinaria existente en vez de construir una
en paralelo.

### Privacidad

El corpus sale de conversaciones reales con clientes reales:

- Lo que se manda a OpenAI para minar es un flujo de datos **distinto** del
  operativo. Que sea una decisión explícita, no un efecto secundario.
- **El corpus no puede commitearse crudo** a `evals/corpus/`: el repo está en
  GitHub. Pasar por `redact_personal_data` antes de tocar disco, y añadir un test
  que falle si un caso contiene teléfono o correo — `find_contacts` ya hace ese
  trabajo.

### Orden de ataque

Cada paso vale por sí solo aunque no se haga el siguiente:

1. **SQL de abandono silencioso + join con ventas.** Cero LLM, cero infra nueva.
   Primera lista de "mensajes que matan conversaciones" en una tarde.
   **Es el que más va a sorprender.**
2. **`crm_turn_traces`** (migración `013`) para cruzar diagnóstico con contenido.
3. **Extractor + limpieza** reusando `guardrails/privacy`.
4. **El LLM agrupador**, offline y programado, solo sobre los estratos.

---

## Nota de mantenimiento

Este documento no toca `app/`, `tests/` ni `evals/`, así que **no requiere
sincronizar `sandbox/`**. En cuanto se implemente cualquiera de las dos partes,
sí: `python scripts/check_mirror.py --fix`.

Recordar también el orden de despliegue para la Parte 2: **SQL → CRM PHP →
agente**.
