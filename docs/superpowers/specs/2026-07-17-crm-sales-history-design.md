# Historial de ventas cerradas en el CRM

## Objetivo

Conservar un registro auditable de las ventas cerradas por Regalito y permitir
que el asesor retire del chat la ficha operativa cuando el pedido ya fue
entregado.

## Contraste con las APIs existentes

Según `API.md`, el agente puede crear un pedido mediante
`POST /pedidos/temporales`, pero no existe un endpoint para consultar ese pedido
por su ID temporal ni para marcarlo como entregado. Por tanto, el CRM no debe
simular una actualización en la API comercial.

“Entregado” será una confirmación manual y explícita del asesor dentro del CRM.
Representa el estado de seguimiento del CRM, no una mutación del pedido en el
panel comercial de Don Regalo.

## Persistencia

Se añadirá una tabla normalizada `crm_ventas_historiales` con las convenciones del
proyecto:

- `id_venta_historial` como llave primaria;
- `id_tenant`, `id_conversation` e identidad del contacto;
- snapshot de producto, distrito, envío, fecha, horario e ID de pedido temporal;
- `estado_venta_historial`: `pendiente` o `entregado`;
- fecha de cierre por Regalito;
- fecha de entrega confirmada;
- usuario que confirmó la entrega;
- fechas de creación y actualización.

El historial es la fuente persistente. La clave actual
`crm_settings.sale_{conversation_id}` se mantiene como marcador de ficha activa
para conservar compatibilidad entre despliegues del agente y del CRM.

## Registro de una venta

El agente continuará escribiendo `sale_{conversation_id}` mediante el endpoint
de settings. Cuando el CRM recibe una clave con ese prefijo:

1. valida el ID de conversación y el JSON;
2. conserva la ficha activa como hoy;
3. inserta o actualiza la venta pendiente en el historial.

La operación será idempotente: reintentar el mismo anuncio no crea duplicados.
La combinación de tenant, conversación y marca `cerrada_en` identificará cada
venta. Esto permite que una misma conversación tenga varias ventas históricas
sin sobrescribir las anteriores. Las ventas ya presentes en `crm_settings` se
migrarán como pendientes durante el despliegue.

## Interacción en el inbox

La ficha verde conservará su opción de plegado y añadirá una acción secundaria
**“Marcar como entregado”**. Al pulsarla se mostrará:

> ¿Confirmas que este pedido fue entregado? La ficha desaparecerá del chat y
> quedará disponible en Historial.

Si el asesor cancela, no cambia nada. Si confirma, el CRM ejecuta una operación
autenticada y atómica que:

1. garantiza que exista el registro histórico;
2. cambia su estado a `entregado`;
3. guarda fecha, hora y usuario;
4. elimina la clave `sale_{conversation_id}`;
5. devuelve la venta actualizada.

La interfaz retirará la ficha, el resaltado verde y la prioridad de “venta
cerrada” sin recargar toda la aplicación. La acción no envía mensajes al
cliente. Si la petición falla, la ficha permanece y se muestra un error al
asesor.

## API interna del CRM

Se añadirá un endpoint de sesión para marcar la entrega, separado del cambio de
modo de conversación. Solo una sesión válida del panel podrá usarlo. El endpoint
será idempotente: repetir la acción devuelve el registro ya entregado sin
duplicarlo ni perder auditoría.

El agente seguirá usando el endpoint interno protegido por token para anunciar
la venta. Así, desplegar el CRM antes o después del agente no interrumpe el
handoff actual.

## Módulo Historial

La navegación superior añadirá **Historial de ventas**. La página mostrará:

- estado;
- cliente y WhatsApp;
- producto;
- distrito;
- fecha y horario de entrega;
- ID del pedido temporal;
- fecha de cierre;
- fecha y asesor que confirmó la entrega.

Incluirá filtros por rango de fechas y estado, además de búsqueda por cliente,
WhatsApp, producto o ID del pedido temporal. Las consultas se limitarán al
tenant de la sesión.

## Seguridad y consistencia

- Toda lectura y escritura queda aislada por tenant.
- El ID de conversación se valida y no se concatena directamente en SQL.
- La venta se copia al historial antes de borrar la ficha activa.
- No se borra el historial desde el inbox.
- El JSON histórico se descompone en campos consultables y puede conservarse
  también como snapshot para trazabilidad.
- El contenido se escapa al renderizarlo.

## Pruebas

- Anunciar una venta crea un historial pendiente y mantiene la ficha activa.
- Repetir el anuncio actualiza sin duplicar.
- Confirmar la entrega registra usuario y fecha, elimina la ficha y conserva el
  historial.
- Cancelar la confirmación no llama al endpoint.
- Un fallo deja visible la ficha.
- La lista filtra por tenant, estado, fechas y búsqueda.
- Una venta activa anterior a la migración aparece como pendiente.
- La acción del CRM no llama a la API pública de pedidos ni envía WhatsApp.

## Despliegue

Se entregará una migración SQL independiente y repetible para crear la tabla y
copiar las ventas activas. El CRM debe desplegarse con esa migración antes de
habilitar la nueva página. Los cambios del CRM se publican por separado del
agente, aunque formen parte del mismo repositorio.
