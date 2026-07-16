# Catálogo Don Regalo — Guía para APIs y agentes

Documento de referencia sobre cómo está modelado el catálogo en BD y **cómo deben usarse las APIs de `clienteApiApp`** para devolver datos reales (sin inventar tipologías).

Base URL (ejemplos):

- Local: `http://localhost/donregalo/clienteApiApp/api`
- Producción: `https://{dominio}/clienteApiApp/api`

Detalle de contratos HTTP: ver [`API.md`](API.md).

---

## 1. El problema que resuelve esta guía

El sitio organiza regalos con **tres ejes distintos** (más un cruce SEO). Si el agente los mezcla o inventa menús (“desayuno dulce / salado / gourmet”), ofrece opciones que **no existen** en BD.

Regla de oro:

> **Solo ofrecer nombres y slugs que vengan de la API.**  
> Primer tool recomendado: `GET /catalogo/navegacion`.

---

## 2. Modelo de datos (BD)

```text
┌─────────────────┐         ┌─────────────────┐
│   categorias    │         │     filtros     │
│  (árbol 2 niv.) │         │  (árbol 2 niv.) │
└────────┬────────┘         └────────┬────────┘
         │                           │
         │    ┌──────────────────────┤
         │    │  categorias_filtros  │  ← landings SEO (/c/slug)
         │    │  (categoría × filtro)│
         │    └──────────────────────┘
         │
         ▼
┌─────────────────┐    N:M    ┌─────────────────┐
│    productos    │◄─────────►│ productos_filtros│
│ id_categoria FK │           └─────────────────┘
└────────┬────────┘
         │ N:M
         ▼
┌─────────────────┐
│productos_ocasiones│──► ocasiones
└─────────────────┘
```

### 2.1 `categorias` — menú y submenú del catálogo

Campo clave: **`id_parent`**.

| Valor | Significado | Ejemplo |
|-------|-------------|---------|
| `0` | Categoría **raíz** (menú principal) | Desayunos, Arreglos Florales, Peluches, Cestas |
| `≠ 0` | **Subcategoría** (hijo del padre indicado) | Desayunos Criollos → padre Desayunos (`id_parent = 2`) |

Otros campos útiles:

| Campo | Uso |
|-------|-----|
| `estado` | `1` = visible; la API solo lista activos |
| `temporal_categoria` | `1` = campaña (Día del Padre, San Valentín…). No es taxonomía permanente |
| `url_categoria` | Slug canónico (ej. `desayunos`, `desayunos-criollos`) |

**Relación con productos:** cada producto tiene un solo `productos.id_categoria` (suele apuntar a la **subcategoría**, no al padre).

Ejemplo Desayunos:

```text
Desayunos (id=2, id_parent=0)          ← raíz
 ├── Desayunos Criollos     (id_parent=2)
 ├── Desayunos Light        (id_parent=2)
 ├── Desayunos de Amor      (id_parent=2)
 └── Desayunos Temáticos   (id_parent=2)
```

Cestas / Peluches pueden ser raíz **sin hijos**. Eso es normal.

### 2.2 `filtros` — facetas transversales (“Más buscados”)

También usan `id_parent`:

| Padre (ejemplos) | Hijos (ejemplos) |
|------------------|------------------|
| Destinatario | Para Hombre, Para Mujer, Para Niños |
| Ocasion (filtro) | Cumpleaños, Aniversario, Condolencias, Nacimiento |
| Flores | Girasoles, Liliums, Rosas, Tulipanes |

Un producto puede tener **varios** filtros vía `productos_filtros` (N:M).

**Importante:** el filtro “Ocasion” **no es** la tabla `ocasiones`. Son sistemas distintos (ver §2.4).

### 2.3 `categorias_filtros` — landings (cruce categoría × filtro)

**No son subcategorías.** Son páginas curadas del tipo `/c/desayunos-de-cumpleanos`.

| Campo | Rol |
|-------|-----|
| `id_categoria` | Categoría ancla (casi siempre raíz, ej. Desayunos) |
| `id_filtro` | Filtro (ej. Cumpleaños) |
| `url_categoria_filtro` | Slug de la landing |
| `nombre_categoria_filtro` | Texto del sidebar |

