# API Atención al Cliente — `clienteApiApp`

API REST JSON para agentes de atención al cliente, catálogo y seguimiento de pedidos.

Guía del modelo de catálogo (categorías, filtros, landings, ocasiones) y uso correcto de las APIs: **[`CATALOGO.md`](CATALOGO.md)**.

## Base URL

| Entorno     | Prefijo |
|-------------|---------|
| Localhost   | `http://localhost/donregalo/clienteApiApp/api` |
| Producción  | `https://{dominio}/clienteApiApp/api` |

Todas las rutas de este documento se expresan relativas a ese prefijo  
(ej.: `GET /productos/buscar` → `…/api/productos/buscar`).

## Formato de respuesta

```json
{
  "success": true,
  "message": "OK",
  "data": { },
  "pagination": {
    "total": 100,
    "per_page": 12,
    "current_page": 1,
    "last_page": 9
  }
}
```

- `data` solo aparece si hay payload.
- `pagination` solo en listados paginados.
- Códigos HTTP habituales: `200`, `201`, `400`, `404`, `405`, `422`, `500`.
- CORS: `Access-Control-Allow-Origin: *` (también responde a `OPTIONS`).

## Autenticación

No hay autenticación por API key en el front controller actual. Los endpoints sensibles usan verificación de negocio (ej. email + código en rastreo de pedidos).

## Configuración

1. Copiar `config/secrets.example.php` → `config/secrets.php` (o usar env `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS`, `DB_PORT`).
2. Para consultas de contacto: ejecutar `setup.sql` (tabla `consultas_clientes`).

---

## Índice de endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/catalogo/navegacion` | **Tool principal del bot:** categorías + filtros + ocasiones + landings (datos reales) |
| GET | `/categorias` | Árbol de categorías |
| GET | `/categorias/{slug}/productos` | Productos por categoría |
| GET | `/productos/buscar` | Búsqueda / listado de productos |
| GET | `/productos/destacados` | Productos destacados |
| GET | `/productos/ofertas` | Productos en oferta |
| GET | `/productos/activos` | Validar IDs activos (catálogo) |
| GET | `/productos/export` | Export para indexación vectorial |
| GET | `/productos/{id}` | Detalle de producto |
| GET | `/filtros` | Filtros y subfiltros |
| GET | `/ocasiones` | Listado de ocasiones |
| GET | `/ocasiones/{id}/productos` | Productos por ocasión |
| GET | `/distritos` | Distritos con cobertura de envío |
| GET | `/distritos/{id}/disponibilidad` | Disponibilidad / tarifa de un distrito |
| POST | `/pedidos/temporales` | Crear pedido temporal (agente IA) |
| POST | `/pedidos/rastrear` | Rastreo completo (email + código) |
| GET | `/pedidos/{codigo}/estado` | Estado público del pedido |
| POST | `/consultas` | Registrar consulta de cliente |
| GET | `/metodos-pago` | Métodos de pago activos |
| GET | `/blog` | Listado de posts |
| GET | `/blog/{slug}` | Detalle de post |
| GET | `/configuracion/tipo-cambio` | Tipo de cambio USD→PEN |

---

## Catálogo (agente IA)

### `GET /catalogo/navegacion`

Endpoint pensado como **primer tool** del bot. Devuelve la taxonomía real del sitio para que **no invente** opciones (ej. “desayuno dulce/salado”).

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `incluir_temporales` | bool | `false` | Incluye campañas (`temporal_categoria=1`: Día del Padre, etc.) |

**`data` contiene:**

| Clave | Contenido |
|-------|-----------|
| `instrucciones_agente` | Reglas de uso (solo usar slugs de este payload) |
| `categorias[]` | Padres con `subcategorias[]` y `landings[]` |
| `filtros[]` | Padres con `subfiltros[]` (destinatario, ocasión-filtro, flores…) |
| `ocasiones[]` | Catálogo `ocasiones` + hint `como_buscar` |
| `landings[]` | Cruces `categorias_filtros` (ej. Desayunos de Cumpleaños) |

**Cómo mapear lo que ve el cliente en la web**

| UI sitio | Origen BD / API |
|----------|-----------------|
| Menú Desayunos / Peluches | `categorias` padre |
| Desayunos Criollos, Light, Amor… | `categorias.subcategorias` |
| Desayunos de Cumpleaños / para Niños | `landings` (categoría × filtro) |
| Más buscados: Para Hombre, Girasoles… | `filtros.subfiltros` |
| Barra Cumpleaños / Aniversario | mezcla de filtros landings y `ocasiones` |

**Flujo bot sugerido**

1. `GET /catalogo/navegacion`
2. Ofrecer solo nombres de ese JSON (numerados)
3. Listar productos:
   - Subcategoría → `GET /productos/buscar?categoria=desayunos-criollos`
   - Landing → `GET /productos/buscar?landing=desayunos-de-cumpleanos`
   - Solo filtro → `GET /productos/buscar?filtro=para-hombre`
   - Ocasión → `GET /productos/buscar?ocasion=1`

