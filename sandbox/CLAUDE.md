# CLAUDE.md

Guía para agentes (y personas) que tocan este repo. Lee esto antes de cambiar nada:
casi todo lo de aquí se aprende a base de tropezar, y ya tropezamos por ti.

## Qué es

Agente de WhatsApp para **Don Regalo** (donregalo.pe), delivery de regalos en Lima.
El cliente escribe por WhatsApp → Meta Cloud API → webhook → un orquestador clasifica
la intención y delega en un especialista → responde por Cloud API. Los asesores
humanos atienden desde un CRM PHP.

Pila: FastAPI (Python 3.11), OpenAI, Qdrant, MySQL (CRM PHP), SQLite (local/tests).

## La regla que más se olvida: sandbox/ es un espejo

`sandbox/app`, `sandbox/tests` y `sandbox/evals` son una **copia exacta** de la raíz
(`app/`, `tests/`, `evals/`). La fuente de verdad es la raíz. **Cada cambio en la raíz
hay que reflejarlo en `sandbox/`** o divergen — ya pasó. Tras editar:

```bash
rm -rf sandbox/app sandbox/tests sandbox/evals
mkdir -p sandbox/app sandbox/tests sandbox/evals
cp -r app/. sandbox/app/ && cp -r tests/. sandbox/tests/ && cp -r evals/. sandbox/evals/
diff -rq app sandbox/app --exclude=__pycache__   # debe decir "espejo OK"
```

(Sí, mantener dos copias idénticas es frágil. Borrar `sandbox/` está sobre la mesa,
pero mientras exista, se sincroniza.)

## Arquitectura del harness

Un turno = **percibir → clasificar → delegar → reducir → persistir**
([`app/harness/master.py`](app/harness/master.py)).

- **El orquestador NO habla con el cliente.** Clasifica y delega. Todo texto de cara
  al cliente sale de un especialista, incluidos los saludos (los atiende `concierge`).
- **Los especialistas devuelven `AgentResult`, nunca `str`** ([`contracts.py`](app/harness/contracts.py)):
  traen lo que dicen Y lo que aprendieron (`artifacts`, `state_patch`). Los ids de
  producto salen de los resultados de las tools, jamás de una regex sobre la prosa.
- **El system message se compone por capas** ([`prompts/compose.py`](app/prompts/compose.py)):
  `CORE + FACTS[agente] + PLAYBOOK[agente] + ESTADO`. El CORE (identidad, estilo y
  **RESTRICCIONES de seguridad**) va en TODOS los agentes de cara al cliente. Un
  commit lo compuso una vez solo con el playbook y el bot corrió sin reglas de
  privacidad ni seguridad; `test_prompts_architecture.py` lo impide ahora.
- **Prompt y toolset viven juntos** ([`registry.py`](app/harness/registry.py):`AgentSpec`).
  Agentes: `concierge, catalog, detail, coverage, checkout, policy, tracking, escalate`.

**Determinista, sin LLM:** cobertura ([`coverage.py`](app/harness/coverage.py)),
cierre ([`checkout.py`](app/harness/checkout.py), una FSM), políticas de negocio
([`policies.py`](app/harness/policies.py)) y el saludo de bienvenida. `coverage` y
`checkout` están marcados `deterministic=True`: sus playbooks/tools NO los ve ningún
modelo, el orquestador los resuelve en código.

**Router híbrido** ([`router.py`](app/harness/router.py)): reglas primero (rápidas,
con confianza); por debajo de `CONFIDENCE_FLOOR` decide un clasificador LLM barato.
Si el LLM falla, mandan las reglas — nunca tumba un turno.

## Las tres cosas que se rompieron y NO hay que repetir

1. **El formato de productos NO va en el prompt.** Lo compone el código
   (`master.compose_product_reply` + `render.py`). Se intentó por prompt tres veces;
   cada desvío del modelo llegaba al cliente como un muro de enlaces en vez de fotos.
   El emisor de WhatsApp solo convierte en foto una línea que es **solo** una URL.
