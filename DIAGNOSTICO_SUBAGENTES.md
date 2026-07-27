# Revisión de los subagentes (especialistas del harness)

Foco: los especialistas a los que el orquestador delega y la mecánica de delegación.
Fecha: 27/07/2026.

## Estado de aplicación

Las recomendaciones de este informe quedaron convertidas en contratos ejecutables:

- `escalate` es determinista también durante el pago.
- Las intenciones desconocidas caen en `concierge`; los contextos comerciales
  explícitos siguen en `catalog`.
- `AgentSpec` declara tier de modelo, rondas, presupuesto de herramientas,
  paralelismo y política de salida.
- Catálogo ejecuta como máximo una herramienta total e inyecta condicionalmente
  campañas, fúnebres y atributos.
- `evals/corpus/specialists.yaml` agrupa regresiones por especialista.
- Tracking/PII, inyección indirecta y coste por agente ya estaban cubiertos y se
  mantienen como regresiones.

Especialistas registrados (`harness/registry.py`): `concierge, catalog, detail,
coverage, checkout, policy, tracking, escalate`. El orquestador clasifica la intención
(router: reglas + LLM barato) y enruta en código con `spec_for(intent)`; no hay un LLM
que decida llamar a otros LLM.

---

## Lo que está bien y conviene NO cambiar

- **La delegación es clasificación + routing en código, no orquestación agéntica por
  LLM.** Se evita el anti-patrón "un agente decide llamar a otros agentes en un bucle":
  aquí cada turno toca **un** especialista, elegido por un clasificador barato. Esto
  acota latencia, coste y hace la traza legible. Es la decisión correcta.
- **Least-privilege por especialista.** Cada `AgentSpec` declara un `tool_names` mínimo;
  `test_prompts_architecture.py` obliga a que el playbook solo cite tools de su toolset.
- **Lo crítico no es un subagente LLM.** Cobertura y cierre son deterministas
  (`deterministic=True`): su "playbook" es documentación, no llega a ningún modelo. El
  dinero, la FSM del cierre y el saludo salen de código.
- **Cada llamada de especialista va tras su circuit breaker** (`openai.specialist`),
  igual que el router (`openai.router`) y las tools (catálogo, qdrant, mcp, crm).
- **`detail` ya no depende de que el modelo recuerde llamar la tool:** `_handle_detail`
  precarga `GET /productos/{id}` y le inyecta el contenido como hecho del sistema.

---

## Recomendaciones por especialista

### `concierge` (greet / small_talk / fuera de alcance) — sin tools
Barato y correcto. **Recomendación:** hacerlo el **fallback de intención desconocida**.
Hoy `spec_for()` cae en `catalog` ante un intent no mapeado, así que un mensaje que el
router no entiende termina buscando productos (y llamando tools). `concierge` —sin tools,
con el CORE de seguridad— es un default más seguro para lo verdaderamente ambiguo.

### `catalog` (catalog_search) — el punto más denso
Es, con diferencia, el playbook más largo (~155 líneas: taxonomía, imágenes, selección de
tool, campañas, fúnebres, honestidad de atributos, personalización, formato de salida).
Dos observaciones de escalabilidad:

- **Playbook mega-monolítico → tokens/latencia/coste en cada turno de catálogo, y dilución
  de instrucciones.** Buena parte ya es determinista fuera del prompt (numeración del menú,
  render del listado). El resto sigue siendo enorme. **Recomendación:** inyectar
  condicionalmente los bloques de nicho —fúnebres, campañas de temporada, honestidad de
  color/flor— solo cuando el turno los active, en vez de mandarlos siempre. El camino
  común (buscar y mostrar) no necesita las reglas de luto en el contexto.
- **"UNA sola tool por turno" es solo prompt, no está impuesto.** El loop manda
  `parallel_tool_calls=True` + `tool_choice="auto"`, así que el modelo puede disparar
  varias. Si el single-tool es una garantía real de latencia, **imponerla** (para catálogo:
  `parallel_tool_calls=False` o un tope de llamadas por round), no confiar en la redacción.

### `detail` (product_detail) — bien resuelto
Con la precarga determinista del contenido, cierra la deuda de "¿qué contiene?". Playbook
lean. **Recomendación:** mantener. Al leer sobre todo hechos precargados, es candidato a
un **modelo más barato/rápido** (ver modelo-por-agente abajo).

### `coverage` / `checkout` (deterministas) — sin LLM
Correctos. Riesgo único: el playbook-documentación puede **divergir del código** con el
tiempo (nadie lo ejecuta, así que nada lo verifica). **Recomendación:** que la verdad de
comportamiento viva en los tests (`test_harness`, `test_checkout_*`) y tratar esos
playbooks explícitamente como comentarios, no como spec.

