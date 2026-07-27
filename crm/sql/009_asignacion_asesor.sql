-- Quién tiene esta conversación.
--
-- "Tomar conversación" solo cambiaba `mode_conversation` a HUMAN: el chat pasaba
-- a manos humanas SIN decir de qué humano. Dos asesores podían abrir el mismo
-- lead y escribirle cosas distintas — el claim del outbox impide que el MISMO
-- mensaje salga dos veces, no que dos personas atiendan al mismo cliente a la
-- vez. Y al revés: un chat en HUMAN sin dueño es un chat del que nadie se siente
-- responsable hasta que el releaser lo devuelve al bot a los 20 minutos.
--
-- El nombre se copia (no se resuelve por JOIN contra `usuarios`) por lo mismo
-- que en `crm_ventas_historiales`: es un dato de auditoría, y si mañana ese
-- usuario se borra o se renombra, la conversación debe seguir diciendo quién la
-- atendió ese día.

ALTER TABLE crm_conversations
  ADD COLUMN id_usuario_asignado     INT(11)      NULL COMMENT 'usuarios.id_usuario del asesor que tiene el chat',
  ADD COLUMN nombre_usuario_asignado VARCHAR(190) NULL COMMENT 'copia del nombre; auditoría, no se resuelve por JOIN',
  ADD COLUMN fecha_asignacion        DATETIME     NULL COMMENT 'cuándo lo tomó (para medir cuánto lleva retenido)';

-- Para el filtro "Mis chats" sin escanear la tabla entera.
ALTER TABLE crm_conversations
  ADD KEY idx_crm_conv_asignado (id_tenant, id_usuario_asignado);