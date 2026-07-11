-- CRM WhatsApp Agent — Opción C
-- Prefijo crm_* para no colisionar con tablas e-commerce Don Regalo.
-- Destino: MySQL donregalo_bd (XAMPP; puerto típico 3307 en este entorno)

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE IF NOT EXISTS crm_tenants (
  id_tenant INT(11) NOT NULL AUTO_INCREMENT,
  slug_tenant VARCHAR(64) NOT NULL,
  nombre_tenant VARCHAR(128) NOT NULL,
  config_tenant JSON NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_tenant),
  UNIQUE KEY uq_crm_tenant_slug (slug_tenant)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_contacts (
  id_contact INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  wa_id VARCHAR(32) NOT NULL COMMENT 'Teléfono E.164 sin +',
  nombre_contact VARCHAR(256) NOT NULL DEFAULT '',
  email_contact VARCHAR(256) NULL,
  attributes_contact JSON NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_contact),
  UNIQUE KEY uq_crm_contact_wa (id_tenant, wa_id),
  KEY idx_crm_contact_tenant (id_tenant),
  CONSTRAINT fk_crm_contact_tenant FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_conversations (
  id_conversation INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  id_contact INT(11) NOT NULL,
  status_conversation ENUM('open','closed') NOT NULL DEFAULT 'open',
  mode_conversation ENUM('AI','HUMAN') NOT NULL DEFAULT 'AI',
  bot_active TINYINT(1) NOT NULL DEFAULT 1,
  human_support TINYINT(1) NOT NULL DEFAULT 0,
  labels_conversation JSON NULL,
  last_message_at DATETIME NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_conversation),
  KEY idx_crm_conv_tenant (id_tenant),
  KEY idx_crm_conv_contact (id_contact),
  KEY idx_crm_conv_last (last_message_at),
  CONSTRAINT fk_crm_conv_tenant FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE,
  CONSTRAINT fk_crm_conv_contact FOREIGN KEY (id_contact) REFERENCES crm_contacts (id_contact) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_messages (
  id_message INT(11) NOT NULL AUTO_INCREMENT,
  id_conversation INT(11) NOT NULL,
  direction_message ENUM('inbound','outbound') NOT NULL,
  sender_type ENUM('contact','bot','agent','system') NOT NULL,
  role_message ENUM('user','assistant','human','system') NOT NULL,
  wa_message_id VARCHAR(128) NULL,
  content_message MEDIUMTEXT NOT NULL,
  media_url VARCHAR(1024) NULL,
  quoted_text TEXT NULL,
  raw_message JSON NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_message),
  KEY idx_crm_msg_conv (id_conversation),
  KEY idx_crm_msg_wa (wa_message_id),
  KEY idx_crm_msg_created (fecha_creacion),
  CONSTRAINT fk_crm_msg_conv FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_leads (
  id_lead INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  wa_id VARCHAR(32) NOT NULL,
  nombre_lead VARCHAR(256) NULL,
  email_lead VARCHAR(256) NULL,
  notas_lead TEXT NULL,
  utm_source VARCHAR(128) NULL,
  utm_medium VARCHAR(128) NULL,
  utm_campaign VARCHAR(128) NULL,
  temperatura_lead VARCHAR(32) NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_lead),
  UNIQUE KEY uq_crm_lead_wa (id_tenant, wa_id),
  KEY idx_crm_lead_tenant (id_tenant),
  CONSTRAINT fk_crm_lead_tenant FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_lead_memory (
  id_lead_memory INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  wa_id VARCHAR(32) NOT NULL,
  nombre_memory VARCHAR(256) NULL,
  email_memory VARCHAR(256) NULL,
  objetivo_memory TEXT NULL,
  situacion_memory TEXT NULL,
  temperatura_memory VARCHAR(32) NULL,
  resumen_memory MEDIUMTEXT NULL,
  first_seen DATETIME NULL,
  last_seen DATETIME NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_lead_memory),
  UNIQUE KEY uq_crm_memory_wa (id_tenant, wa_id),
  KEY idx_crm_memory_tenant (id_tenant),
  CONSTRAINT fk_crm_memory_tenant FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_settings (
  id_setting INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  llave_setting VARCHAR(128) NOT NULL,
  valor_setting TEXT NULL,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_setting),
  UNIQUE KEY uq_crm_setting (id_tenant, llave_setting),
  CONSTRAINT fk_crm_setting_tenant FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_outbox (
  id_outbox INT(11) NOT NULL AUTO_INCREMENT,
  id_conversation INT(11) NOT NULL,
  wa_id VARCHAR(32) NOT NULL,
  content_outbox MEDIUMTEXT NOT NULL,
  type_outbox ENUM('text','image') NOT NULL DEFAULT 'text',
  media_path VARCHAR(1024) NULL,
  status_outbox ENUM('pending','sent','failed') NOT NULL DEFAULT 'pending',
  error_outbox TEXT NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_enviado DATETIME NULL,
  PRIMARY KEY (id_outbox),
  KEY idx_crm_outbox_status (status_outbox),
  KEY idx_crm_outbox_conv (id_conversation),
  CONSTRAINT fk_crm_outbox_conv FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_tool_events (
  id_tool_event INT(11) NOT NULL AUTO_INCREMENT,
  id_conversation INT(11) NOT NULL,
  nombre_tool VARCHAR(64) NOT NULL,
  has_email TINYINT(1) NOT NULL DEFAULT 0,
  payload_tool JSON NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_tool_event),
  KEY idx_crm_tool_conv (id_conversation),
  CONSTRAINT fk_crm_tool_conv FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

INSERT INTO crm_tenants (slug_tenant, nombre_tenant, config_tenant)
SELECT 'don-regalo', 'Don Regalo', JSON_OBJECT('locale', 'es-PE')
WHERE NOT EXISTS (SELECT 1 FROM crm_tenants WHERE slug_tenant = 'don-regalo');

INSERT INTO crm_settings (id_tenant, llave_setting, valor_setting)
SELECT t.id_tenant, 'paused', '0'
FROM crm_tenants t
WHERE t.slug_tenant = 'don-regalo'
  AND NOT EXISTS (
    SELECT 1 FROM crm_settings s
    WHERE s.id_tenant = t.id_tenant AND s.llave_setting = 'paused'
  );
