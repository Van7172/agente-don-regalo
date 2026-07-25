-- RAG incremental del catálogo Don Regalo.
-- Ejecutar sobre la misma base que contiene `productos`.
--
-- El vector se conserva como caché/trazabilidad. Qdrant sigue siendo el índice
-- de similitud y MySQL la fuente de verdad de precio, stock y estado.

CREATE TABLE IF NOT EXISTS `producto_embeddings` (
    `id_producto` INT NOT NULL,
    `idioma` VARCHAR(10) NOT NULL DEFAULT 'es',
    `content_hash` CHAR(64) NOT NULL,
    `embedding_model` VARCHAR(64) NOT NULL,
    `dimensions` SMALLINT UNSIGNED NOT NULL,
    `embedding` MEDIUMBLOB DEFAULT NULL,
    `document_version` INT UNSIGNED NOT NULL DEFAULT 1,
    `status` ENUM('pending', 'processing', 'ready', 'error')
        NOT NULL DEFAULT 'pending',
    `last_error` TEXT DEFAULT NULL,
    `embedded_at` DATETIME DEFAULT NULL,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id_producto`, `idioma`),
    KEY `idx_producto_embeddings_status` (`status`, `updated_at`),
    CONSTRAINT `fk_producto_embeddings_producto`
        FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `producto_embedding_jobs` (
    `id_job` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `id_producto` INT NOT NULL,
    `reason` VARCHAR(50) NOT NULL DEFAULT 'product_changed',
    `status` ENUM('pending', 'processing', 'done', 'error')
        NOT NULL DEFAULT 'pending',
    `attempts` SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    `available_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `claimed_at` DATETIME DEFAULT NULL,
    `finished_at` DATETIME DEFAULT NULL,
    `last_error` TEXT DEFAULT NULL,
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id_job`),
    KEY `idx_producto_embedding_jobs_claim`
        (`status`, `available_at`, `id_job`),
    KEY `idx_producto_embedding_jobs_producto` (`id_producto`),
    CONSTRAINT `fk_producto_embedding_jobs_producto`
        FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Los triggers solo encolan; nunca hacen llamadas HTTP dentro de MySQL.
-- Cambios de precio/stock también generan trabajo para refrescar el payload,
-- pero el worker decidirá por content_hash que no debe regenerar el vector.
DELIMITER $$

CREATE TRIGGER `trg_productos_embedding_after_insert`
AFTER INSERT ON `productos`
FOR EACH ROW
BEGIN
    IF NEW.`estado_producto` = 1 AND NEW.`is_complemento` = 0 THEN
        INSERT INTO `producto_embedding_jobs` (`id_producto`, `reason`)
        VALUES (NEW.`id_producto`, 'product_created');
    END IF;
END$$

CREATE TRIGGER `trg_productos_embedding_after_update`
AFTER UPDATE ON `productos`
FOR EACH ROW
BEGIN
    IF NOT (OLD.`nombre_producto` <=> NEW.`nombre_producto`)
       OR NOT (OLD.`descripcion_corta_producto` <=> NEW.`descripcion_corta_producto`)
       OR NOT (OLD.`descripcion_producto` <=> NEW.`descripcion_producto`)
       OR NOT (OLD.`tags_producto` <=> NEW.`tags_producto`)
       OR NOT (OLD.`id_categoria` <=> NEW.`id_categoria`)
       OR NOT (OLD.`precio_producto` <=> NEW.`precio_producto`)
       OR NOT (OLD.`stock_producto` <=> NEW.`stock_producto`)
       OR NOT (OLD.`estado_producto` <=> NEW.`estado_producto`)
       OR NOT (OLD.`is_complemento` <=> NEW.`is_complemento`)
       OR NOT (OLD.`url_producto` <=> NEW.`url_producto`) THEN
        INSERT INTO `producto_embedding_jobs` (`id_producto`, `reason`)
        VALUES (
            NEW.`id_producto`,
            CASE
                WHEN NEW.`estado_producto` = 1 AND NEW.`is_complemento` = 0
                    THEN 'product_changed'
                ELSE 'product_unpublished'
            END
        );
    END IF;
END$$

DELIMITER ;

-- Backfill inicial: un trabajo por producto publicable.
INSERT INTO `producto_embedding_jobs` (`id_producto`, `reason`)
SELECT p.`id_producto`, 'initial_backfill'
FROM `productos` p
WHERE p.`estado_producto` = 1
  AND p.`is_complemento` = 0
  AND NOT EXISTS (
      SELECT 1
      FROM `producto_embedding_jobs` j
      WHERE j.`id_producto` = p.`id_producto`
        AND j.`status` IN ('pending', 'processing')
  );