### `policy` (policy_faq) — la superficie de inyección indirecta
Usa `buscar_conocimiento_equipo` (RAG sobre respuestas del equipo). Ese contenido es
**influenciable** si el KB ingiere texto de clientes: una instrucción incrustada podría
intentar secuestrar la respuesta. El playbook ya pide "usa solo la parte genérica", pero
eso es prompt. **Recomendación:** cubrirlo con los evals adversariales (inyección
**indirecta** vía resultado de tool) — es el caso B1 del informe de escalabilidad
aplicado justo a este especialista.

### `tracking` (track_order) — least-privilege de datos
Pide email + código antes de `rastrear_pedido` y solo informa de ESE pedido. Bien. Pero
"pedir antes de llamar" es prompt. **Recomendación (defensa en profundidad):** validar en
el executor que ambos parámetros llegan antes de pegarle a la API, y confiar en que el
servidor devuelve solo el pedido que casa con esos datos (no fiarse del modelo para el
filtrado de PII).

### `escalate` (derivación) — dos caminos para una acción
La derivación normal es determinista (`_handle_escalate` → `perform_handoff`). Pero el
camino de **pago** corre `_run_specialty("escalate", …)` para que el modelo llame la tool,
con un fallback en código si no lo hace. Es decir: la derivación ya está garantizada por
código, y aun así se invoca un LLM que puede fallar. **Recomendación:** hacer `escalate`
**totalmente determinista** (nunca LLM). Elimina una llamada a OpenAI, un modo de fallo y
un camino duplicado; el mensaje de espera ya lo emite `perform_handoff`.

---

## Recomendaciones transversales de la capa de subagentes

1. **Modelo por especialista (tiering).** Hoy todos los de cara al cliente usan
   `settings.openai_model`. `concierge` (charla) y `detail` (lee hechos precargados)
   rendirían igual con un modelo más barato/rápido; `catalog` (razona sobre taxonomía) es
   el que justifica el modelo fuerte. Un `model` por `AgentSpec` es una palanca directa de
   coste y latencia a escala.

2. **`MAX_TOOL_ROUNDS` es global (4).** `detail`/`policy`/`tracking` raramente necesitan
   cuatro rondas; catálogo puede. Un tope por especialista acota el peor caso de latencia
   y de coste por turno.

3. **Evals organizados por especialista.** El corpus cubre routing/replies/handoff, pero
   no hay un set de regresión **por subagente**. Recomendación: una carpeta por
   especialista (catalog: honestidad de taxonomía + `aproximado`; detail: contenido;
   policy: no-inventar + handoff; tracking: barrera de PII). Cada bug de un especialista
   deja su caso donde se ve.

4. **Presupuesto observable por especialista.** Emitir en la traza `agent` + tokens +
   latencia por turno (ya hay `agent` y `latency_ms`; falta tokens). Permite ver qué
   especialista domina el coste — casi seguro `catalog`— y priorizar su recorte.

5. **Contrato de salida uniforme, entradas asimétricas.** Todos devuelven `AgentResult`
   (bien). Pero `catalog`/`detail` reciben post-proceso (`compose_product_reply`,
   `dedupe_artifacts`, `_capture_choice`) y los demás no. Está justificado, pero conviene
   documentar en `registry.py` qué especialistas pasan por qué post-proceso, para que
   añadir el próximo no herede sorpresas.

---

## Prioridad sugerida

| | Recomendación | Por qué |
|---|---|---|
| 1 | `escalate` 100% determinista | Quita un LLM y un camino duplicado de una acción ya garantizada por código |
| 2 | Fallback de intención desconocida → `concierge` | Evita que lo ambiguo acabe buscando productos |
| 3 | Imponer single-tool en `catalog` (o medirlo) | La garantía de latencia hoy es solo aspiracional |
| 4 | Evals por especialista + inyección indirecta en `policy` | Regresión de comportamiento y de seguridad donde falta |
| 5 | Modelo por `AgentSpec` + tokens en la traza | Palancas de coste/latencia al crecer el volumen |
| 6 | Trocear el playbook de `catalog` (inyección condicional) | Menos tokens y menos dilución en el camino común |

---

## Cierre

La capa de subagentes está **bien diseñada**: delegación determinista, toolsets mínimos,
lo crítico fuera del LLM y contrato de salida uniforme. Las mejoras no son estructurales:
**simplificar `escalate`, endurecer el default de intención, imponer lo que hoy solo pide
el prompt, y abrir visibilidad de coste por especialista.** Con eso, añadir el próximo
especialista (o subir el volumen) no degrada ni el coste ni la trazabilidad de lo que ya
funciona.
