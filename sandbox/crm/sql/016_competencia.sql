-- Catálogo público de competidores (Fase 2 de inteligencia comercial).
--
-- El scrape vive en el agente; el CRM solo guarda y pinta. Cada fila de producto
-- lleva URL de origen y fecha de captura: sin eso no se muestra (mismo principio
-- que prices_are_sourced). Las filas NO se borran: `activo=0` + visto_por_ultima_vez
-- dice qué dejaron de ofrecer.
--
-- Competidores iniciales (lista del negocio, no inventada):
--   Rosatel, Magia.pe, Sorprende Lima.

CREATE TABLE IF NOT EXISTS crm_competidores (
  id_competidor INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  slug_competidor VARCHAR(64) NOT NULL
    COMMENT 'clave estable: rosatel | magia | sorprendelima',
  nombre_competidor VARCHAR(128) NOT NULL,
  dominio_competidor VARCHAR(255) NOT NULL
    COMMENT 'origen canónico, con https://',
  activo TINYINT(1) NOT NULL DEFAULT 1,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_competidor),
  UNIQUE KEY uq_crm_competidor_slug (id_tenant, slug_competidor),
  CONSTRAINT fk_crm_competidor_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS crm_competencia_productos (
  id_competencia_producto INT(11) NOT NULL AUTO_INCREMENT,
  id_tenant INT(11) NOT NULL,
  id_competidor INT(11) NOT NULL,
  clave_externa VARCHAR(128) NOT NULL
    COMMENT 'id del producto en la tienda origen',
  nombre_producto VARCHAR(255) NOT NULL,
  precio_sol DECIMAL(10,2) NULL
    COMMENT 'precio de catálogo en soles; NULL si no se pudo leer',
  precio_tachado_sol DECIMAL(10,2) NULL
    COMMENT 'precio anterior si hay promo',
  url VARCHAR(1024) NOT NULL
    COMMENT 'obligatoria: sin URL de origen no se muestra',
  capturado_en DATETIME NOT NULL
    COMMENT 'primera vez que lo vimos',
  visto_por_ultima_vez DATETIME NOT NULL
    COMMENT 'último crawl que lo encontró',
  activo TINYINT(1) NOT NULL DEFAULT 1
    COMMENT '0 = ya no apareció en el catálogo origen',
  match_id_producto INT(11) NULL
    COMMENT 'id_producto propio más cercano en Qdrant, si hay',
  match_score DECIMAL(6,4) NULL
    COMMENT 'similitud del vecino; NULL si no se pudo matchear',
  match_nombre VARCHAR(255) NULL,
  es_hueco TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '1 = no tenemos equivalente cercano',
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id_competencia_producto),
  UNIQUE KEY uq_crm_comp_prod (id_tenant, id_competidor, clave_externa),
  KEY idx_crm_comp_hueco (id_tenant, es_hueco, activo, visto_por_ultima_vez),
  KEY idx_crm_comp_competidor (id_competidor, activo),
  CONSTRAINT fk_crm_comp_prod_tenant
    FOREIGN KEY (id_tenant) REFERENCES crm_tenants (id_tenant) ON DELETE CASCADE,
  CONSTRAINT fk_crm_comp_prod_competidor
    FOREIGN KEY (id_competidor) REFERENCES crm_competidores (id_competidor) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed: tenant don-regalo (id 1 en local; en prod se resuelve por slug).
INSERT INTO crm_competidores (id_tenant, slug_competidor, nombre_competidor, dominio_competidor)
SELECT t.id_tenant, v.slug, v.nombre, v.dominio
  FROM crm_tenants t
  JOIN (
    SELECT 'rosatel' AS slug, 'Rosatel' AS nombre, 'https://www.rosatel.pe' AS dominio
    UNION ALL
    SELECT 'magia', 'Magia.pe', 'https://magia.pe'
    UNION ALL
    SELECT 'sorprendelima', 'Sorprende Lima', 'https://www.sorprendelima.pe'
  ) v
 WHERE t.slug_tenant = 'don-regalo'
    ON DUPLICATE KEY UPDATE
      nombre_competidor = VALUES(nombre_competidor),
      dominio_competidor = VALUES(dominio_competidor),
      activo = 1;
