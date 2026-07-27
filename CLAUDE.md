# CLAUDE.md

Guía para agentes (y personas) que tocan este repo. Lee esto antes de cambiar nada:
casi todo lo de aquí se aprende a base de tropezar, y ya tropezamos por ti.

## Qué es

Agente de WhatsApp para **Don Regalo** (donregalo.pe), delivery de regalos en Lima.
El cliente escribe por WhatsApp → Meta Cloud API → webhook → un orquestador clasifica
la intención y delega en un especialista → responde por Cloud API. Los asesores
humanos atienden desde un CRM PHP.

Pila: FastAPI (Python 3.12), OpenAI, Qdrant, MySQL (CRM PHP), SQLite (local/tests).

**Mínimo 3.11** (el `Dockerfile` fija `python:3.12-slim`, que es lo que corre en
producción). En 3.10 revientan ~9 tests por `asyncio.timeout`, que no existe hasta
3.11 — si ves esos fallos, es la versión de Python, no el código.

## La regla que más se olvida: sandbox/ es un espejo

`sandbox/app`, `sandbox/tests` y `sandbox/evals` son una **copia exacta** de la raíz
(`app/`, `tests/`, `evals/`). La fuente de verdad es la raíz. **Cada cambio en la raíz
hay que reflejarlo en `sandbox/`** o divergen — ya pasó. Tras editar:

```bash
python scripts/check_mirror.py         # ¿está sincronizado? (0 = "espejo OK")
python scripts/check_mirror.py --fix   # sincronízalo desde la raíz
```

Compara contenido (no mtime), ignora `__pycache__` y avisa también de lo que
**sobra** en el espejo: un archivo borrado en la raíz que sigue vivo en `sandbox/`
es la otra mitad de la divergencia, y el `diff` a mano de antes no la miraba.

Ya no depende de que alguien se acuerde: **CI lo corre y falla si divergen**
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)), antes de los tests — si
el espejo divergió, el resto del build mide dos versiones distintas del código.