2. **La API devuelve tres formas distintas de producto** (listado, detalle, Qdrant) y
   una cuarta de distrito, todo en **USD**. Todo pasa por
   [`tools/adapters.py`](app/tools/adapters.py), que normaliza a una forma canónica y
   convierte a soles. `tipo_cambio` NO es tool de ningún agente: el dinero lo calcula
   el adapter, no el LLM. El contrato oficial de la API está en [`API.md`](API.md);
   el slug de categoría es **siempre `url_categoria`** (no `categoria_url`).
3. **Si el cliente nombra una categoría, es un límite duro.** La API manda; solo si no
   tiene nada de esa categoría entra Qdrant, y esos productos van marcados
   `aproximado: true` para que el bot diga que son alternativas
   ([`executor.enforce_category`](app/tools/executor.py)).

## Evals: la red de regresión

Cada bug arreglado deja un caso en el corpus. Es lo que impide que el parche de hoy
rompa el de la semana pasada.

```bash
python -m evals.runner            # corpus determinista, sin red
python -m evals.runner --llm      # el clasificador LLM (llama a OpenAI; RUN_LLM_EVALS=1 para el test)
```

Invariantes de respuesta en [`harness/invariants.py`](app/harness/invariants.py)
(cada una nació de un incidente real: contraentrega, URLs pegadas al texto, precios
inventados, productos repetidos). Se evalúan en runtime (van a la `Trace`) y en el corpus.

## Tests

```bash
python -m pytest tests/ -q       # 281 pasan, offline
```

**La suite NO sale a la red.** [`tests/conftest.py`](tests/conftest.py) fuerza
`crm_enabled=False` y stub de `/productos/activos`. El `.env` de dev trae
`CRM_MODE=external`; sin el conftest, `load_state` haría HTTP real y el filler de
0.7s del agente ensuciaría las aserciones (fallos intermitentes según el orden).

## Flujo de trabajo

- Trabaja en la raíz, **sincroniza `sandbox/`**, corre `pytest` + `evals.runner`.
- No subas a GitHub salvo que se pida. Remoto: `Van7172/agente-don-regalo`, rama `main`.
- Verifica contra la **API real** cuando toques catálogo/cobertura/precios — varias
  veces el código asumía una forma que la API no devuelve, y el test lo tapaba con un
  mock inventado. `https://donregalo.pe/clienteApiApp/api`.

## Despliegue (son DOS)

- **Agente**: EasyPanel, servicio `agente-donregalo`. `uvicorn app.main:app`. Despliega
  desde GitHub — el código en local no llega a producción hasta hacer push Y redeploy.
  Webhook Meta: `.../whatsapp/webhook`. Env clave: `WHATSAPP_APP_SECRET` (valida la
  firma — sin él cualquiera inyecta mensajes), `WATCHDOG_ENABLED=1`, `ALERT_WHATSAPP`.
- **CRM PHP**: hosting de Don Regalo, carpeta [`crm-php/`](crm-php/). El verde de venta
  cerrada, el sonido del handoff y los emojis viven aquí — hay que subir el CRM aparte.

## Deuda conocida (del servidor, no de este código)

- ~~`GET /productos/{id}` devolvía un error fatal de PHP con status 200 para
  productos con imagen `null` (id 1235).~~ **Corregido en producción (jul 2026):**
  devuelve JSON; con `imagen_url: null` la foto sale de `imagenes[]` y el adapter la
  recupera. Verificado de punta a punta.
- ~~La API se autocontradecía: categorías usaban `url_categoria`, productos
  `categoria_url`.~~ **Corregido en producción (jul 2026):** ahora es `url_categoria`
  en todas partes (API.md nota #4). El adapter lee ese campo; mantiene `categoria_url`
  solo como respaldo histórico.