En BD hay pocas landings (orden de magnitud: unas 5), por ejemplo:

| Nombre en web | = Categoría × Filtro |
|---------------|----------------------|
| Desayunos de Cumpleaños | Desayunos × Cumpleaños |
| Desayunos para Niños | Desayunos × Para Niños |
| Arreglos Florales de Cumpleaños | Arreglos × Cumpleaños |
| Arreglos Florales de Aniversario | Arreglos × Aniversario |
| Arreglos Florales para Nacimiento | Arreglos × Nacimiento |

**Cómo se calculan los productos de una landing** (sitio y API):

1. Productos cuya categoría es la ancla **o sus hijas**.
2. **Y** que tengan ese filtro en `productos_filtros`.

```text
landing = (categoría ∪ hijas) ∩ productos_con_filtro
```

### 2.4 `ocasiones` — etiquetas de ocasión (otro eje)

Tabla corta: Cumpleaños, Aniversario, Felicitación, Nacimiento, Agradecimiento, Negocios, Otros.

Relación N:M: `productos_ocasiones`.

Sirve para etiquetar productos; **no** alimenta el menú lateral de Desayunos ni sustituye a `categorias_filtros`.

---

## 3. Qué ve el usuario en la web vs qué es en BD

Página típica `/desayunos`:

| Bloque UI | Origen en BD |
|-----------|--------------|
| Menú superior: Desayunos, Arreglos… | `categorias` con `id_parent = 0` |
| Sidebar “DESAYUNOS”: Criollos, Light… | `categorias` hijas (`id_parent = 2`) |
| Sidebar “Desayunos de Cumpleaños / para Niños” | `categorias_filtros` (landings) |
| “Más buscados”: Para Hombre, Girasoles… | `filtros` hijos |
| Barra iconos ocasión | mezcla marketing / filtros / landings; para datos duros usar API |

Si el bot inventa “Desayuno dulce / salado / gourmet”, **no hay filas equivalentes** en `categorias` ni en landings.

---

## 4. APIs: qué usar y para qué

### 4.1 Tool principal del agente

#### `GET /catalogo/navegacion`

Devuelve en un solo JSON:

- `instrucciones_agente` — reglas de uso
- `categorias[]` — raíces con `subcategorias[]` y `landings[]`
- `filtros[]` — padres con `subfiltros[]`
- `ocasiones[]`
- `landings[]` — listado plano de cruces SEO

Query opcional: `incluir_temporales=1` para campañas (`temporal_categoria = 1`).

**Uso correcto del bot**

1. Llamar este endpoint al inicio del flujo de recomendación.
2. Numerar **solo** opciones presentes en la respuesta.
3. Según la elección del cliente, llamar a `/productos/buscar` con el parámetro adecuado (tabla abajo).

### 4.2 Listar productos

#### `GET /productos/buscar`

| Situación | Query | Ejemplo |
|-----------|-------|---------|
| Eligió subcategoría | `categoria={url_categoria}` | `?categoria=desayunos-criollos` |
| Eligió categoría padre | `categoria={url_categoria}` | `?categoria=desayunos` → incluye **hijas** |
| Eligió landing del sidebar | `landing={url_categoria_filtro}` | `?landing=desayunos-de-cumpleanos` |
| Eligió filtro transversal | `filtro={url_filtro}` | `?filtro=para-hombre` |
| Eligió ocasión (tabla ocasiones) | `ocasion={id_ocasion}` | `?ocasion=1` |
| Texto libre | `q=...` | Combinable con lo anterior |

Otros: `page`, `per_page`, `orden`, `min_precio`, `max_precio`, `incluir_funebre`.

### 4.3 Endpoints auxiliares (también válidos)