(Sí, mantener dos copias idénticas es frágil. Borrar `sandbox/` sigue sobre la mesa,
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
4. **La taxonomía (tipos/categorías/subtipos) NO va en el prompt.** Sale SOLO de la
   tool `explorar_catalogo` (`GET /catalogo/navegacion`). Una lista hardcodeada en el
   prompt se desactualiza Y le da al modelo de dónde extrapolar: inventó "desayuno
   clásico/premium", "globos y kits". Una sola puerta a la taxonomía; nada de
   `listar_categorias` compitiendo.
   Corolario (22-07): **el menú tampoco lo escribe el modelo.** Tenerlo prohibido en
   el playbook no bastó — a una clienta le ofreció siete "tipos de planta" (existen
   tres: Orquideas, Suculentas, Terrarios) y luego seis terrarios inventados, con
   descripción y sin precio, y acabó ofreciéndole un asesor para enseñarle fotos de
   un producto que no existe: cuatro menús, cero productos, veinte minutos. Ahora la
   lista la arma [`taxonomy.render_menu`](app/harness/taxonomy.py) desde el payload y
   **la numeración es del código**, que es lo que permite resolver el "7" del cliente
   sin preguntarle: `master._answer_menu` lo traduce a slug y, si esa rama no tiene
   hijas o ya se gastaron los dos menús (`MAX_MENU_DEPTH`), llama a la API y muestra
   productos sin pasar por el modelo. Dos menús como máximo y luego fotos: quien ya
   sabe lo que quiere no vuelve tras el tercer formulario. Ver
   `tests/test_menu_taxonomia.py`.
   **El nivel se deduce del mensaje, nunca se asume.** La primera versión de esto
   reescribía SIEMPRE con las categorías padre, y a un cliente que preguntó "¿Cuáles
   son las opciones de flores disponibles?" le contestó con desayunos, peluches y
   cestas: le borró de la respuesta lo único que había pedido. Si `match_category`
   ve que ya nombró una categoría, el menú es el de SUS hijas (y si no tiene hijas
   —Cestas, Peluches— van productos directos, sin menú). Ese matcher devuelve `None`
   ante la duda a propósito: "quiero un regalo" NO es Regalos para Bebé y "arreglos"
   es ambiguo entre Florales y Fúnebres.
5. **Un paso determinista que no entiende NO puede repetirse igual.** La FSM del
   cierre era una función pura de `(paso, texto)`: al no entender devolvía los mismos
   bytes, y los devolvía para siempre. Una clienta recibió cuatro veces "No pude
   confirmar esa fecha" y otras cuatro el menú de horarios — también al escribir
   "Gracias" y "Ya no deseo el pedido por q no entienden" — y se fue. Ahora
   `state.step_retries` cuenta fallos seguidos: cada reintento reformula y **cita** lo
   que el cliente escribió, y al tercero `meta["handoff"]` cede el chat (rescate, no
   venta: sin pedido temporal ni verde en el CRM). Antes de tratar el texto como
   respuesta al formulario se mira si lo es: cortesía se acusa sin gastar reintento,
   abandono y "no me entienden" derivan. Ver `_again`/`_advance` en
   [`checkout.py`](app/harness/checkout.py) y `tests/test_checkout_no_bucle.py`.
   Corolario: los ejemplos dentro de un mensaje se **calculan**, no se escriben — la
   plantilla fija proponía "20/07" y el 21 de julio pedía una fecha futura con un
   ejemplo del día anterior.
6. **Dos caminos que entregan lo mismo necesitan un candado, no buena suerte.** El
   push del CRM (`POST /internal/outbox/send`) y el drenaje periódico del agente
   (cada 12s) leían la misma fila `pending` de `crm_outbox`, y la fila seguía
   `pending` durante TODA la llamada a la Cloud API. Un "No disculpe. Somos de Lima"
   del asesor le llegó tres veces al cliente. Ahora `deliver_outbox` **reclama antes
   de hablar con Meta** (`Repository::claimOutbox`, un UPDATE condicional atómico:
   `pending → sending`); quien no gana el claim se retira. Las filas que se quedan en
   `sending` vuelven a la cola a los 3 min, por si el agente muere entre medias.
   Debajo hay dos redes más: `_already_seen` descarta redeliveries de Meta por
   `wamid` ([`buffer.py`](app/services/buffer.py)) y `findDuplicateMessage` no pinta
   dos veces el mismo texto del mismo emisor en 90s. Ojo con el criterio: **"mismo
   contenido" a secas no es un duplicado** — un cliente puede escribir "sí" dos veces
   a propósito, y borrarle el segundo es inventarse una conversación que no ocurrió.
   Hacen falta conversación + dirección + emisor + ventana corta, o un `wamid`
   repetido, que sí es prueba directa.
7. **El estado se guarda fusionando, no pisando.** `load_state` → mutar →
   `save_state` es leer-modificar-escribir de un documento COMPLETO, y hay TRES
   escritores sobre el mismo: el turno del cliente (tarda segundos, con el LLM en
   medio), el releaser (en segundo plano) y `perform_handoff` (a mitad del propio
   turno). El lock por conversación de Redis solo serializa los turnos
   ENTRANTES, así que no protege de los otros dos. Ya había un *lost update*
   vivo: `perform_handoff` guardaba `handoff_at` y, al terminar, `master`
   escribía su copia —cargada ANTES— y lo borraba; ese campo es el ancla del
   releaser, o sea que el turno deshacía el arreglo del punto 5 en cada handoff.
   Ahora el documento lleva `version`, el CRM hace la escritura condicional
   (`Repository::casSetting`, `SELECT … FOR UPDATE`, único sitio donde puede ser
   atómica) y quien pierde la carrera **relee y reaplica solo SU delta**
   (`state.state_delta` / `apply_delta`). Reintentar el turno entero no es
   opción: ya se habló con el cliente. Todo escritor pasa su foto:
   `save_state(..., base=snapshot)` — sin `base` vuelve a pisar. Ver
   `tests/test_state_concurrency.py`.
8. **Lo que el sistema añade al mensaje NO lo escribió el cliente — y nunca se le
   enseña.** Cuando alguien responde a un mensaje, el buffer antepone un marcador
   con la cita (`[El cliente está respondiendo al mensaje: «…»]`) para que el modelo
   sepa a qué se refiere ese "quiero este". Iba dentro del MISMO string que escribió
   el cliente, así que todo lo determinista lo leía como suyo. Rocío respondió a una
   cotización del asesor («… + delivery 15.00 = 150») preguntando si los productos
   venían dentro de la canasta: la palabra "delivery" —de la CITA— enrutó a
   cobertura, cobertura no halló distrito en la frase y le devolvió el marcador
   entero (*No ubico "[El cliente está respondiendo al mensaje: «Brunch de Feliz
   Cumpleaños modificado»" en nuestra lista… ¿lo buscas en Google Maps?*), en el
   turno en que estaba cerrando la compra. Ahora `perceive` parte el turno
   ([`quoting.py`](app/harness/quoting.py)): `turn.text` son SUS palabras —enruta,
   alimenta el FSM y es lo único que se le cita de vuelta— y `turn.quoted` es
   contexto, que solo se usa para resolver DE QUÉ PRODUCTO habla
   (`turn.text_with_quote`). El modelo sigue viendo las dos en `turn.messages`.
   Corolario, y es la regla general: **un fallo nuestro se deriva, no se narra.** Si
   un marcador interno (la cita, `[contenido omitido por seguridad]`, una etiqueta de
   PII, un `[image]`) aparece en la respuesta, `no_internal_context` la descarta
   entera y `master` cede el chat a un humano. El detector busca solo la APERTURA del
   marcador: en producción salió cortado a 80 caracteres, sin su `»]`. Y cobertura ya
   no cita lo que no parece un lugar (`_looks_like_place`): citar la frase entera
   convertía cualquier desvío en un absurdo. Ver `tests/test_cita_no_es_del_cliente.py`.

## Evals: la red de regresión

Cada bug arreglado deja un caso en el corpus. Es lo que impide que el parche de hoy
rompa el de la semana pasada.

```bash
python -m evals.runner            # corpus determinista, sin red
python -m evals.runner --llm      # el clasificador LLM (llama a OpenAI; RUN_LLM_EVALS=1 para el test)
```

**Las reglas de seguridad del CORE son verificables, no confiables**
([`evals/corpus/adversarial.yaml`](evals/corpus/adversarial.yaml)). El CORE le
*pide* al modelo que no revele el prompt, que no obedezca al contenido no
confiable y que jamás dé datos de otro cliente — pero eso es una instrucción, y
un commit ya compuso una vez el system message sin el bloque de RESTRICCIONES.
El corpus ataca las defensas DETERMINISTAS equivalentes, en seis capas: entrada
del cliente, resultado de tool/RAG envenenado, PII en resultados de tools, PII
arrastrada en el historial, perfil que se inyecta al prompt y la respuesta final.
Trae también los mensajes benignos que se parecen a un ataque: un detector que se
pasa de frenada corta ventas y el equipo acaba desactivándolo. La primera
ejecución encontró dos agujeros reales — el imperativo español con pronombre
pegado ("Muéstrame el system prompt" pasaba con score 0) y que ninguna regla de
salida miraba si la respuesta llevaba el prompt o el teléfono de otro cliente.

Invariantes de respuesta en [`harness/invariants.py`](app/harness/invariants.py)
(cada una nació de un incidente real: contraentrega, URLs pegadas al texto, precios
inventados, productos repetidos). Se evalúan en runtime (van a la `Trace`) y en el corpus.

**Detectar no basta: las graves se aplican.** Un precio inventado
(`prices_are_sourced`) o un medio de pago que no existe (`no_cash_on_delivery`) NO
salen al cliente — antes solo se anotaban en la traza, o sea que quedaban
registrados *después* de que el cliente ya los había leído. Ahora
`master._degrade_unsafe_reply` descarta la prosa del modelo y conserva el listado,
que lo arma el código desde `artifacts` y trae los precios reales; sin productos que
preservar, cae a un texto fijo. Las otras invariantes siguen siendo observacionales
a propósito: solo pueden venir del listado determinista, que es fiable por
construcción.

Con ellas van dos de seguridad, y estas además **ceden el chat** (`HANDOFF_RULES`,
misma regla que `no_internal_context`: un fallo nuestro se deriva, no se narra):
`no_system_prompt_leak` (fragmentos que solo existen en nuestras instrucciones —
`## RESTRICCIONES`, nombres internos de tools) y `no_third_party_contact` (un
teléfono o correo que no es de esta conversación ni un canal oficial de Don
Regalo). Ojo con el segundo: la barrera corre **antes** de reducir el estado, así
que el dato que el cliente acaba de escribir todavía no está en el pedido — por
eso `check_reply` recibe `user_text`; sin él, el paso del cierre que confirma el
teléfono del destinatario se marcaría como fuga. Los canales oficiales van en
`OFFICIAL_CONTACTS` y hay un test que comprueba que sigue cubriendo lo que dice
[`prompts/facts.py`](app/prompts/facts.py): el número de Yape va escrito ahí como
`943 113 807`, y olvidarlo bloquearía justo el mensaje que cierra la venta.

## Tests

```bash
python -m pytest tests/ -q       # 817 pasan, 2 skip, offline
```

**Qué cuesta un turno.** `donregalo_llm_tokens_total{agent,type}` y
`donregalo_llm_cost_usd_total{agent}` en `/metrics`, más `prompt_tokens` /
`cached_tokens` / `llm_calls` en la traza de cada turno. El agente se etiqueta por
`ContextVar` ([`observability/llm_usage.py`](app/observability/llm_usage.py)), no
por parámetro: cruzarlo por las firmas obligaba a que cada test que hace stub del
cliente HTTP replicara el kwarg. **No hay precios en el código** — un tarifario
hardcodeado caduca en silencio y da una cifra falsa; los tokens se cuentan
siempre, el dinero solo si se declara `LLM_PRICES`.

**El webhook aceptaba cualquier cosa sin `WHATSAPP_APP_SECRET`.**
`_valid_signature` devolvía `True` cuando no había secreto, así que quien
conociera la URL podía inyectar mensajes como si fueran de un cliente. Ahora el
arranque lo grita (`/health` → `webhook_signature: unverified`) y
`WHATSAPP_REQUIRE_SIGNATURE=1` lo cierra. **Va en 0 por defecto a propósito:**
encenderlo sin el secreto puesto rechazaría todos los webhooks y dejaría el
negocio sin WhatsApp. Hay además un rate limit (`WEBHOOK_RATE_LIMIT_PER_MINUTE`)
que corre ANTES de leer el cuerpo y de verificar la firma.

**Un proveedor de respaldo para el LLM** (`LLM_FALLBACK_BASE_URL` / `_API_KEY` /
`_MODEL`, cualquier API con formato OpenAI). Entra también con el circuito
ABIERTO —que es cuando más falta hace— y tiene su propio breaker. Si caen los
dos, se propaga el error del **primario**: enseñar el del respaldo mandaría a
depurar el proveedor equivocado. Vacío = desactivado.

**Cada turno deja la huella de sus instrucciones** (`prompt_version` en la
traza): solo CORE + FACTS + PLAYBOOK, nunca la hora ni el estado — si cambiara
en cada mensaje no serviría para agrupar nada. Es lo que permite ver si algo se
degradó justo cuando alguien reescribió un playbook.

**Calidad más allá de las invariantes:** `python -m evals.judge` puntúa una
muestra con tres criterios (¿resolvió?, ¿tono?, ¿sin inventar?). El corpus son
conversaciones donde las invariantes pasan **limpias** y la respuesta puede ser
mala igual. No entra en el gate: no es determinista y cuesta dinero. Los evals
del router corren de noche contra un **umbral de tasa** (`--llm`), no exigiendo
100%: un job que se pone rojo por ruido se acaba ignorando.

**La DLQ ya no es muda.** `check_dlq` en el tick del watchdog avisa desde **un
solo** mensaje descartado (no hay una cantidad sana) y publica
`donregalo_dlq_depth`. Un mensaje ahí es un cliente al que no contestó nadie: el
fallo ocurre antes del CRM, así que ni siquiera sale en el inbox.

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
- **CRM PHP**: hosting de Don Regalo, carpeta [`crm/`](crm/). El verde de venta
  cerrada, el sonido del handoff y los emojis viven aquí — hay que subir el CRM aparte.
  Los módulos del asesor (asignación, notas, seguimientos, venta manual y la
  ventana de 24h de WhatsApp) son **solo CRM**: no tocan el agente, pero exigen
  las migraciones `009`–`012` ANTES del PHP. Ver [`crm/README.md`](crm/README.md).

**De qué anuncio viene el lead.** Muchos chats abren con "¡Hola! Quiero más
información.": no lo escribe el cliente, es el *mensaje predefinido* de un anuncio de
Click-to-WhatsApp, y **toda la campaña comparte el mismo texto** (siete anuncios
"PORTADA …" en DESAYUNOS | VENTAS), así que por el mensaje no se distingue cuál fue.
Meta lo dice en un `referral` adjunto **solo al primer mensaje**; llegaba al webhook y
se tiraba entero. Ahora `parser` lo captura, `upsert_inbound` lo manda y
`Repository::setConversationAd` lo fija en `crm_conversations` — **una vez y sin
pisar**: si el cliente vuelve meses después desde otro anuncio, el lead sigue siendo
de quien lo trajo. El asesor lo ve al abrir el chat (tarjeta `ad-card`, con el copy
que el cliente leyó antes de escribir). Ojo: **no es recuperable hacia atrás**, solo
de la migración `007` en adelante. El *nombre* del anuncio ("PORTADA FAMILIA") NO
viene en el payload — eso exige cruzar `source_id` con la Marketing API, que es
también la única vía para saber si vino de Facebook o de Instagram.

**Citar es texto Y foto.** El asesor manda un ramo, el cliente responde a ESA foto
("podría optar por esta opción?") y la cita salía como el literal `[image]`: justo en
el turno en que el lead elige, el vendedor era el único que no veía qué. El texto de
una imagen sin caption ES ese marcador, así que guardar solo `quoted_text` no bastaba;
ahora `Repository::findQuotedByWaId` devuelve texto **y** `media_url`, y se persiste en
`quoted_media_url` (migración `008`). Cubre los dos sentidos —cliente citando y asesor
citando— porque ambos pasan por ese resolvedor. La cita se **copia**, no se referencia:
si el mensaje original se borra, debe seguir mostrando lo que el cliente vio.

**Las migraciones SQL van primero.** [`crm/sql/`](crm/sql/) se corre a mano contra el
MySQL del hosting, y el orden importa: SQL → CRM PHP → agente. Al revés, el agente
llama a endpoints que aún no existen. Por eso el claim del outbox degrada en vez de
fallar (`deliver_outbox`): si el CRM no sabe reclamar todavía, envía igual — quedarse
callado dejaría al equipo sin poder escribirle a ningún cliente.

## Deuda conocida: que Regalito sepa QUÉ CONTIENE cada producto

Un cliente citó un desayuno y preguntó *"¿Qué contiene? ¿Se puede quitar y aumentar
otros, y qué opciones hay?"*. Regalito ofreció **"lo consulto con un asesor y te
vuelvo"** — algo que no puede hacer: no tiene forma de preguntarle nada a nadie,
solo de ceder el chat. Lo que hay hoy, verificado contra la API real:

| Pregunta | ¿Se puede responder hoy? |
|---|---|
| *"¿Qué contiene?"* | **Sí, y ya no depende del modelo.** `GET /productos/{id}` trae `descripcion` con la lista. Antes había que confiar en que el especialista llamara `detalle_producto` y a veces no lo hacía. Ahora `master._handle_detail` resuelve el producto en código (`resolve_chosen_product`) y **precarga** el detalle antes de que el modelo escriba: el contenido entra por `extra_system` como hecho del sistema. Si no se puede saber de qué producto pregunta, no se adivina — el especialista pregunta cuál. |
| *"¿Se puede quitar/añadir items? ¿Qué opciones hay?"* | **No.** La API **no modela** personalización: no hay items, ni sustituciones, ni precios por componente. Esto **sí** es deuda del servidor y hoy es motivo legítimo de handoff. |

Estado:

1. ~~**El HTML se filtra al cliente.**~~ **Corregido (jul 2026):** `adapters.clean_html()`
   limpia tags, entidades y tabs en la frontera del adapter. Dos formas según el destino:
   `descripcion_corta` y `nombre` van `inline=True` (una sola línea, porque se imprimen
   dentro de la viñeta del listado y un salto lo parte); la `descripcion` del detalle
   conserva los saltos, que **son** las viñetas del "¿qué contiene?". También se limpia
   `metodos_pago` — su descripción envuelve el número de cuenta y el CCI en `<p>`/`<strong>`,
   y es texto que el cliente copia para pagar. Cubierto en `tests/test_adapters.py`.
2. **Personalizar un producto no existe en la API.** Mientras no exista, "¿se puede
   cambiar el globo / quitar el croissant?" se escala, no se improvisa. **Sigue abierto.**

Regla que ya es determinista (no depende del prompt): si la respuesta del modelo
**promete** meter a un asesor ("consulto con un asesor", "un asesor te enviará…"),
`master` ejecuta el handoff de verdad y descarta esa frase. Regalito no puede
consultar y volver; solo ceder el chat. Ver `_promises_handoff` en
[`harness/master.py`](app/harness/master.py) y las RESTRICCIONES del
[`CORE`](app/prompts/core.py).

## Deuda conocida (del servidor, no de este código)

- ~~`GET /productos/{id}` devolvía un error fatal de PHP con status 200 para
  productos con imagen `null` (id 1235).~~ **Corregido en producción (jul 2026):**
  devuelve JSON. Pero **la foto del detalle sigue rota** (verificado 21-07-2026):
  `imagen_url` viene `null` y las cuatro variantes de `imagenes[]`
  (`thumb_`, `middle_`, `big_`, original) dan **404** — los nombres llevan prefijos
  que no existen en disco. La única URL viva es la del listado
  (`medium/<archivo>`, sin prefijo). O sea que el adapter "recupera" una URL muerta
  y **ningún detalle sale con foto**. Es del servidor: bastaría con que
  `GET /productos/{id}` devolviera la misma URL que ya devuelve el listado.
  Mientras tanto el detalle se muestra sin imagen en vez de perderse entero
  (`catalog._productos`), que es lo que pasaba antes.
- ~~La API se autocontradecía: categorías usaban `url_categoria`, productos
  `categoria_url`.~~ **Corregido en producción (jul 2026):** ahora es `url_categoria`
  en todas partes (API.md nota #4). El adapter lee ese campo; mantiene `categoria_url`
  solo como respaldo histórico.
