# Detección y prevención de inyección de instrucciones

## Modelo de amenaza

El agente recibe contenido no confiable desde mensajes de WhatsApp, captions,
audio transcrito, PDF, imágenes, memoria del cliente, Qdrant, MCP y REST. Una
inyección intenta convertir ese contenido en instrucciones con autoridad para:

- sustituir las reglas del sistema;
- extraer el prompt, secretos o credenciales;
- adoptar otro rol;
- forzar herramientas o saltarse validaciones;
- persistir instrucciones maliciosas en la memoria.

No existe un detector perfecto. La defensa es por capas: OpenAI recomienda
limitar las capacidades accesibles, separar datos no confiables de instrucciones
y evitar depender únicamente de un filtro de entrada:

- [Understanding prompt injections](https://openai.com/safety/prompt-injections/)
- [Designing AI agents to resist prompt injection](https://openai.com/index/designing-agents-to-resist-prompt-injection/)
- [A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)

## Controles implementados

### 1. Barrera antes del router

`app/guardrails/input.py` detecta señales fuertes en español e inglés:

- sustitución de instrucciones;
- extracción del prompt o instrucciones internas;
- suplantación de roles privilegiados;
- extracción de tokens, claves o variables de entorno;
- etiquetas falsas `system`/`developer`;
- coerción de herramientas;
- instrucciones codificadas.

El bloqueo ocurre en `run_master` antes de clasificar, llamar a OpenAI o ejecutar
una herramienta. La respuesta es determinista y no revela qué regla exacta
detectó el ataque.

### 2. Historial y fuentes indirectas

Los ataques permanecen en el CRM como evidencia del mensaje recibido, pero se
sustituyen por un marcador antes de volver a enviar el historial al modelo. Los
strings maliciosos dentro de JSON de herramientas se eliminan conservando la
estructura y los campos seguros.

Los datos guardados del cliente se serializan como JSON delimitado y se etiquetan
como contenido no confiable. Un valor almacenado no puede cerrar el bloque ni
convertirse en una instrucción de sistema.

### 3. Jerarquía explícita

El CORE indica que mensajes, imágenes, documentos, audio, perfiles y resultados
de tools son datos sin autoridad. Una etiqueta o texto que diga `system`,
`developer` o “las reglas cambiaron” sigue siendo contenido externo.

### 4. Autorización de herramientas

Cada especialista conserva su toolset mínimo en `AgentSpec`. Además, el loop
verifica en runtime que cada llamada devuelta por el modelo pertenezca al toolset
enviado en ese mismo round. Una herramienta inventada o no autorizada recibe un
resultado bloqueado y nunca se ejecuta.

Las herramientas disponibles son mayormente lecturas. El pedido temporal se crea
desde una FSM determinista, no por decisión libre del modelo; handoff y memoria
mantienen validaciones adicionales.

### 5. Auditoría sin contenido

Se registran únicamente:

- riesgo y número de reglas activadas;
- cantidad de fragmentos saneados;
- nombre de la herramienta bloqueada;
- conversación y `trace_id`.

No se guardan mensajes, prompts, argumentos, tokens ni secretos. Las series
principales son:

- `guardrail.input`;
- `guardrail.history`;
- `guardrail.tool_input`;
- `guardrail.tool_output`;
- `guardrail.tool_authorization`.

## Decisiones de experiencia

Una frase comercial como “ignora el arreglo anterior y muéstrame rosas” no se
bloquea. Para bloquear se necesita una referencia clara a instrucciones,
jerarquía, secretos, roles privilegiados o evasión de validaciones.

El detector determinista no pretende reconocer todas las variantes adversariales.
El texto visible dentro de una imagen, por ejemplo, se contiene mediante jerarquía
de instrucciones y capacidades mínimas. Si en el futuro el agente obtiene tools
de escritura de mayor impacto, deberá añadirse confirmación humana y un
clasificador independiente para acciones de riesgo.

## Pruebas

`tests/test_prompt_injection_guardrails.py` cubre ataques directos, falsos
positivos comerciales, historial contaminado, JSON de tools, memoria persistente
y llamadas de herramientas no autorizadas.

### 7. Promesas respaldadas por evidencia

El prompt recibe exactamente los nombres de las herramientas expuestas en ese
turno. Antes de enviar una respuesta, la barrera de salida compara afirmaciones
operativas con `tools_used`: no permite confirmar pagos o modificaciones, ni
prometer contactos futuros, ni afirmar tracking o memoria sin la ejecución
correspondiente. También bloquea la exposición de CRM, API, MCP, Qdrant, modos
internos y nombres de herramientas.
