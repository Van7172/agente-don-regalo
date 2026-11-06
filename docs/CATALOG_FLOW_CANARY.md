# Catálogo determinista: validación y activación gradual

El flujo textual validado en el piloto se integra como código secuencial en el
agente actual. Reutiliza el executor MCP/REST/Qdrant, relevancia, atributos,
presupuesto, contratos y renderizador. No requiere dependencias nuevas.

## Cuándo se usa

Se exige intención de búsqueda, categoría reconocible y una característica o
modelo concreto. Una proporción configurable de conversaciones elegibles utiliza
el flujo nuevo; la selección SHA-256 de tenant + ID de conversación es estable.
Los demás turnos continúan por el especialista actual.

Imágenes, enlaces, respuestas citadas, pedidos abiertos, personalizaciones,
exclusiones, cantidades y preguntas mixtas con políticas conservan las rutas
actuales. La versión inicial se configura al 10 % y no aumenta automáticamente.

El flujo busca, comprueba atributos/destinatario/stock, consulta la ficha cuando
hay una única coincidencia y genera hasta tres opciones con precios del catálogo.
Los textos de producto con datos personales o instrucciones se omiten. Una
respuesta vacía del catálogo pide aclaración. Un fallo técnico permite continuar
con el especialista actual; las cancelaciones y mensajes nuevos se propagan.

El flujo devuelve `AgentResult`: el master aplica la barrera de salida y guarda
estado por el mecanismo existente. No envía mensajes ni crea pedidos por sí mismo.

## Configuración y reversión

```dotenv
CATALOG_FLOW_ENABLED=1
CATALOG_FLOW_PERCENT=10
```

Los valores predeterminados son apagado y 0 %. El corte es por conversación,
no por mensaje; el porcentaje observado puede diferir del objetivo con muestras
pequeñas. Sin un ID de conversación el flujo nuevo no se selecciona.

- `GET /health` muestra activación, porcentaje y desactivación de emergencia.
- `GET /internal/catalog-flow/status` exige el token interno y devuelve métricas.
- `POST /internal/catalog-flow/disable`, con `X-Agent-Token`, desactiva la selección
  mediante un marcador local compartido por los workers del mismo contenedor.
  Rechaza una instalación sin token configurado o con el token de ejemplo.
- Para una reversión permanente, guardar `CATALOG_FLOW_ENABLED=0` en EasyPanel y
  redesplegar. El marcador temporal puede desaparecer al recrear el contenedor.

No existe un endpoint para ampliar o reactivar la cohorte. Esos cambios requieren
configurar el porcentaje y realizar otro despliegue. Antes de escalar el agente a
varios contenedores debe desactivarse cada réplica o usarse la reversión por env.

## Cómo se mide

Las trazas incluyen `catalog_strategy`: `deterministic`, `legacy` o
`legacy_fallback` solo para turnos elegibles, para que el control no mezcle
consultas abiertas con pedidos concretos. Los contadores por cohorte resumen turnos, barreras activadas,
llamadas conversacionales, tokens y herramientas. Las series
`catalog.turn.<estrategia>` incluyen latencia, máximo y p95 estimado.
`catalog.flow` distingue exacto, aproximado, vacío, error y cancelación.

Las métricas son locales al proceso y se reinician en los despliegues. Los logs
existentes permiten conservar las trazas fuera del proceso si su recolector está
habilitado. No se almacenan mensajes, teléfonos ni argumentos de tools en estas
métricas. Los tokens de chat no incluyen embeddings ni representan todo el costo.

Durante la revisión del canary se desactiva inmediatamente si hay una respuesta
con precio incorrecto, identidad de producto errónea o datos personales ajenos.
También se detiene para investigar si los fallbacks técnicos superan el 5 % tras
al menos 20 turnos seleccionados. Se mantiene el 10 % hasta revisar precisión,
latencia, aclaraciones y consumo con una muestra suficiente; no hay promoción
automática al 100 %.

## Validación

`tests/test_catalog_flow.py` contiene 30 escenarios de aceptación: nombres,
ocasión/destinatario, correcciones, exclusión de vistos, monedas y envío, colores,
productos no encontrados, rutas fuera del alcance, agotados y API caída. Incluye
contratos adicionales de cohorte estable, fallback, cancelación, textos con
contactos y control de desactivación autenticado.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_catalog_flow.py -q
.\.venv\Scripts\python.exe scripts/smoke_catalog_flow.py
.\.venv\Scripts\python.exe scripts/quality_gate.py --report artifacts/catalog-canary-quality-gate.json
```

El smoke utiliza 12 mensajes sintéticos, lecturas reales del catálogo y bloqueos
de envíos, pedidos, memoria y estado. Su informe se guarda en
`artifacts/catalog-canary-live-smoke.json`. Un fallback correcto por indisponibilidad
transitoria se registra separado de la ejecución determinista.
