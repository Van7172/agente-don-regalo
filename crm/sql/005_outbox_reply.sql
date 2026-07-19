-- Responder a un mensaje desde el CRM (menú contextual del inbox).
--
-- El asesor elige un mensaje y responde citándolo, como en WhatsApp. Para que el
-- cliente vea la cita en SU WhatsApp hay que mandar `context.message_id` a la
-- Cloud API, así que la cola necesita recordar a qué mensaje se responde.
--
-- `quoted_text` en crm_messages ya existía (001): esto es solo para la cola.

ALTER TABLE crm_outbox
  ADD COLUMN reply_to_wa_id VARCHAR(128) NULL DEFAULT NULL AFTER media_path;
