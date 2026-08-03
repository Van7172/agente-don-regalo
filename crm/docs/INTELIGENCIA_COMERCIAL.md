# Inteligencia comercial — Campañas y Oportunidades

Dos módulos del CRM que responden preguntas que hasta ahora nadie podía contestar
con datos: **qué anuncio trae compradores** y **qué producto nos están pidiendo
que no tenemos**.

Nació de una idea más grande —comparar contra la competencia con research
automatizado— que está **en pausa a propósito**. Lo que sigue explica qué quedó
construido, qué decisiones no conviene deshacer, y qué haría falta para retomar
la parte parada.

Estado a **2 de agosto de 2026**.

| Fase | Qué | Estado |
|---|---|---|
| 0 | Migraciones `013` + `014` | ✅ Escrita, **sin correr** |
| 1A | Campañas (`/campaigns.php`) | ✅ Código completo |
| 1B | Oportunidades (`/opportunities.php`) + instrumentación del agente | ✅ Código completo |
| 2 | Scraper de competidores + matching | ⏸️ **En pausa** |
| 3 | Cruce: hueco de catálogo × demanda propia | ⏸️ **En pausa** (depende de 2) |

**Nada de esto está desplegado.** Las migraciones no se han corrido contra el
MySQL del hosting y las vistas no se han renderizado con datos reales. Los tests
que pasan son contratos estáticos y unitarios, no una prueba contra la base.

---

## Puesta en marcha

El orden importa y es el de siempre: **SQL → CRM PHP → agente**. Al revés, el
agente llama a `POST /api/demand`, que todavía no existe, y suma 404 al circuit
breaker del CRM.

1. Correr `crm/sql/013_venta_producto_id.sql` contra el MySQL del hosting.
2. Correr `crm/sql/014_demanda_no_cubierta.sql`.
3. Subir el CRM PHP (`public/`, `views/`, `src/Repository.php`, `public/api/index.php`, `public/assets/app.css`).
4. Push a GitHub + redeploy del agente en EasyPanel.
5. **Verificar que llegó entero:**

```bash
python scripts/check_deploy.py \
  --base-url https://donregalo.pe/crm/public \
  --token EL_CRM_INTERNAL_TOKEN
```

Entre el paso 3 y el 4 no se rompe nada: el agente viejo simplemente no manda
nada a `/demand`. Entre el 2 y el 3 tampoco: la tabla existe y nadie la escribe.

El paso 5 no es opcional y es la razón de que exista `GET /api/schema`: **este
despliegue falla en silencio.** Saltarse una migración o subir medio PHP no da
un error, da una pantalla vacía — y la de Oportunidades vacía se lee exactamente
igual que "no falta nada en el catálogo", que es la conclusión contraria a la
verdadera. El script comprueba, columna a columna, que las migraciones están
aplicadas, y detecta el caso que más engaña: un fatal de PHP servido con status
200 cuando el MySQL no acepta la conexión.

Campañas funciona con los pasos 1-3. Oportunidades necesita además el 4: es el
agente quien alimenta la tabla.

**La medición de demanda empieza en el paso 4 y no es recuperable hacia atrás.**
El histórico de conversaciones nunca guardó qué devolvía cada búsqueda, así que
no hay nada que backfillear — igual que pasó con el `referral` de los anuncios en
la migración `007`. Cada día sin desplegar es un día de señal perdida.

---

## Campañas — `/campaigns.php`

Qué anuncio trae compradores y cuál trae curiosos. Cruza `crm_conversations.ad_*`
(que se captura desde la migración `007` y hasta ahora solo se pintaba como
tarjeta en el inbox) con `crm_ventas_historiales`.

Columnas: chats, ventas, conversión, monto, ticket promedio y % que necesitó
asesor, por anuncio.

### Tres decisiones que cambian los números

**El rango filtra por llegada del chat, no por cierre de la venta.** Es una
cohorte: de los chats que entraron en estas fechas, cuántos compraron (cuando sea
que compraran). Rangear las ventas por su fecha de cierre mezclaría numerador y
denominador —ventas de leads de otro periodo sobre los leads de este— y daría
conversiones que pueden pasar del 100%. El precio de hacerlo bien es que la
cohorte reciente sale artificialmente baja; la vista lo advierte.

**`LEFT JOIN`, no `JOIN`.** Con un join interno desaparecen los anuncios que no
cerraron ni una venta, que son exactamente los que hay que apagar.

**`COUNT(DISTINCT c.id_conversation)`.** El `LEFT JOIN` duplica la fila de la
conversación por cada venta: sin `DISTINCT`, un chat con dos compras cuenta como
dos chats y hunde su propia conversión.

### Lo que esta tabla NO puede decir

- **El nombre del anuncio.** Meta manda `source_id` y `headline` en el referral,
  no "PORTADA FAMILIA". Cruzarlo con Ads Manager exige la Marketing API.
