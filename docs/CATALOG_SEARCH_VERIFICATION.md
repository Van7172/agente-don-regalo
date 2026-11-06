# Búsqueda y verificación de productos

El caso de una clienta que pidió desayuno criollo dejó tres fallos: se listó la
categoría general, un filtro de cumpleaños excluyó una ficha sin etiquetas, y se
identificó una captura con el nombre de un producto mostrado anteriormente.

## Comportamiento

- El texto del cliente viaja por contexto a las herramientas. El núcleo comercial
  se busca literalmente antes de recurrir a vecinos semánticos. Una consulta
  genérica elegida por el modelo no borra una característica específica.
- Los candidatos se ordenan antes del límite visible y de la validación de fotos.
  La respuesta presenta como máximo tres opciones. Si faltan características,
  el texto de salida las anuncia como alternativas.
- La ocasión sirve para ordenar. Una lista de ocasiones vacía no significa que
  el producto sea incompatible. Los destinatarios expresos incompatibles se
  descartan por nombre; se admiten regalos neutros.
- `catalog_query` conserva el pedido cuando se añade ocasión/destinatario. Una
  corrección permite volver a ver el producto. Solo «más/otras opciones» excluye
  los IDs anteriores, antes de validar fotos, para completar el cupo con nuevos.
- El especialista tiene hasta dos herramientas secuenciales y tres rondas. Una
  segunda búsqueda o ficha reemplaza los candidatos de la primera consulta.

## Capturas

La visión transcribe únicamente la última imagen y no recibe los nombres de
productos anteriores. El título, si es legible, debe coincidir exactamente con
una ficha. Sin título se buscan candidatos y se consultan hasta dos fichas:
se requieren al menos dos frases distintas verificadas, con un mínimo del 75 %
de sus palabras distintivas presentes, y una ventaja sobre el segundo candidato.
Los nombres, precios e IDs de salida proceden de la ficha del catálogo.

Si hay empate, texto insuficiente o error de consulta, se pide nombre/enlace.
Una transcripción errónea sigue siendo posible; el agente no identifica productos
solo por semejanza visual.

## Metadatos de ocasión

El indexador registra por separado `ocasiones_catalogo_ids` y
`ocasiones_inferidas_ids`. Las últimas se derivan de menciones explícitas en el
título o descripción corta; no se escriben etiquetas en la tienda.

Para reparar una ficha ya indexada desde su detalle vigente:

```powershell
.\.venv\Scripts\python.exe scripts/refresh_catalog_occasions.py --ids 235
.\.venv\Scripts\python.exe scripts/refresh_catalog_occasions.py --ids 235 --apply
```

La actualización solo cambia tres claves de metadatos de Qdrant y verifica la
lectura posterior. No genera embeddings ni modifica precio, stock o vectores.

## Validación

`tests/test_catalog_request_verification.py` cubre el diálogo de criollo,
correcciones, destinatarios, candidatos fuera de la primera página visible,
fichas sin ocasión, identificación ambigua y verificación en una segunda consulta.
El smoke de visión usa una imagen sintética con descripción pública, sin datos
de clientes. Antes del despliegue se exige el gate completo del repositorio.
