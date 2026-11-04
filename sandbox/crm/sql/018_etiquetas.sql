-- Etiquetas de conversación, elegidas de un catálogo.
--
-- Texto libre en cada chat partiría los datos: "cumple" y "Cumpleaños" serían dos
-- etiquetas. La colación utf8mb4_unicode_ci hace que el UNIQUE no distinga
-- mayúsculas ni acentos, así que "Cumpleaños" y "cumpleanos" chocan.
--
-- Se archivan, no se borran: una etiqueta archivada no se ofrece en chats nuevos,
-- pero los chats que ya la llevan conservan esa información.

CREATE TABLE IF NOT EXISTS crm_etiquetas (
  id_etiqueta INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  nombre_etiqueta VARCHAR(40) NOT NULL,
  color_etiqueta VARCHAR(16) NOT NULL DEFAULT 'arena'
    COMMENT 'Valores en Repository::TAG_COLORS; cada uno tiene su clase CSS',
  activa_etiqueta TINYINT(1) NOT NULL DEFAULT 1,
  id_usuario_actualizacion INT(11) NULL,
  nombre_usuario_actualizacion VARCHAR(190) NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_etiqueta),
  UNIQUE KEY uq_crm_etiqueta_nombre (id_tenant, nombre_etiqueta),
  CONSTRAINT fk_crm_etiqueta_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_conversacion_etiquetas (
  id_conversation INT(11) NOT NULL,
  id_etiqueta INT(11) NOT NULL,
  id_usuario INT(11) NULL,
  nombre_usuario VARCHAR(190) NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_conversation, id_etiqueta),
  -- El filtro de la bandeja pregunta "qué chats llevan esta etiqueta".
  KEY idx_crm_conv_etiqueta (id_etiqueta, id_conversation),
  CONSTRAINT fk_crm_conv_etiqueta_conv
    FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE,
  CONSTRAINT fk_crm_conv_etiqueta_tag
    FOREIGN KEY (id_etiqueta) REFERENCES crm_etiquetas (id_etiqueta) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