- **Nada anterior a la migración `007`.** Esos chats no tienen anuncio.
- **«Sin anuncio identificado» no es tráfico orgánico.** Mezcla a quien escribió
  por su cuenta con todo lo anterior a que se capturara el referral. Llamarlo
  orgánico sería afirmar de más.
- **Costo por venta.** Falta el gasto — ver abajo.

### Decisión tomada: sin CAC por ahora

El gasto de Meta Ads no está en ninguna base de datos. Se evaluaron tres caminos
y se eligió **no medir CAC de momento**: la conversión por anuncio ya responde la
pregunta cara ("cuál trae compradores"), y el costo se puede añadir después sin
rehacer nada.

Si en algún momento se quiere, las dos opciones siguen siendo:

| Opción | Coste de arranque |
|---|---|
| Tabla manual de gasto por anuncio y mes, que llena el equipo desde el CRM | Una migración; sale en días. Se queda vacía si nadie la llena |
| Meta Marketing API | App de Meta, permiso `ads_read`, revisión y token de larga duración: semanas de trámite |

---

## Oportunidades — `/opportunities.php`

Lo que los clientes pidieron y el catálogo no tenía, agrupado por término.

El agente marca `aproximado: true` cuando la búsqueda no encuentra lo pedido
(`app/tools/executor.py`), para que el bot diga "te muestro lo más cercano". Esa
marca vivía dentro del resultado de la tool durante ese turno y **moría ahí**:
sabíamos decirle al cliente que no lo teníamos y no sabíamos cuántos lo habían
pedido. Ahora se persiste en `crm_demanda_no_cubierta`.

### Cómo se registra

`executor._record_demand` → `app/services/demand.py` → `POST /api/demand` →
`Repository::recordDemandMiss`.

**Se anota al agotar la escalera, no en cada peldaño.** La búsqueda tiene cuatro
escalones (API literal → Qdrant → consulta acortada → categoría). Un escalón que
falla no es una carencia mientras el siguiente acierte: contarlos por separado
convertiría una búsqueda en hasta cuatro señales de un producto que sí teníamos.

**Se guarda la consulta ORIGINAL, no la que acabó funcionando.** Si "unicornio de
peluche gigante" solo dio resultados al acortarse a "peluche", lo que falta en el
catálogo es el unicornio gigante. Guardar "peluche" —el término que sí tiene
productos— invertiría la conclusión y mandaría a comprar stock de lo que ya hay.

**No puede costarle latencia ni errores al turno.** Es telemetría, no parte de la
respuesta: `record_miss` lanza una tarea y vuelve, y `_send` se traga cualquier
excepción. Un CRM lento o caído no puede añadir un segundo al turno de un
cliente, y si falla lo que se pierde es una fila de análisis. Hay un test que
falla si alguien lo convierte en `await` (`test_no_se_espera_al_crm`).

**Vacío y aproximado son dos carencias distintas.** En una el cliente se llevó
una alternativa y pudo comprar; en la otra se fue con las manos vacías. Por eso
la tabla ordena por vacíos y no por total: diez "globos metálicos" sin resultado
pesan más que treinta con alternativa.

**Se guarda el argumento de la tool, no el mensaje del cliente.** El mensaje trae
prosa, saludos y a veces la dirección o el teléfono del destinatario; el
argumento ya viene destilado a términos. Aun así pasa por `redact_personal_data`,
porque ese argumento lo compone el modelo y nada garantiza que no arrastre PII
desde la frase original.

**Cada miss es una fila, sin contador acumulado.** Dos personas que piden lo
mismo el mismo día son dos señales, no una, y agrupando por fecha se ve si algo
sube (una tendencia, una fecha del calendario) o si fue un caso suelto. Un
contador borraría el cuándo, que es la mitad de la información. La columna
«Chats» separa un cliente insistiendo de un producto que falta de verdad.

### Lo que esta tabla NO puede decir

- **Nada anterior al despliegue.** Y una tabla vacía se lee igual que "no falta
  nada en el catálogo", que es el peor modo de fallo posible para este módulo.
  Por eso la vista lo dice en pantalla.
- **Si el producto que falta se vendería.** Que lo pidan no es que lo compren.

---

## En pausa: el módulo de competencia

**Por qué se paró:** falta la lista de competidores. Un scraper no puede
"descubrirlos" — esa es justo la parte que se inventaría datos. Sin 3-5 dominios
concretos, la Fase 2 no arranca.

La pausa es de alcance, no de bloqueo técnico: las Fases 0, 1A y 1B no dependen
de esto y funcionan solas.

### Lo que se puede y lo que no

Separarlo desde el principio es lo que evita construir algo que miente:

