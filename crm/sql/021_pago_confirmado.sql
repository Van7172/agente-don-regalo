-- Separa pedido confirmado de pago verificado por un asesor.
-- Repetible: comprueba columnas y ENUM antes de alterar el esquema.
SET @falta := (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'crm_ventas_historiales'
    AND COLUMN_NAME = 'fecha_confirmacion_pago');
SET @ddl := IF(@falta,
  'ALTER TABLE crm_ventas_historiales ADD COLUMN fecha_confirmacion_pago DATETIME NULL AFTER estado_venta_historial',
  'SELECT 1');
PREPARE aplicar FROM @ddl; EXECUTE aplicar; DEALLOCATE PREPARE aplicar;

SET @falta := (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'crm_ventas_historiales'
    AND COLUMN_NAME = 'id_usuario_pago');
SET @ddl := IF(@falta,
  'ALTER TABLE crm_ventas_historiales ADD COLUMN id_usuario_pago INT(11) NULL AFTER fecha_confirmacion_pago',
  'SELECT 1');
PREPARE aplicar FROM @ddl; EXECUTE aplicar; DEALLOCATE PREPARE aplicar;

SET @falta := (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'crm_ventas_historiales'
    AND COLUMN_NAME = 'nombre_usuario_pago');
SET @ddl := IF(@falta,
  'ALTER TABLE crm_ventas_historiales ADD COLUMN nombre_usuario_pago VARCHAR(190) NULL AFTER id_usuario_pago',
  'SELECT 1');
PREPARE aplicar FROM @ddl; EXECUTE aplicar; DEALLOCATE PREPARE aplicar;

-- Una entrega confirmada implica un pago. Los demás registros históricos
-- quedan sin verificar; no se inventa un cobro que el CRM no observó.
UPDATE crm_ventas_historiales
SET fecha_confirmacion_pago = COALESCE(fecha_confirmacion_entrega, fecha_cierre_venta_historial)
WHERE estado_venta_historial = 'entregado' AND fecha_confirmacion_pago IS NULL;

SET @falta := (SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'crm_conversations'
    AND COLUMN_NAME = 'etapa_conversation'
    AND COLUMN_TYPE LIKE '%pendiente_pago%');
SET @ddl := IF(@falta,
  'ALTER TABLE crm_conversations MODIFY COLUMN etapa_conversation ENUM(''nuevo'',''cotizado'',''pendiente_pago'',''vendido'',''entregado'',''perdido'') NULL',
  'SELECT 1');
PREPARE aplicar FROM @ddl; EXECUTE aplicar; DEALLOCATE PREPARE aplicar;

-- Corrige las etapas que el bot había marcado Vendido antes del cobro.
UPDATE crm_conversations c
JOIN (
  SELECT v.id_conversation, v.fecha_confirmacion_pago
  FROM crm_ventas_historiales v
  JOIN (
    SELECT id_conversation, MAX(id_venta_historial) AS id_ultima
    FROM crm_ventas_historiales GROUP BY id_conversation
  ) ultima ON ultima.id_ultima = v.id_venta_historial
) v ON v.id_conversation = c.id_conversation
SET c.etapa_conversation = 'pendiente_pago', c.fecha_etapa_conversation = NOW()
WHERE c.etapa_conversation = 'vendido' AND v.fecha_confirmacion_pago IS NULL;
