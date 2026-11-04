-- Respuestas rápidas editables desde el panel.
--
-- Vivían fijas en inbox.js: cambiar una coma exigía subir código al hosting.
-- Las tres de abajo son copia literal de las que había, generadas desde ese
-- archivo. Los saltos van como \n para que el checkout de git en Windows no les
-- meta un retorno de carro que acabaría llegando al cliente.
--
-- Se desactivan, no se borran. Repetible: NOT EXISTS no resucita ni pisa una
-- respuesta que el equipo ya editó o desactivó.

CREATE TABLE IF NOT EXISTS crm_respuestas_rapidas (
  id_respuesta INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  comando_respuesta VARCHAR(32) NOT NULL COMMENT 'Lo que se escribe tras la barra: /formulario',
  titulo_respuesta VARCHAR(120) NOT NULL,
  texto_respuesta TEXT NOT NULL,
  activa_respuesta TINYINT(1) NOT NULL DEFAULT 1,
  id_usuario_actualizacion INT(11) NULL,
  nombre_usuario_actualizacion VARCHAR(190) NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_respuesta),
  UNIQUE KEY uq_crm_respuesta_comando (id_tenant, comando_respuesta),
  CONSTRAINT fk_crm_respuesta_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO crm_respuestas_rapidas (id_tenant, comando_respuesta, titulo_respuesta, texto_respuesta)
SELECT t.id_tenant, 'formulario', 'Pedir formulario de pedido',
  'Llene los siguientes datos en este formulario para registrar su pedido porfavor. Es importante nos avise una vez lo termine. No coloque comillas simples '' ni emojis.'
FROM crm_tenants t
WHERE t.slug_tenant = 'don-regalo'
  AND NOT EXISTS (
    SELECT 1 FROM crm_respuestas_rapidas x
    WHERE x.id_tenant = t.id_tenant AND x.comando_respuesta = 'formulario'
  );

INSERT INTO crm_respuestas_rapidas (id_tenant, comando_respuesta, titulo_respuesta, texto_respuesta)
SELECT t.id_tenant, 'origen', 'Encuesta: ¿dónde nos encontraste?',
  'Buenas tardes, Queríamos hacerle una consulta rápida 🙏 ¿Dónde nos encontraste? 👀\n1️⃣ Google\n2️⃣ Instagram\n3️⃣ TikTok\n4️⃣ Facebook\n5️⃣ RAPPI'
FROM crm_tenants t
WHERE t.slug_tenant = 'don-regalo'
  AND NOT EXISTS (
    SELECT 1 FROM crm_respuestas_rapidas x
    WHERE x.id_tenant = t.id_tenant AND x.comando_respuesta = 'origen'
  );

INSERT INTO crm_respuestas_rapidas (id_tenant, comando_respuesta, titulo_respuesta, texto_respuesta)
SELECT t.id_tenant, 'ubicacion', 'Pedir pin de Google Maps (MZ/Lte)',
  'Dado que la ubicación es por MZ y Lte le pido compartir la ubicación exacta del lugar de entrega por Google maps, ya que puede tomar mucho tiempo llegar a la ubicación y la idea es llegar a tiempo.'
FROM crm_tenants t
WHERE t.slug_tenant = 'don-regalo'
  AND NOT EXISTS (
    SELECT 1 FROM crm_respuestas_rapidas x
    WHERE x.id_tenant = t.id_tenant AND x.comando_respuesta = 'ubicacion'
  );

