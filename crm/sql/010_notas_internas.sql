-- Notas internas de la conversación: lo que el equipo sabe y el cliente no ve.
--
-- Hoy TODO lo que un asesor escribe en el panel sale a WhatsApp. No hay forma de
-- dejar "pide factura, falta el RUC" o "ya se le cotizó, no repetir precio" para
-- el turno siguiente: o se lo manda al cliente, o se pierde. El resultado es que
-- el contexto vive en la cabeza de quien atendió y el relevo empieza de cero.
--
-- Tabla aparte y NO en `crm_messages` a propósito: todo lo que entra en el hilo
-- es candidato a salir por la Cloud API (el outbox lee de ahí, el agente compone
-- desde ahí). Una nota interna que comparta tabla con los mensajes es una nota
-- interna a un bug de distancia de acabar en el teléfono del cliente.

CREATE TABLE IF NOT EXISTS crm_conversation_notes (
  id_nota INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  id_conversation INT(11) NOT NULL,
  id_usuario INT(11) NULL COMMENT 'quién la escribió',
  nombre_usuario VARCHAR(190) NULL COMMENT 'copia del nombre, como en el historial de ventas',
  nota_texto TEXT NOT NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_nota),
  KEY idx_crm_notas_conv (id_conversation, id_nota),
  CONSTRAINT fk_crm_nota_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE,
  CONSTRAINT fk_crm_nota_conv
    FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