| Endpoint | Uso correcto | Cuidado |
|----------|--------------|---------|
| `GET /categorias` | Árbol categorías (sin landings) | No incluye `categorias_filtros` |
| `GET /categorias/{slug}/productos` | Productos de **esa** `id_categoria` exacta | Si `slug` es padre (ej. `desayunos`), **no** agrega hijas → puede devolver pocos o ningún producto |
| `GET /filtros` | Árbol de filtros | No son categorías |
| `GET /ocasiones` | Listado de ocasiones | Distinto del filtro “Ocasion” |
| `GET /ocasiones/{id}/productos` | Productos por ocasión | |
| `GET /productos/{id}` | Detalle | |
| `GET /productos/activos?ids=` | Validar IDs tras Qdrant | |

Para listar como el sitio bajo un padre o una landing, preferir **`/productos/buscar`**, no `/categorias/{slug}/productos`.

### 4.4 Slugs canónicos

En listados de productos el slug de categoría es siempre **`url_categoria`** (mismo nombre que en `/categorias`).  
Landings usan **`slug_landing` / `url_categoria_filtro`**.  
Filtros usan **`url_filtro`**.

---

## 5. Flujos recomendados para el bot

### Flujo A — Cliente dice “quiero un desayuno”

```text
1. GET /catalogo/navegacion
2. Localizar categoría url_categoria = "desayunos"
3. Ofrecer SOLO:
   - subcategorias[]  (Criollos, Light, Amor, Temáticos)
   - landings[]       (Cumpleaños, para Niños, …)
4. Según respuesta:
   - subcategoría → GET /productos/buscar?categoria=desayunos-criollos
   - landing      → GET /productos/buscar?landing=desayunos-de-cumpleanos
5. Mostrar 3–5 productos reales (nombre, precio, id)
6. Si elige producto → GET /productos/{id} para detalle
```

### Flujo B — Cliente dice “regalo para hombre”

```text
1. GET /catalogo/navegacion (o GET /filtros)
2. Ofrecer subfiltro url_filtro = "para-hombre"
3. GET /productos/buscar?filtro=para-hombre
```

### Flujo C — Búsqueda semántica (Qdrant / vectores)

```text
1. Obtener candidatos vectoriales (ids)
2. GET /productos/activos?ids=1,2,3,…   → quedarse solo con activos
3. Opcional: enriquecer con GET /productos/{id}
```

Nunca presentar un id inactivo o inventado.

### Flujo D — Crear pedido temporal (después de acordar producto/datos)

```text
POST /pedidos/temporales
```

Ver sección Pedidos en [`API.md`](API.md).  
`estado_pedido_temporal = 1`; el pago lo confirma ventas (`estado_pedido = Pendiente` al convertir).

---

## 6. Errores frecuentes (y cómo evitarlos)

| Error | Por qué falla | Qué hacer |
|-------|---------------|-----------|
| Inventar “desayuno dulce/salado” | No existen en BD | Usar `/catalogo/navegacion` |
| Tratar landing como `categoria=` | Landings no están en `categorias` | Usar `landing=` |
| `GET /categorias/desayunos/productos` y esperar todo el catálogo | Solo filtra `id_categoria` exacta | Usar `buscar?categoria=desayunos` |
| Confundir filtro Ocasión con tabla `ocasiones` | Son tablas distintas | Filtro → `filtro=`; ocasión → `ocasion=` |
| Mezclar fúnebres en búsquedas genéricas | Por defecto se excluyen | Solo con `incluir_funebre=1` |
| Usar `categoria_url` | Nombre antiguo | Usar `url_categoria` |

---

## 7. Resumen ejecutivo

1. **Categorías** = árbol del menú (`id_parent`).  
2. **Filtros** = facetas N:M con productos.  
3. **Landings** (`categorias_filtros`) = vistas SEO categoría × filtro; aparecen en el sidebar junto a subcategorías pero **no son** lo mismo.  
4. **Ocasiones** = etiquetas aparte.  
5. El agente debe **descubrir** el catálogo con `GET /catalogo/navegacion` y **listar** con `GET /productos/buscar` usando `categoria`, `landing`, `filtro` u `ocasion` según el tipo de opción elegida.

Así las respuestas del bot coinciden con lo que el cliente vería en [donregalo.pe](https://www.donregalo.pe).
