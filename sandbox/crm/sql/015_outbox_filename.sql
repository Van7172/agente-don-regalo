-- El nombre del archivo no sobrevivía a un fallo del push.
--
-- El asesor adjunta «catalogodedesayunos.pdf». El nombre viaja en el payload
-- que el CRM le hace POST al agente (`/internal/outbox/send`) y NUNCA se
-- guarda: `enqueueOutbox` inserta contenido, tipo, media_path y reply_to, y
-- nada más. Mientras el push funciona no se nota.
--
-- El problema es qué pasa cuando NO funciona, que es justo cuando importa. Si
-- el agente no responde, la fila se queda en 'pending' a propósito para que el
-- drenaje periódico la recoja (ver 006_outbox_claim). Pero el drenaje solo ve
-- la fila, y en la fila el nombre ya no existe: `send_media` cae en su valor
-- por defecto y el cliente recibe un archivo llamado **"documento"**, sin
-- extensión. Varios clientes de WhatsApp no lo abren bien sin el `.pdf`, así
-- que el camino de rescate entrega algo peor que no entregar nada — el asesor
-- cree que salió y el cliente tiene un fichero que no puede abrir.
--
-- 255 y no más: es el límite práctico de un nombre de archivo y el agente lo
-- manda tal cual a la Cloud API. Nullable porque el texto y las filas antiguas
-- no tienen nombre, y no hay nada que rellenar hacia atrás: el nombre de los
-- envíos ya hechos se perdió cuando terminó su push.

ALTER TABLE crm_outbox
  ADD COLUMN filename_outbox VARCHAR(255) NULL
    COMMENT 'nombre original del adjunto; el drenaje lo necesita si el push falló';
