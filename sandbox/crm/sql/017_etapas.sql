-- Etapa de venta de cada conversación.
--
-- El inbox era una lista de chats: no había forma de saber cuántos leads están
-- cotizados ni por qué se pierden. Vendido y Entregado NO los marca nadie: salen
-- de `crm_ventas_historiales`, para que el embudo no pueda contradecir al
-- historial de ventas (`Repository::syncStageFromSales`).
--
-- NULL es legítimo y significa "sin clasificar": son los chats anteriores a esta
-- migración que no tienen venta. Ponerlos en 'nuevo' inflaría el embudo con leads
-- de hace meses que nadie va a atender.
--
-- Repetible: comprueba cada columna antes de añadirla, porque ADD COLUMN IF NOT
-- EXISTS no existe en MySQL (solo en MariaDB) y no sabemos cuál corre el hosting.

SET @falta := (
  SELECT COUNT(*) = 0 FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'crm_conversations'
    AND COLUMN_NAME = 'etapa_conversation'
);
SET @ddl := IF(@falta,
  'ALTER TABLE crm_conversations
     ADD COLUMN etapa_conversation
       ENUM(''nuevo'',''cotizado'',''vendido'',''entregado'',''perdido'') NULL
       COMMENT ''NULL = sin clasificar (chat anterior a la migración 017)'',
     ADD COLUMN motivo_perdida_conversation VARCHAR(32) NULL
       COMMENT ''Solo con etapa perdido; valores en Repository::LOST_REASONS'',
     ADD COLUMN fecha_etapa_conversation DATETIME NULL
       COMMENT ''Cuándo entró en la etapa actual; ancla de la reapertura'',
     ADD KEY idx_crm_conv_etapa (id_tenant, etapa_conversation)',
  'SELECT 1'
);
PREPARE aplicar FROM @ddl;
EXECUTE aplicar;
DEALLOCATE PREPARE aplicar;

-- Sin fila de historial no se puede medir cuánto tarda un lead en avanzar, y eso
-- no se puede reconstruir después. id_usuario NULL = lo hizo el sistema.
CREATE TABLE IF NOT EXISTS crm_etapas_historial (
  id_etapa_historial INT(11) NOT NULL AUTO_INCREMENT,
  id_conversation INT(11) NOT NULL,
  etapa_anterior VARCHAR(16) NULL,
  etapa_nueva VARCHAR(16) NOT NULL,
  motivo_perdida VARCHAR(32) NULL,
  id_usuario INT(11) NULL COMMENT 'NULL = cambio automático del sistema',
  nombre_usuario VARCHAR(190) NULL COMMENT 'copia del nombre: es auditoría',
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_etapa_historial),
  KEY idx_crm_etapa_hist_conv (id_conversation, fecha_creacion),
  CONSTRAINT fk_crm_etapa_hist_conv
    FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Chats con venta: su etapa sale de la última venta. Repetible: solo toca los que
-- siguen sin etapa. No escribe historial: ese cambio no ocurrió hoy.
UPDATE crm_conversations c
JOIN (
  SELECT v.id_conversation, v.estado_venta_historial, v.fecha_cierre_venta_historial
  FROM crm_ventas_historiales v
  JOIN (
    SELECT id_conversation, MAX(marca_cierre_venta_historial) AS marca
    FROM crm_ventas_historiales
    GROUP BY id_conversation
  ) ultima
    ON ultima.id_conversation = v.id_conversation
   AND ultima.marca = v.marca_cierre_venta_historial
) venta ON venta.id_conversation = c.id_conversation
SET c.etapa_conversation = IF(venta.estado_venta_historial = 'entregado', 'entregado', 'vendido'),
    c.fecha_etapa_conversation = venta.fecha_cierre_venta_historial
WHERE c.etapa_conversation IS NULL;