---

## Categorías

### `GET /categorias`

Devuelve categorías activas en árbol (padres con `subcategorias`).

**Respuesta `data`:** array de categorías con `id_categoria`, `nombre_categoria`, `url_categoria`, `es_temporal`, `subcategorias[]`, etc.

### `GET /categorias/{slug}/productos`

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `page` | int | `1` | |
| `per_page` | int | `12` | máx. 60 |
| `orden` | `asc`\|`desc` | `asc` | Por precio |

**Respuesta `data`:** `{ categoria, productos[] }` + `pagination`.

---

## Productos

### `GET /productos/buscar`

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `q` | string | | Busca en nombre, descripción corta y tags |
| `categoria` | string | | Slug `url_categoria`. Si es padre, incluye **hijas** (ej. `desayunos` → criollos, light…) |
| `filtro` | string | | Slug `url_filtro` (`productos_filtros`), ej. `para-hombre`, `girasoles` |
| `landing` | string | | Slug `url_categoria_filtro` (misma lógica que `/c/...` en la web) |
| `ocasion` | int | | `id_ocasion` (`productos_ocasiones`) |
| `incluir_funebre` | bool | `false` | `1`/`true`/`si` para incluir categoría fúnebre |
| `min_precio` | float | | Precio final (con oferta si aplica) |
| `max_precio` | float | | |
| `orden` | `asc`\|`desc` | `asc` | Por precio final |
| `page` | int | `1` | |
| `per_page` | int | `12` | máx. 60 |

Solo productos con `estado_producto = 1` e `is_complemento = 0`.  
Si hay `landing`, no hace falta repetir `categoria`+`filtro` (ya vienen de `categorias_filtros`).

### `GET /productos/destacados`

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `limit` | int | `8` | máx. 50 |

### `GET /productos/ofertas`

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `page` | int | `1` | |
| `per_page` | int | `12` | máx. 60 |

Incluye `precio_original`, `precio_oferta`, `descuento_pct`.

### `GET /productos/activos`

Valida qué IDs de un lote siguen activos en catálogo (útil tras búsqueda vectorial / Qdrant).

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `ids` | string | | Lista separada por comas, ej. `1,2,3`. Máx. 100 IDs |

**Respuesta `data`:** array de enteros (`id_producto` activos). Sin `ids` → `[]`.

**Ejemplo:** `GET /productos/activos?ids=10,20,30`

### `GET /productos/export`

Export paginado del catálogo activo para indexación vectorial (nombre, descripciones, categoría, ocasiones, tags, `es_funebre`, imagen).

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `page` | int | `1` | |
| `per_page` | int | `100` | máx. 200 |

### `GET /productos/{id}`

Detalle: descripciones, precios, stock, tags, categoría, galería de imágenes, ocasiones y hasta 4 relacionados.

Si no existe o está inactivo → `404`.

---

## Filtros

### `GET /filtros`

Árbol de filtros padre con `subfiltros[]` (`id_filtro`, `nombre_filtro`, `url_filtro`).

---

## Ocasiones

### `GET /ocasiones`

Listado ordenado (`id_ocasion`, `nombre_ocasion`, `descripcion_ocasion`).

### `GET /ocasiones/{id}/productos`

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `page` | int | `1` | |
| `per_page` | int | `12` | máx. 60 |

Si la ocasión no existe → `404`. Respuesta con meta de ocasión + productos + `pagination`.

---

## Distritos / envío

### `GET /distritos`

Distritos con cobertura y tarifa distinta de cero. Cada ítem incluye `tarifa_envio_distrito` y `moneda` (`USD`).

### `GET /distritos/{id}/disponibilidad`

```json
{
  "id_distrito": 1,
  "nombre": "...",
  "disponible": true,
  "tarifa_envio": 12.5,
  "moneda": "USD"
}
```

---

## Pedidos

### `POST /pedidos/temporales`

Crea un **pedido temporal** ya rellenado para que aparezca en el panel (**Pedidos temporales**). Pensado como tool del agente IA a partir de la conversación con el lead.

| Campo en BD | Valor |
|-------------|--------|
| `estado_pedido_temporal` | `1` (activo / visible en admin) |
| `codigo_pedido_temporal` | vacío (el link `/formulario-pedido/{code}` no se reabre) |
| Tabla `pedidos` | **No** se crea aún |
| Cuando ventas convierta | Usar `estado_pedido = Pendiente` (el agente no confirma pago) |

**Body JSON**

