# Quitar de la cola de atención (manual)

## Problema

La cola superior (“necesitan ayuda ahora”) lista conversaciones con
`human_support = 1`. Ese flag no se limpia al atender en modo HUMAN: solo al
devolver el chat a Don Regalo. Chats ya resueltos por un humano quedan atrapados
en la cola con badge AYUDA.

## Decisión

Solo acción **manual**. No auto-limpiar al tomar el chat.

## Comportamiento

1. Botón **“Quitar de la cola”** en el header del hilo, visible solo si
   `human_support` es true.
2. En cada chip de la cola, una **×** que hace lo mismo (sin forzar abrir el chat).
3. Acción: `PATCH /conversations/{id}/mode` con `{ "human_support": false }`.
4. No cambia `mode` (HUMAN/AI), no cierra la conversación, no toca `keep_human`.
5. Sin diálogo de confirmación (un nuevo handoff vuelve a meterlo en la cola).

## Fuera de alcance

- Auto-dismiss al tomar / al enviar el primer mensaje del asesor.
- Cerrar o archivar conversaciones.
- Cambios de schema SQL.

## Archivos

- `crm/views/inbox.php` — botón en el chat-head
- `crm/public/assets/inbox.js` — visibilidad + handlers
- `crm/public/assets/app.css` — estilo mínimo del chip × / botón
