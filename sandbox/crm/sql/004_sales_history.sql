-- Historial auditable de ventas cerradas por Regalito.
-- Ejecutar antes de publicar el módulo "Historial de ventas".

CREATE TABLE IF NOT EXISTS crm_ventas_historiales (
  id_venta_historial INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  id_conversation INT(11) NOT NULL,
  id_contact INT(11) NOT NULL,
  wa_id_venta_historial VARCHAR(32) NOT NULL,
  nombre_contacto_venta_historial VARCHAR(190) NULL,
  producto_venta_historial VARCHAR(255) NOT NULL,
  distrito_venta_historial VARCHAR(120) NULL,
  envio_sol_venta_historial DECIMAL(10,2) NULL,
  fecha_entrega_venta_historial VARCHAR(20) NULL,
  horario_venta_historial VARCHAR(80) NULL,
  id_pedido_temporal INT(11) NULL,
  motivo_venta_historial VARCHAR(255) NULL,
  marca_cierre_venta_historial BIGINT NOT NULL,
  fecha_cierre_venta_historial DATETIME NOT NULL,
  estado_venta_historial ENUM('pendiente','entregado') NOT NULL DEFAULT 'pendiente',
  fecha_confirmacion_entrega DATETIME NULL,
  id_usuario INT(11) NULL,
  nombre_usuario_confirmacion VARCHAR(190) NULL,
  snapshot_venta_historial JSON NOT NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_venta_historial),
  UNIQUE KEY uq_venta_historial_cierre
    (id_tenant, id_conversation, marca_cierre_venta_historial),
  KEY idx_ventas_historiales_estado
    (id_tenant, estado_venta_historial, fecha_cierre_venta_historial),
  KEY idx_ventas_historiales_contacto (id_contact),
  CONSTRAINT fk_ventas_historiales_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE,
  CONSTRAINT fk_ventas_historiales_conversation
    FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE RESTRICT,
  CONSTRAINT fk_ventas_historiales_contact
    FOREIGN KEY (id_contact) REFERENCES crm_contacts (id_contact) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Backfill de fichas activas existentes. INSERT IGNORE vuelve repetible el script.
INSERT IGNORE INTO crm_ventas_historiales (
  id_tenant,
  id_conversation,
  id_contact,
  wa_id_venta_historial,
  nombre_contacto_venta_historial,
  producto_venta_historial,
  distrito_venta_historial,
  envio_sol_venta_historial,
  fecha_entrega_venta_historial,
  horario_venta_historial,
  id_pedido_temporal,
  motivo_venta_historial,
  marca_cierre_venta_historial,
  fecha_cierre_venta_historial,
  estado_venta_historial,
  snapshot_venta_historial
)
SELECT
  c.id_tenant,
  c.id_conversation,
  c.id_contact,
  ct.wa_id,
  ct.nombre_contact,
  JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.producto')),
  JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.distrito')),
  CAST(JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.envio_sol')) AS DECIMAL(10,2)),
  JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.fecha')),
  JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.horario')),
  CAST(JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.pedido_temporal_id')) AS UNSIGNED),
  JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.motivo')),
  CAST(JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.cerrada_en')) AS UNSIGNED),
  FROM_UNIXTIME(CAST(JSON_UNQUOTE(JSON_EXTRACT(s.valor_setting, '$.cerrada_en')) AS UNSIGNED)),
  'pendiente',
  CAST(s.valor_setting AS JSON)
FROM crm_settings s
JOIN crm_conversations c
  ON c.id_tenant = s.id_tenant
 AND s.llave_setting = CONCAT('sale_', c.id_conversation)
JOIN crm_contacts ct ON ct.id_contact = c.id_contact
WHERE s.llave_setting LIKE 'sale\_%'
  AND JSON_VALID(s.valor_setting)
  AND JSON_EXTRACT(s.valor_setting, '$.producto') IS NOT NULL
  AND JSON_EXTRACT(s.valor_setting, '$.cerrada_en') IS NOT NULL;
