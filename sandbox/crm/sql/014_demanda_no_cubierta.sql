-- Qué nos piden los clientes que no tenemos.
--
-- Es la señal más directa de qué producto lanzar, y hoy se tira entera. Cuando
-- una búsqueda no encuentra nada de lo que el cliente pidió, el executor marca
-- el resultado `aproximado: true` (app/tools/executor.py) para que el bot diga
-- "te muestro lo más cercano". Esa marca vive dentro del resultado de la tool
-- durante ese turno y muere ahí: no hay tabla, ni métrica, ni traza. Sabemos
-- decirle al cliente que no lo tenemos y no sabemos cuántos lo pidieron.
--
-- Como el `referral` de los anuncios (migración 007), NO es recuperable hacia
-- atrás: el histórico de conversaciones no guarda qué devolvió cada búsqueda.
-- Solo cuenta desde el despliegue en adelante, que es la razón de meterla ya
-- aunque el panel que la lee venga después.
--
-- `consulta_demanda` es el ARGUMENTO de la tool, no el mensaje del cliente. Esa
-- distinción es deliberada por dos motivos: el mensaje trae prosa, saludos y a
-- veces la dirección o el teléfono del destinatario —PII que no pinta nada en
-- una tabla de análisis—, y además el argumento ya viene destilado a términos,
-- que es justo lo que hay que agrupar. Guardar el mensaje crudo daría una tabla
-- imposible de agregar y llena de datos personales.
--
-- Sin unicidad ni contador: cada miss es una fila. Dos personas que piden lo
-- mismo el mismo día son dos señales, no una — y agregando por fecha se ve si
-- algo sube (una tendencia, una fecha del calendario) o si fue un caso suelto.
-- Un contador acumulado borraría el cuándo, que es la mitad de la información.

CREATE TABLE IF NOT EXISTS crm_demanda_no_cubierta (
  id_demanda INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  id_conversation INT(11) NULL
    COMMENT 'de qué chat salió; NULL si la conversación se borró',
  consulta_demanda VARCHAR(255) NOT NULL
    COMMENT 'términos buscados (argumento de la tool), nunca el mensaje crudo',
  categoria_demanda VARCHAR(120) NULL
    COMMENT 'url_categoria pedida, si el cliente nombró una',
  resultado_demanda ENUM('vacio','aproximado') NOT NULL
    COMMENT 'vacio: ni Qdrant devolvió algo. aproximado: solo alternativas',
  n_resultados_demanda SMALLINT(6) NOT NULL DEFAULT 0
    COMMENT 'cuántas alternativas se ofrecieron (0 en vacio)',
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_demanda),
  -- Las dos lecturas del panel: "lo más pedido del mes" y la serie temporal.
  KEY idx_demanda_fecha (id_tenant, fecha_creacion),
  KEY idx_demanda_consulta (id_tenant, consulta_demanda, fecha_creacion),
  CONSTRAINT fk_demanda_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE,
  -- SET NULL y no RESTRICT: si se borra una conversación, la señal de demanda
  -- sigue siendo válida por sí sola. Perderla sería tirar el dato por limpiar.
  CONSTRAINT fk_demanda_conversation
    FOREIGN KEY (id_conversation) REFERENCES crm_conversations (id_conversation)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
