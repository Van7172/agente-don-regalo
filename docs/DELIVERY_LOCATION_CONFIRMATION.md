# Confirmación de ubicación y hora de entrega

El flujo distingue evidencia de Maps, distrito confirmado por el cliente, hora
solicitada y franja aceptada. Un enlace nunca es un nombre de distrito y una hora
puntual nunca implica aceptar una franja.

## Comportamiento

- Los enlaces HTTPS de Google Maps se leen con redirecciones limitadas a orígenes
  de Maps autorizados. Se extraen nombres de distrito de metadatos y ruta, sin
  interpretar instrucciones ni enviar la página a un modelo. Si no hay evidencia
  inequívoca se pide el distrito directamente.
- El distrito detectado se conserva como candidato y se pregunta al cliente en
  el mismo turno. La API de cobertura y sus tarifas se consultan al confirmarlo.
- Un destino nuevo invalida distrito, zona, identificador y tarifa anteriores.
- Fecha válida y hora solicitada se conservan durante la conversación. Los
  desayunos siguen sujetos a anticipación, franjas y excepciones vigentes.
- «A las 7 en punto» pide confirmar si esa hora es indispensable. Confirmar el
  distrito no confirma también esa necesidad. Si la hora es indispensable se
  ejecuta el handoff existente, sin crear pedido ni marcar pago o venta.
- Si hay flexibilidad, se pide aceptar un rango explícito y luego se retoma el
  cierre. No se puede confirmar un pedido con ubicación o condición horaria
  pendientes. Las nuevas propiedades de estado tienen valores por defecto y no
  requieren migración SQL.

## Verificación

`tests/test_delivery_location_flow.py` cubre preguntas sin destino, enlaces,
confirmaciones, correcciones, negación, hora puntual, persistencia, cambio de
destino, límites de URL, derivación y reanudación del cierre. Incluye un diálogo
por la entrada pública del harness con herramientas sustituidas, sin mensajes
reales ni pedidos.

El enlace del incidente fue comprobado en modo lectura: los metadatos de Google
Maps permitieron identificar Callao. Su dirección y URL no se incluyen aquí.

El control `scripts/quality_gate.py` valida espejo, contrato MCP, suite y evals.
Revertir este cambio restaura el comportamiento previo; las propiedades nuevas
del estado se ignoran por las versiones anteriores.
