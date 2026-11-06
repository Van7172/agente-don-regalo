# Frontera entre interpretación, evidencia y ejecución

## Diagnóstico

El router resolvía por reglas antes de mostrar el contexto a un modelo. El
clasificador de respaldo solo recibía el texto actual y una breve descripción
del paso del formulario. La cobertura y la conversación de entrega compartían
un único flujo determinista: una lista de tipos de lugar decidía qué era una
referencia y las plantillas decidían cómo responder. El prompt `coverage` figuraba
en el registro, pero ningún modelo lo leía porque era una ruta determinista.

Agregar expresiones regulares y pruebas de frases exactas arreglaba incidentes,
pero ampliaba esa rigidez. La interpretación necesitaba una frontera distinta.

| Decisión | Responsable |
| --- | --- |
| Completar/corregir destino, referencia o punto; contestar una pregunta; cambiar de tema | IA con diálogo reciente y estado relevante |
| Distinguir hora deseada, requisito indispensable y franja aceptada | IA propone el significado; código exige precondiciones y valida la franja |
| Comprender una referencia no enumerada previamente | IA, con evidencia literal del mensaje actual |
| Cobertura, ID y tarifa de un distrito | API real y resolución existente |
| Metadatos de un enlace Maps | Lector de Maps con orígenes acotados; no inferencia geográfica del modelo |
| Catálogo, precios, stock y hechos del producto | Herramientas existentes; la IA interpreta la necesidad y consulta |
| Confirmar pago, crear pedidos, ceder realmente el chat | Código y autorizaciones/acciones existentes |
| Redactar la conversación de entrega | IA con hechos verificados; tarifa y restricciones de cobertura se incorporan literalmente |
| Identificadores, dedicatorias y datos literales del formulario | Parsers existentes, fuera de esta primera ruta contextual |

## Implementación

`harness/contextual.py` interpreta antes del router por palabras en conversaciones
de entrega y sus continuaciones. Usa el modelo rápido con razonamiento ya
configurado para el agente (`openai_fast_model`), no el clasificador barato.
El primer ensayo con el clasificador barato confundió un rechazo; no se activó.

El modelo devuelve una propuesta estructurada: acción, intención, confianza,
datos nuevos y fragmentos que los sustentan. No devuelve campos de pago,
productos creados, tarifas ni acciones ejecutables arbitrarias. No se guarda una
cadena de pensamiento. Se registra la categoría de decisión y el consumo.

Cada dato nuevo exige evidencia del mensaje actual. El estado y el historial
ayudan a entender, pero no son nuevas elecciones. Datos opcionales sin evidencia
se descartan; si falta evidencia necesaria para la acción, se abstiene. Una
interpretación insegura pide aclarar y no modifica datos. No se vuelve a una
suposición por palabras cuando falla el intérprete.

Las propuestas válidas se ejecutan por las operaciones existentes de cobertura,
franjas y handoff. Las referencias y puntos semánticos llegan como argumentos
validados, sin exigir las expresiones regulares de tipos de establecimiento.
Se conservan los límites por fecha, anticipación, horario y confirmación.

Una segunda llamada redacta con hechos y pendientes, sin copiar la plantilla de
respaldo. Solo puede usar hechos verificados. El código incorpora la tarifa y
las restricciones de cobertura; rechaza importes inventados, garantías de hora,
confirmaciones anticipadas y respuestas que fallen los guardrails existentes.
Si falla la redacción se utiliza una respuesta operacional segura.

Esta primera versión no sustituye todo el harness, los parsers de formularios
ni el flujo de catálogo. Sí puede detectar que el cliente cambió hacia catálogo,
detalle u otra consulta y delegar a las capacidades existentes.

## Operación y validación

- `CONTEXTUAL_DELIVERY_ENABLED=1` activa la ruta; `0` restaura la anterior.
- `CONTEXTUAL_DELIVERY_PERCENT=0..100` selecciona conversaciones por un hash
  estable del tenant e ID. `/health` muestra activación, porcentaje y versión.
- `contextual.*`, uso del modelo y `trace.context_action` permiten observar
  abstenciones, acciones y respuestas descartadas sin registrar direcciones.
- El estado del intérprete omite teléfono, correo, nombres de personas,
  dirección completa, URL del pin y coordenadas. Lleva distrito/referencia/punto
  relevantes y una ventana acotada del diálogo ya saneado.
- Dos llamadas al modelo en un turno logístico normal: interpretar y redactar.
  En las pruebas completas se observaron aproximadamente 3–7 segundos por
  turno; un handoff no necesita la llamada de redacción. Este es el coste de
  devolver interpretación y conversación al modelo en esta ruta.
- `tests/test_contextual_delivery.py` verifica contratos, evidencia, acciones,
  límites y fallos. Las otras pruebas conservan las protecciones anteriores.
- `scripts/smoke_contextual_delivery.py`: veinte situaciones sintéticas con el
  modelo real; se evalúan decisiones y hechos, no frases exactas.
- `scripts/smoke_contextual_conversation.py`: tres diálogos completos con
  cobertura real, estado aislado, Maps simulado y handoff simulado. Bloquea
  escrituras al CRM, pedidos, avisos y mensajes reales.

El esquema estricto utiliza la capacidad de salida estructurada documentada en
la [documentación oficial de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs).
La validación de negocio sigue siendo independiente de la estructura del JSON.
