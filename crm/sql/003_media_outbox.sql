-- Medios en el inbox del CRM (fotos, notas de voz, documentos).
--
-- El outbox solo admitía 'text' e 'image'. El asesor ahora también puede enviar
-- audio y documentos, así que hay que ensanchar el ENUM.
--
-- Es aditivo: no borra ni reescribe nada. Las filas existentes siguen válidas.
-- crm_messages.media_url ya existe (VARCHAR 1024) y no necesita cambios.

ALTER TABLE crm_outbox
  MODIFY COLUMN type_outbox ENUM('text','image','audio','document')
  NOT NULL DEFAULT 'text';
