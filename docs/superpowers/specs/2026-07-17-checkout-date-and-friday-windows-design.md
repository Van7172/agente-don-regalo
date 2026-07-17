# Fechas deterministas y horarios de viernes

## Objetivo

Resolver expresiones como “mañana” con la fecha real de Lima, guardar una fecha
canónica y ofrecer horarios compatibles con el día de entrega. Los viernes no
se debe ofrecer la franja de 07:00 a 09:00.

## Fuente temporal

Todos los agentes de cara al cliente recibirán en su contexto la fecha, el día
de la semana y la hora actuales de `America/Lima`. Este contexto ayuda a
responder preguntas temporales, pero las decisiones del checkout no dependerán
del LLM.

El FSM resolverá la fecha usando la misma zona horaria. El valor canónico
guardado en el estado será `YYYY-MM-DD`; al cliente y al asesor se les mostrará
en formato legible `DD/MM/YY`. De este modo, el estado, el resumen, la ficha de
venta y `POST /pedidos/temporales` representan el mismo día.

## Normalización de fecha

La normalización existente se trasladará al momento en que el FSM recibe el
paso `date`. Debe reconocer al menos:

- hoy, mañana y pasado mañana;
- nombres de días de la semana;
- `DD/MM`, `DD/MM/YY` y `DD/MM/YYYY`;
- `YYYY-MM-DD`;
- fechas con nombre de mes.

Una fecha imposible, ambigua o anterior al día actual no avanza el FSM. Regalito
pedirá una fecha concreta en lugar de guardar el texto literal. Un día de la
semana sin fecha expresa se interpreta como la próxima ocurrencia futura.

## Horarios por fecha de entrega

La fuente de verdad dejará de ser una cadena y mapas globales independientes.
Cada opción tendrá etiqueta, rango visible y hora admitida por la API. La lista
y el parser se construirán desde esa misma estructura para que la numeración no
pueda desalinearse.

### Días distintos de viernes

1. Mañana temprano — 07:00 a 09:00
2. Mañana — 09:00 a 11:00
3. Mediodía — 11:00 a 14:00
4. Tarde — 14:00 a 17:00
5. Tarde-noche — 16:00 a 19:00

### Viernes

1. Mañana — 09:00 a 11:00
2. Mediodía — 11:00 a 14:00
3. Tarde — 14:00 a 17:00
4. Tarde-noche — 16:00 a 19:00

El día se calcula a partir de la fecha de entrega, no de la fecha actual. La
respuesta numérica se interpreta usando exactamente las opciones que se
mostraron para esa fecha.

## Compatibilidad con `API.md`

`POST /pedidos/temporales` acepta las horas canónicas `07:00`, `10:00`, `13:00`
y `16:00`. Las franjas visibles conservarán el mapeo actual:

- 07:00–09:00 → `07:00`;
- 09:00–11:00 → `10:00`;
- 11:00–14:00 → `13:00`;
- 14:00–17:00 → `16:00`;
- 16:00–19:00 → `16:00`.

No se enviará una hora fuera del contrato documentado.

## Pruebas

- “mañana” se resuelve desde una fecha Lima controlada.
- La fecha canónica se guarda antes de ofrecer horarios.
- Una fecha inválida o pasada repregunta y no avanza.
- Un viernes muestra cuatro opciones, sin 07:00–09:00.
- Otro día muestra las cinco opciones.
- En viernes, responder `1` selecciona 09:00–11:00; en otro día selecciona
  07:00–09:00.
- Cada franja produce una hora aceptada por `POST /pedidos/temporales`.
- El contexto temporal se compone para todos los agentes de cara al cliente.

## Despliegue

Los cambios y pruebas se implementan en la raíz y se sincronizan con
`sandbox/app`, `sandbox/tests` y `sandbox/evals`.