| Se puede (es público) | No se puede (nadie lo publica) |
|---|---|
| Precio de catálogo por producto | Sus ventas, unidades, conversión |
| Qué categorías y productos ofrecen | Sus márgenes o costos |
| Costo de delivery por distrito, cobertura | Su ticket promedio real |
| Horarios y plazos que prometen | Su stock |
| Promos y precios tachados | Cuánto de eso vende de verdad |

Un módulo que prometa la columna derecha se la va a inventar. Este repo ya tiene
tres cicatrices exactamente de eso (taxonomía inventada, menús inventados,
precios inventados), y la regla aplica igual aquí: **el LLM redacta la lectura,
nunca produce la cifra.** Cada dato de la competencia guardado con URL de origen
y fecha de captura, o no se muestra — el mismo principio que la invariante
`prices_are_sourced`.

### Diseño ya pensado, para no re-derivarlo

**Dónde vive.** El scraping y el research en el agente FastAPI, que ya tiene
watchdog con ticks, cliente LLM con fallback y breaker, contabilidad de costo por
token y Qdrant. El CRM PHP solo lee y pinta — es hosting compartido, sin cron
confiable. Es el patrón que ya usa `OperationsClient.php` con el panel de
operaciones.

**Una migración nueva** (`crm_competencia_productos`), con `url` y
`capturado_en` obligatorios, y `visto_por_ultima_vez` para detectar lo que
dejaron de vender. Las filas no se borran nunca: el histórico de lo que
ofrecieron es en sí mismo la señal.

> Ojo con el número: `015` ya lo ocupa `015_outbox_filename.sql`, de otra rama
> de trabajo. Mira `crm/sql/` antes de crearla — dos migraciones con el mismo
> prefijo se corren en un orden que depende de quién ordene la lista.

**Matching por Qdrant.** Embedding de nombre + descripción de cada producto de la
competencia contra el catálogo propio; si el vecino más cercano queda por debajo
del umbral, es un hueco candidato. La maquinaria ya existe en
`app/services/product_embedding_index.py`, no hay que traer nada nuevo.

**Fase 3, el cruce:** lo que ellos venden y nosotros no, ordenado por cuánta
gente ya nos lo pidió (`crm_demanda_no_cubierta`). Ahí es donde las dos mitades
se juntan y donde está el valor real — un hueco de catálogo que nadie pide es
ruido.

**Sobre el scraping:** precios públicos de catálogo es práctica normal y
legítima. La línea es respetar `robots.txt`, ir despacio, identificarse en el
`User-Agent`, y no tocar nada detrás de login ni datos personales.

### Para retomar hacen falta

1. **La lista de competidores** (3-5 dominios de Lima: regalos, desayunos,
   flores). Es lo único que bloquea.
2. Decidir si se usan los MCP de **BrightData** (scraping) o **Semrush**
   (tráfico y keywords), que hoy están sin autorizar — se conectan desde los
   ajustes de conectores de claude.ai.

---

## Archivos

**Migraciones** — `crm/sql/013_venta_producto_id.sql`, `crm/sql/014_demanda_no_cubierta.sql`

**CRM PHP** — `public/campaigns.php`, `public/opportunities.php`,
`views/campaigns.php`, `views/opportunities.php`, `views/layout.php` (nav),
`public/assets/app.css`, `public/api/index.php` (`POST /demand`, `GET /schema`),
`src/Repository.php` (`campaignPerformance`, `recordDemandMiss`, `unmetDemand`,
`schemaState`)

**Agente** — `app/services/demand.py`, `app/tools/executor.py`
(`_record_demand`), `app/harness/master.py` (`set_conversation`),
`app/crm/http_client.py` (`record_demand_miss`)

**Despliegue** — `scripts/check_deploy.py`, `crm/docs/DEPLOY.md`

> Recuerda que `sandbox/app` y `sandbox/tests` son espejo de la raíz:
> `python scripts/check_mirror.py --fix`.

## Tests

```bash
python scripts/check_crm.py                          # contratos PHP
python -m pytest tests/test_demanda_no_cubierta.py -q # el agente
```

- `crm/tests/campaigns_contract.php` — cada aserción es una forma de mentir con
  los mismos datos: el join que infla los chats, el rango que mezcla cohortes, el
  backfill que convierte "sin producto" en el producto 0.
- `crm/tests/demanda_contract.php` — la cadena cruza los tres despliegues y si se
  cae un eslabón no se detecta como error, sino como una tabla vacía.
- `tests/test_demanda_no_cubierta.py` — protege las tres formas en que este
  módulo podría hacer daño: latencia, excepción, o guardar el término equivocado.
- `crm/tests/deploy_check_contract.php` — que el verificador siga pudiendo
  verificar: `/schema` con token, comprobación por columna y no por tabla, y que
  probar la ruta de demanda no escriba una fila falsa.

Los tres los corre `scripts/check_crm.py` salvo el de pytest.
