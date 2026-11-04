-- El producto vendido, por id y no solo por nombre.
--
-- `producto_venta_historial` guarda el NOMBRE del producto, y era lo único que
-- identificaba lo vendido. Para "qué se vende más" hay que agrupar por texto
-- libre: un renombre en el catálogo parte la misma fila en dos productos
-- distintos, y cruzar contra la API —que va por `id_producto` en todas partes—
-- no se puede. Cualquier lectura por producto (huecos de catálogo, qué trae
-- cada anuncio) nace rota sin esta columna.
--
-- El dato no hay que inventarlo ni capturarlo de nuevo: `build_sale` mete
-- `id_producto` en el snapshot desde el principio (app/harness/sale.py), así que
-- el backfill sale completo. Lo que faltaba era sacarlo del JSON a una columna
-- indexable — buscar dentro de `snapshot_venta_historial` obliga a escanear la
-- tabla entera en cada reporte.
--
-- NULL es legítimo y hay que esperarlo: las ventas manuales del asesor
-- (migración 012) las escribe una persona sobre un producto que puede no estar
-- en el catálogo, y el bot tampoco siempre resuelve el id. Toda consulta que
-- agrupe por producto tiene que contemplar esas filas en vez de perderlas.

ALTER TABLE crm_ventas_historiales
  ADD COLUMN id_producto_venta_historial INT(11) NULL
    COMMENT 'id_producto del catálogo; NULL si la venta fue manual o no se resolvió';

-- "Qué producto se vendió más este mes" sin escanear la tabla entera.
ALTER TABLE crm_ventas_historiales
  ADD KEY idx_ventas_historiales_producto
    (id_tenant, id_producto_venta_historial, fecha_cierre_venta_historial);

-- Backfill desde el snapshot. Repetible: solo toca las filas que siguen en NULL.
--
-- El doble filtro no es redundante. `JSON_EXTRACT` de una clave ausente devuelve
-- SQL NULL, pero de una clave presente con valor `null` devuelve el JSON null,
-- que NO es SQL NULL y pasaría el primer filtro para acabar en un CAST a 0.
-- Una venta con producto 0 es peor que una sin producto: se agrupa como si
-- fuera un producto real.
UPDATE crm_ventas_historiales
   SET id_producto_venta_historial = CAST(
         JSON_UNQUOTE(JSON_EXTRACT(snapshot_venta_historial, '$.id_producto')) AS UNSIGNED
       )
 WHERE id_producto_venta_historial IS NULL
   AND JSON_VALID(snapshot_venta_historial)
   AND JSON_EXTRACT(snapshot_venta_historial, '$.id_producto') IS NOT NULL
   AND JSON_TYPE(JSON_EXTRACT(snapshot_venta_historial, '$.id_producto')) <> 'NULL';
