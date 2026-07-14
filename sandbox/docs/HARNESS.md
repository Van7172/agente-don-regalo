# Harness engineering

Cómo está organizado el agente: un orquestador que delega, especialistas con su
propio contexto, y estado explícito.

## Un turno

```
percibir → clasificar → delegar → reducir → persistir
```

```
Webhook Meta → buffer
                 │
                 ▼
        ORQUESTADOR  (harness/master.py)
        único que escribe estado · no habla con el cliente
                 │
   ┌─────────────┼──────────────┐
   ▼             ▼              ▼
 router      especialistas    políticas
 (intención) (registry.py)   (policies.py)
                 │
                 ▼
              tools → Renderer → WhatsApp
```

## Reglas

**1. El orquestador no habla con el cliente.** Todo texto de cara al cliente sale
de un especialista, incluidos los saludos (los atiende `concierge`). Por eso el
prompt del orquestador (`prompts/playbooks.ORCHESTRATOR`) no lleva identidad, ni
estilo, ni reglas de producto: solo la taxonomía de intenciones.

**2. Los especialistas devuelven `AgentResult`, nunca un `str`.** Traen lo que
dirán al cliente *y* lo que aprendieron: `state_patch` y `artifacts` (los
productos que las tools citaron de verdad). El orquestador reduce el estado con
eso. Antes devolvían texto suelto y el master intentaba recuperar los ids de
producto con una regex sobre la prosa — que nunca casaba, así que `excluir_ids`
no se enviaba jamás y el resumen del pedido decía "Producto elegido".

**3. El system message se compone por capas**, no se escribe a mano:

```
system(agente) = CORE + FACTS[agente] + PLAYBOOK[agente] + ESTADO
```

- `prompts/core.py` — identidad, estilo y **RESTRICCIONES**. Va en *todos* los
  agentes de cara al cliente, sin excepción.
- `prompts/facts.py` — datos del dominio, inyectados solo a quien los necesita
  (cobertura no necesita los ids de ocasión; catálogo no necesita devoluciones).
- `prompts/playbooks.py` — el procedimiento propio de cada especialista.
- `prompts/compose.py` — el único sitio donde se arma un system message.

**4. Prompt y toolset viven juntos** (`harness/registry.py:AgentSpec`). Estaban en
archivos distintos y nada obligaba a que coincidieran: un playbook podía citar una
tool que su toolset no tenía y el modelo la alucinaba en silencio.

## Los agentes

| Agente | Atiende | Tools | Puede escalar |
|---|---|---|---|
| `concierge` | saludos, cortesía, fuera de alcance | — | no |
| `catalog` | búsqueda de productos, campañas | 10 | **no** |
| `detail` | detalle de un producto ya mostrado | 3 | no |
| `coverage` | distrito y tarifa (determinista) | 2 | no |
| `checkout` | cierre del pedido (FSM determinista) | 3 | sí |
| `policy` | políticas, pagos, objeciones | 4 | sí |
| `tracking` | estado de un pedido | 1 | sí |
| `escalate` | derivación a un asesor | 1 | sí |

