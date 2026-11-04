-- Harness Regalito: campos útiles para releaser HUMAN→AI (opcionales / documentados).
-- El auto-releaser también funciona con crm_settings (last_human_outbound_*, keep_human_*).
-- Ejecutar solo si prefieres columnas en crm_conversations:

-- ALTER TABLE crm_conversations
--   ADD COLUMN keep_human TINYINT(1) NOT NULL DEFAULT 0,
--   ADD COLUMN handoff_reason VARCHAR(255) NULL,
--   ADD COLUMN last_human_outbound_at DATETIME NULL;

SELECT 1;