| Campo | Requerido | Notas |
|-------|-----------|-------|
| `nombre_cliente` | sí | Quien envía |
| `apellidos_cliente` | sí | |
| `telefono_cliente` | sí | |
| `email_cliente` | sí | Busca cliente existente; si no, lo crea |
| `nombre_destinatario` | sí | |
| `apellidos_destinatario` | sí | |
| `telefono_destinatario` | sí | |
| `fecha_entrega` | sí | `YYYY-MM-DD` |
| `hora_entrega` | sí | Franjas: `07:00`, `10:00`, `13:00` (o `01:00`), `16:00` (o `04:00`) |
| `dedicatoria` | sí | Máx. 2000 caracteres |
| `id_distrito` | sí | Debe tener cobertura (`GET /distritos`) |
| `direccion` | sí | Dirección y referencias |
| `tipo` | sí | `0` = casa, `1` = oficina (también `casa` / `oficina`) |
| `id_producto` | no | Producto activo opcional |
| `observaciones` | no | Se antepone `[Agente IA]` si falta |
| `delivery` | no | Monto en **PEN**; se guarda convertido a USD como en el panel |

**Ejemplo**

```http
POST /api/pedidos/temporales
Content-Type: application/json

{
  "nombre_cliente": "Ana",
  "apellidos_cliente": "Pérez",
  "telefono_cliente": "999888777",
  "email_cliente": "ana@email.com",
  "nombre_destinatario": "Luis",
  "apellidos_destinatario": "Gómez",
  "telefono_destinatario": "999111222",
  "fecha_entrega": "2026-07-20",
  "hora_entrega": "10:00",
  "dedicatoria": "Feliz cumpleaños",
  "id_distrito": 5,
  "direccion": "Av. Principal 123, ref. parque",
  "tipo": 0,
  "id_producto": 1235,
  "observaciones": "Lead por WhatsApp, prefiere entrega mañana",
  "delivery": 15.00
}
```

**Respuesta `201`**

```json
{
  "success": true,
  "message": "Pedido temporal creado. Ventas lo convertirá con estado Pendiente al confirmar datos/pago.",
  "data": {
    "id_pedido_temporal": 123,
    "id_cliente": 456,
    "id_producto": 1235,
    "nombre_producto": "...",
    "id_distrito": 5,
    "nombre_distrito": "Miraflores",
    "estado_pedido_temporal": 1,
    "nota_estado_pedido": "Pendiente (al convertir en admin; el agente no confirma pago)",
    "fecha_entrega": "2026-07-20",
    "hora_entrega": "10:00:00"
  }
}
```

Validación fallida → `422`. Distrito sin cobertura o producto inválido → `422`.

### `POST /pedidos/rastrear`

Body JSON (o form):

| Campo | Requerido | Notas |
|-------|-----------|-------|
| `email` | sí | Email del cliente del pedido |
| `codigo` | sí | Código del pedido |

Devuelve estado, preparación, fecha, datos de entrega (destinatario/dirección/horario) y productos del pedido. Sin coincidencia email+código → `404`.

### `GET /pedidos/{codigo}/estado`

Estado público sin datos personales:

```json
{
  "codigo": "...",
  "estado": "Pagado",
  "preparacion": "Pendiente",
  "fecha": "2026-07-01 10:00:00"
}
```

---

## Consultas (contacto)

### `POST /consultas`

Requiere tabla `consultas_clientes` (`setup.sql`).

| Campo | Requerido | Notas |
|-------|-----------|-------|
| `nombre` | sí | |
| `email` | sí | Validado |
| `mensaje` | sí | Máx. 2000 caracteres |
| `telefono` | no | |
| `asunto` | no | |

Éxito → `201` con `{ "id": <id_consulta> }`. Validación fallida → `422`.

---

## Métodos de pago

### `GET /metodos-pago`

Métodos con `activo_metodo_pago = 'S'`: nombre, descripción, imagen e icono.

---

## Blog

### `GET /blog`

| Query | Tipo | Default | Notas |
|-------|------|---------|-------|
| `page` | int | `1` | |
| `per_page` | int | `10` | máx. 30 |
| `categoria` | int | | `id_categoria_blog` |

### `GET /blog/{slug}`

Detalle por `url_post`. No encontrado → `404`.

---

## Configuración

### `GET /configuracion/tipo-cambio`

Lee `configuracion.nombre_configuracion = 'TIPO_CAMBIO'`. Si no hay fila, fallback `3.50`.

```json
{
  "tipo_cambio": 3.5,
  "moneda_base": "USD",
  "moneda_local": "PEN"
}
```

---

## Notas para consumidores (agentes / apps)

1. **Taxonomía:** antes de ofrecer tipos de producto, llamar `GET /catalogo/navegacion`. No inventar categorías.
2. **Moneda:** precios de catálogo y tarifas de distrito se exponen en lógica alineada a USD; convertir con `/configuracion/tipo-cambio` si se muestra en PEN.
3. **Funebre:** la búsqueda excluye categoría `arreglos-funebres` (y subcategorías) salvo `incluir_funebre=1`.
4. **IDs vectoriales:** tras Qdrant u otra búsqueda semántica, filtrar con `/productos/activos?ids=…`.
5. **Export / listados:** el slug de categoría es siempre `url_categoria` (mismo nombre que en `/categorias`).
6. **Imágenes null:** si un producto no tiene archivo de imagen, los campos de URL son `null` (HTTP 200 JSON válido; no error).
7. Router: `index.php` + rewrite en `.htaccess` (`RewriteRule → index.php`).