`catalog` **no puede escalar** a propósito: buscar productos nunca es motivo de
handoff. Ahí es donde el modelo mandaba a un asesor ventas sanas ("regalos
corporativos por Fiestas Patrias").

## La capa de adaptadores

La API de donregalo.pe devuelve **tres formas distintas de producto** (listados
con `nombre_producto`/`precio_final`, detalle con `nombre`/`precio`/`imagenes[]`,
y Qdrant con `nombre`/`precio`) más una cuarta para distritos (`nombre_distrito`,
`tarifa_envio_distrito`). Nadie adaptaba entre ellas: quien tapaba la diferencia
era el LLM, leyendo el JSON crudo y adivinando los campos.

Donde el código sí asumía una forma, fallaba en silencio:

- `match_district` buscaba `nombre` y la API devuelve `nombre_distrito`, así que
  **ningún distrito hizo match nunca**: todo cliente que preguntaba "¿llegan a
  Miraflores?" recibía "búscalo en Google Maps".
- `/categorias/{slug}/productos` anida los productos bajo `data.productos`, y no
  se extraían.

`tools/adapters.py` normaliza todo a una forma canónica en la frontera de las
tools. Nada aguas abajo vuelve a ver el JSON crudo.

**El dinero se calcula en código.** La API entrega todo en USD. Antes el modelo
llamaba a `tipo_cambio` y multiplicaba él mismo cada precio — aritmética de dinero
a cargo de un LLM, en un prompt que a la vez le prohíbe inventar precios. Ahora el
adaptador convierte y `tipo_cambio` **ya no es una tool de ningún agente**: los
productos llegan con `precio_sol` y `precio_usd`, y el playbook solo los copia.

Los tests de `test_adapters.py` corren contra **payloads reales grabados** en
`tests/fixtures/api/`. El test viejo de cobertura inventaba la forma del payload y
por eso pasaba en verde mientras la función estaba rota en producción.

## Qué es determinista y qué es LLM

Cobertura y cierre **no llaman al LLM**: son `harness/coverage.py` y
`harness/checkout.py`. Las reglas de negocio (cuándo procede un handoff, qué
productos no repetir, si un precio salió de una tool) son funciones puras en
`harness/policies.py` — se testean sin red y en milisegundos.

## Evals: la red de regresión

Cada turno del orquestador emite un `Trace` (intención, agente, tools, ids de
producto, handoff, violaciones, latencia) como una línea JSON en el log.

Las **invariantes** (`harness/invariants.py`) son funciones puras sobre
`(estado, respuesta, artifacts)`. Cada una nació de un incidente real:

| Invariante | El incidente |
| --- | --- |
| `no_cash_on_delivery` | el bot ofreció pago contra entrega (y PSE, que es colombiano) |
| `image_urls_on_own_line` | las fotos llegaron como links al perderse el formato |
| `prices_are_sourced` | el modelo calculaba los soles de cabeza |
| `no_repeated_products` | "otras opciones, no esas" y repetía las mismas |
| `no_duplicates_within_reply` | el mismo producto dos veces en un paquete |

Se evalúan en runtime (van a la traza) y en el corpus (`evals/corpus/*.yaml`).

```bash
python -m evals.runner      # informe legible
pytest tests/test_evals.py  # el mismo corpus, en CI
```

El corpus es determinista: no llama a OpenAI ni a la API, así que corre en
milisegundos. Cubre enrutado, invariantes de respuesta y política de handoff.

**Regla de trabajo: cada bug arreglado deja un caso en el corpus.** En su primera
ejecución ya cazó dos bugs vivos del router — "¿Dónde está mi pedido?" no casaba
con el patrón de rastreo porque los patrones estaban escritos sin tildes, y "Todo
en orden hoy" acababa en el catálogo buscando productos para alguien que no pedía
nada.

## Invariantes con test

- `test_prompts_architecture.py` — todo agente de cara al cliente lleva las
  RESTRICCIONES; el orquestador no lleva CORE ni tools; ningún playbook cita una
  tool fuera de su toolset. **Este archivo existe porque la regresión ya ocurrió**:
  un commit compuso el prompt solo con el playbook y el bot corrió en producción
  sin reglas de privacidad ni defensa anti-manipulación.
- `test_harness_contracts.py` — el lazo de estado: los ids salen de las tools, no
  se repiten productos, el resumen nombra el producto real.
- `test_master_e2e.py` — un turno completo con OpenAI y tools simuladas.

## Pendiente

- **Trazas y corpus de evals** (fase 4 del plan): un `Trace` por turno y un runner
  de replay con conversaciones reales. Cada bug arreglado debería dejar un caso.
- **Estado en tabla propia del CRM** (fase 5): hoy va como blob JSON en `settings`,
  sin versión ni bloqueo optimista. Dos flushes concurrentes se pisan.
- **Router híbrido**: hoy `classify_intent` es solo reglas y su caso por defecto es
  `catalog_search`. Falta el clasificador LLM de respaldo con confianza.
