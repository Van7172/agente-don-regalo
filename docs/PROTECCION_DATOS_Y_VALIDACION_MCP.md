# Protección de datos personales y validación MCP

## Objetivo

Reducir la exposición de identificadores personales y garantizar que ningún
parámetro salga hacia el servidor MCP sin cumplir un contrato cerrado.

## Protección de datos

`app/guardrails/privacy.py` aplica minimización por finalidad:

- el último mensaje conserva temporalmente los datos necesarios para completar
  la operación solicitada;
- correos, teléfonos, documentos y datos de pago se redactan del historial
  anterior antes de volver a enviarlo al modelo;
- perfiles y memoria excluyen campos de contacto del `system prompt`;
- resultados de herramientas se recorren como JSON y redactan antes de volver
  al modelo, sin eliminar productos, estados ni códigos operacionales;
- notas de memoria no conservan identificadores directos;
- auditoría y métricas solo registran conteos, operación y resultado.

Esta capa controla la propagación dentro del agente. Los sistemas transaccionales
de pedidos y CRM mantienen sus propias finalidades y políticas de retención.

## Contrato estricto de herramientas

Hay dos validaciones consecutivas:

1. `run_specialist` valida el JSON del modelo contra el esquema publicado para
   el especialista. Rechaza JSON inválido, campos adicionales, requeridos
   ausentes y tipos incorrectos.
2. `app/guardrails/parameters.py` valida nuevamente el payload inmediatamente
   antes del lifecycle y de cualquier solicitud HTTP MCP.

Cada contrato MCP establece tool permitida, claves, tipos, límites, formatos,
enums y cardinalidad. Una tool desconocida nunca se transmite.

`donregalo_rastrear_pedido` es la única llamada MCP que admite un identificador
personal (`email`), acompañado exclusivamente por el código de pedido. Teléfono,
dirección, instrucciones o banderas administrativas son rechazados.

## Observabilidad

- `guardrail.personal_data:redacted`;
- `guardrail.tool_parameters:blocked`;
- `guardrail.mcp_parameters:blocked`.

Los eventos no incluyen valores, prompts ni argumentos.
