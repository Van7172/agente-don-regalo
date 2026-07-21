-- Un mensaje del asesor se enviaba VARIAS veces.
--
-- Hay dos caminos que entregan la misma fila de `crm_outbox`:
--   1. el push directo del CRM al agente (`POST /internal/outbox/send`), y
--   2. el drenaje periódico del agente, cada 12s, que hace
--      `SELECT * FROM crm_outbox WHERE status_outbox = 'pending'`.
--
-- La fila queda en 'pending' durante TODA la llamada a la Cloud API — recién al
-- volver se marca 'sent'. Si esa llamada tarda más que el tick del drenaje (Meta
-- lento, o un adjunto, que tiene 60s de timeout), el drenaje ve la misma fila
-- 'pending' y la manda otra vez. Un asesor escribió "No disculpe. Somos de Lima"
-- y al cliente le llegó tres veces.
--
-- Ningún camino reclamaba la fila antes de enviarla: faltaba el estado
-- intermedio. Con 'sending', el claim es un UPDATE condicional y solo un worker
-- puede ganarlo (`WHERE status_outbox = 'pending'` bajo el lock de fila de
-- InnoDB); el resto se retira sin enviar nada.

ALTER TABLE crm_outbox
  MODIFY COLUMN status_outbox
    ENUM('pending','sending','sent','failed') NOT NULL DEFAULT 'pending';

-- Si el agente muere entre el claim y el envío, la fila se queda en 'sending'
-- para siempre. `listPendingOutbox` la devuelve a 'pending' pasados unos minutos:
-- es el índice que hace barata esa consulta.
ALTER TABLE crm_outbox
  ADD KEY idx_crm_outbox_status_fecha (status_outbox, fecha_creacion);
