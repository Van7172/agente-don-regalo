# RAG incremental de productos

## Fuente y alcance

La fuente de verdad es MySQL, mediante `GET /productos/export`. Solo se indexan
productos que el proveedor considera publicables:

- `estado_producto = 1`
- `is_complemento = 0`

Qdrant es un índice derivado. Nunca decide de forma definitiva precio, stock o
estado; los IDs recuperados se revalidan por MCP/API antes de ofrecerlos.

## Documento semántico

Cada producto genera un solo documento:

```text
Producto: {nombre}
Categoría: {categoría}
Ocasiones: {ocasiones}
Descripción corta: {descripción corta sin HTML}
Descripción: {descripción sin HTML}
Tags: {tags}
```

Precio, stock, IDs, URLs y estado quedan fuera del embedding. Cambiarlos solo
actualiza el payload. Nombre, categoría, ocasiones, descripciones o tags cambian
`content_hash` y fuerzan un embedding nuevo.

## Sincronización

`sync_qdrant.py` compara `content_hash`, modelo, dimensiones y versión de esquema
contra el payload existente:

1. punto nuevo o contenido/modelo/esquema distinto: genera embedding y hace upsert;
2. solo payload distinto: reemplaza payload sin llamar a OpenAI;
3. sin cambios: no escribe;
4. ID ausente del export: elimina el punto obsoleto.

La primera ejecución con `index_schema_version = 2` reindexa la colección una vez
para incorporar las huellas. Las siguientes ejecuciones son incrementales.

## Worker dirigido por eventos

Con `EMBEDDING_WORKER_ENABLED=1`, el agente consulta al CRM PHP por HTTP con el
token interno existente:

1. `POST /api/embedding-jobs/claim` reclama filas de forma transaccional;
2. el worker lee el detalle vigente desde la API de catálogo;
3. genera un vector solo si cambió el contenido semántico;
4. actualiza o elimina el punto en Qdrant;
5. `PATCH /api/embedding-jobs/{id}` persiste el vector binario y termina el job;
6. ante error, reencola con backoff hasta cinco intentos.

El CRM y MySQL permanecen en el hosting. EasyPanel nunca abre una conexión MySQL
remota. Los claims abandonados vuelven a `pending` después de diez minutos.

## Persistencia MySQL

La migración `database/mysql/001_producto_embeddings.sql` crea:

- `producto_embeddings`: caché, trazabilidad y estado por producto/idioma;
- `producto_embedding_jobs`: outbox para actualizaciones dirigidas por eventos.
- triggers sobre `productos` que encolan altas, cambios y despublicaciones sin
  efectuar llamadas HTTP dentro de MySQL.

La migración no debe ejecutarse desde el agente. Se aplica en la base del catálogo
durante el despliegue y requiere respaldo previo.

Orden de activación:

1. desplegar el CRM PHP con los endpoints de embedding;
2. respaldar MySQL y ejecutar la migración una sola vez;
3. desplegar el agente con el worker;
4. configurar `EMBEDDING_WORKER_ENABLED=1`;
5. comprobar que los jobs pasan de `pending` a `done`;
6. conservar `sync_qdrant.py` como reconciliación periódica.

## Operación

```powershell
python sync_qdrant.py
```

Debe programarse como reconciliación periódica incluso cuando se active el outbox.
El job necesita `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`,
`QDRANT_COLLECTION`, `EMBED_MODEL`, `EMBED_DIM` y `DONREGALO_API_BASE`.
