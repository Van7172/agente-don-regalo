-- Seguimientos: el CRM deja de ser solo reactivo.
--
-- El lead que dijo "lo consulto con mi esposa y te aviso" se pierde. Nadie tiene
-- una lista de a quién hay que volver a escribirle hoy: la bandeja ordena por
-- recencia, así que el chat que MÁS necesita un empujón es justo el que se va
-- hundiendo hacia abajo. Hoy eso lo suple la memoria del asesor.
--
-- `fecha_programada` es DATETIME y no DATE porque "mañana a las 9" y "esta tarde"
-- son seguimientos distintos: un recordatorio que solo sabe el día vence a
-- medianoche y aparece al fondo del turno, cuando ya no sirve.
--
-- Ojo con la ventana de servicio de WhatsApp (24h desde el último mensaje del
-- cliente): un seguimiento a 3 días casi seguro cae fuera y necesitará una
-- plantilla aprobada por Meta. El módulo avisa, no lo esconde.

CREATE TABLE IF NOT EXISTS crm_seguimientos (
  id_seguimiento INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  id_conversation INT(11) NOT NULL,
  motivo_seguimiento VARCHAR(255) NOT NULL,
  fecha_programada DATETIME NOT NULL,
  estado_seguimiento ENUM('pendiente','hecho','cancelado') NOT NULL DEFAULT 'pendiente',
  id_usuario INT(11) NULL COMMENT 'quién lo programó',
  nombre_usuario VARCHAR(190) NULL,
  id_usuario_cierre INT(11) NULL COMMENT 'quién lo dio por hecho o lo canceló',
  nombre_usuario_cierre VARCHAR(190) NULL,
  fecha_cierre DATETIME NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_seguimiento),
  -- El rail del inbox pregunta siempre lo mismo: "pendientes de este tenant que
  -- ya vencieron, los más antiguos primero".
  KEY idx_crm_seg_pendiente (id_tenant, estado_seguimiento, fecha_programada),
  KEY idx_crm_seg_conv (id_conversation, estado_seguimiento),
  CONSTRAINT fk_crm_seg_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE,
  CONSTRAINT fk_crm_seg_conv
    FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
