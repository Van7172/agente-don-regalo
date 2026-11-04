-- Ventas que cierra el asesor, no el bot.
--
-- `crm_ventas_historiales` se llenaba SOLO desde el setting `sale_{id}` que
-- escribe el agente al cerrar el checkout. Pero el chat que llega a un humano es
-- justo el que el bot no pudo cerrar: esas ventas —la mayoría de las que pasan
-- por el CRM— no quedaban registradas en ninguna parte. El Historial de ventas
-- mostraba una fracción del negocio y los reportes contaban esa fracción como si
-- fuera el total.
--
-- `origen_venta_historial` existe para poder separarlas después: cuánto cierra
-- solo el bot y cuánto cierra el equipo son dos preguntas distintas, y con las
-- filas mezcladas no se pueden responder. Las filas viejas son del bot, que es
-- lo que dice el DEFAULT.
--
-- `monto_venta_historial` es el valor del producto. No existía porque el bot
-- nunca lo guardó (el precio vive en el pedido temporal del panel e-commerce);
-- sin él, una venta manual no se puede sumar y el módulo no sirve de nada.

ALTER TABLE crm_ventas_historiales
  ADD COLUMN origen_venta_historial ENUM('bot','asesor') NOT NULL DEFAULT 'bot'
    COMMENT 'quién cerró la venta: el agente o un asesor desde el inbox',
  ADD COLUMN monto_venta_historial DECIMAL(10,2) NULL
    COMMENT 'valor del producto en soles, sin envío',
  ADD COLUMN id_usuario_registro INT(11) NULL
    COMMENT 'asesor que registró la venta manual (NULL si la cerró el bot)',
  ADD COLUMN nombre_usuario_registro VARCHAR(190) NULL;

-- "Ventas del mes por origen" sin escanear la tabla entera.
ALTER TABLE crm_ventas_historiales
  ADD KEY idx_ventas_historiales_origen
    (id_tenant, origen_venta_historial, fecha_cierre_venta_historial);
