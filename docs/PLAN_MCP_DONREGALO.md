# Plan: MCP de Don Regalo

Servidor MCP dentro del proyecto principal (`donregalo/`), consumible desde Claude Code /
Cursor mientras se desarrolla **este** repo (`agente-don-regalo`).

Estado: **Fases 0–1 implementadas**; el bot ahora SÍ consume el MCP (opt-in). El rumbo
cambió respecto a §1 y §8 — ver §10, que es lo vigente. Lo de arriba queda como registro
del diseño original y su razonamiento.

---

## 1. Objetivo

Que un agente de desarrollo pueda **consultar el catálogo real** sin curl a mano, y que el
contrato de la API deje de vivir solo en la cabeza de `app/tools/adapters.py`.

No objetivo (explícito): **el bot de WhatsApp NO consume este MCP**. Sigue llamando
`execute_tool` en proceso. El MCP es tooling de desarrollo. Ver §7.

## 2. El hallazgo que condiciona todo

`clienteApiApp` no tiene una capa reutilizable. Verificado:

```php
// handlers/productos.php
function buscarProductos(PDO $pdo): void   // ← void
{
    $q = $_GET['q'] ?? '';                 // ← lee superglobals
    ...
    jsonResponse(true, 'OK', $productos);  // ← echo + exit
}
```

`jsonResponse()` termina con `exit`. Y `imgUrl()` (helpers/response.php) construye URLs desde
`$_SERVER['HTTP_HOST']`, así que fuera de una petición HTTP devuelve rutas rotas.

Consecuencia: **un MCP no puede reutilizar los handlers**. Solo tiene dos salidas:

| Opción | Coste | Problema |
|---|---|---|
| MCP → HTTP loopback a `/api/...` | bajo | doble hop, y el contrato sigue sin tener dueño |
| Extraer *services* puros y que REST y MCP los compartan | medio | requiere tocar los handlers |

Este plan toma la segunda, porque la primera deja el problema real (deriva de contrato) sin
resolver — y añade un tercer sitio donde vive el schema.

## 3. Arquitectura

Ports & adapters, versión mínima. Una capa de dominio pura, dos adaptadores de entrega.

```
                                 ┌─────────────────────────────┐
  GET /api/productos/buscar ───► │ index.php  (adapter REST)   │──┐
                                 │ $_GET → params → jsonResp() │  │
                                 └─────────────────────────────┘  │
                                                                  ▼
                                                    ┌──────────────────────────┐
                                                    │  src/Service/*.php       │
                                                    │  puros: (PDO, array):array│
                                                    │  sin echo/exit/$_GET      │
                                                    └──────────────────────────┘
                                 ┌─────────────────────────────┐  ▲
  POST /mcp  (JSON-RPC 2.0) ───► │ mcp.php   (adapter MCP)     │──┘
                                 │ tools/call → envelope MCP   │
                                 └─────────────────────────────┘
```

**Regla dura:** un service nunca hace `echo`, `exit`, ni lee `$_GET`/`$_SERVER`. Recibe un
array de parámetros ya validado y devuelve un array. Es lo que hace que dos adaptadores
distintos puedan compartirlo, y lo que lo vuelve testeable sin levantar Apache.

Layout propuesto en `donregalo/clienteApiApp/`:

```
src/
  Service/
    CatalogoService.php     navegacion()
    ProductoService.php     buscar(), detalle(), activos()
    DistritoService.php     listar(), disponibilidad()
  Support/
    ImageUrl.php            base URL inyectada, no $_SERVER
    Money.php               USD→PEN en un solo sitio
mcp/
  index.php                 endpoint JSON-RPC
  Server.php                initialize / tools / resources
  Tools/                    una clase por tool
  schemas/                  JSON Schema de entrada y salida
handlers/                   quedan como adapters finos
```

`Support/ImageUrl.php` y `Support/Money.php` son la razón de peso del refactor: hoy la
conversión a soles vive en `adapters.py` de este repo, y la URL de imagen se arma con
`$_SERVER`. Ambas son decisiones de contrato, no de presentación.

## 4. Transporte y despliegue

**Streamable HTTP, modo stateless.** Un solo endpoint `POST /mcpApp/` que recibe JSON-RPC 2.0
y responde `application/json`.

Por qué no stdio: el hosting de Don Regalo es PHP compartido, sin procesos largos. Un servidor
stdio tendría que correr en la máquina del desarrollador, y entonces el código no estaría "en
el proyecto principal".

Por qué stateless encaja: PHP-FPM es request/response, exactamente la forma de Streamable HTTP
sin SSE. No hay que mantener sesión ni stream abierto. Si un día hace falta progreso o
notificaciones, se añade `text/event-stream`; hoy no hace falta.

Detalles del transporte:
- El cliente manda `Accept: application/json, text/event-stream`. Responder JSON está permitido.
- Negociar versión en `initialize`; devolver la que soporte el cliente, no una fija a ciegas.
- `Mcp-Session-Id`: omitir. Sin estado, no aporta.
- Métodos mínimos: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`,
  `resources/list`, `resources/read`.

**Autenticación.** Hoy `clienteApiApp` no tiene ninguna (API.md §Autenticación). Un endpoint MCP
público sin auth es peor que la API REST sin auth, porque invita a un agente a iterarlo. Mínimo
para fase 1: bearer token en `Authorization`, comparado con `hash_equals()` contra un secreto en
`config/secrets.php`. Rate limit por IP. Nada de OAuth todavía — es para tres desarrolladores.

## 5. Diseño del MCP

### 5.1 Tools vs Resources

No todo es tool. La distinción importa porque cambia quién decide cuándo se lee.

| Qué | Tipo | Por qué |
|---|---|---|
| Taxonomía (`/catalogo/navegacion`) | **Resource** `donregalo://catalogo/navegacion` | es un documento que se lee, no una acción; el host puede cachearlo |
| `API.md` y `CATALOGO.md` | **Resource** | que el agente lea el contrato desde la fuente, no desde una copia |
| Buscar productos | Tool | tiene parámetros y decide el modelo |
| Detalle de producto | Tool | |
| Cobertura de distrito | Tool | |

### 5.2 Tools de la v1 (solo lectura)

| Tool | Service | Annotations |
|---|---|---|
| `donregalo_buscar_productos` | `ProductoService::buscar` | `readOnlyHint: true`, `openWorldHint: false` |
| `donregalo_detalle_producto` | `ProductoService::detalle` | idem |
| `donregalo_cobertura_distrito` | `DistritoService::disponibilidad` | idem |
| `donregalo_validar_activos` | `ProductoService::activos` | idem |

**Fuera de la v1, y probablemente para siempre:** `POST /pedidos/temporales` y `POST /consultas`.
Crean filas reales en el panel de ventas. Un agente de desarrollo iterando parámetros llenaría
el panel de pedidos fantasma.

### 5.3 Presupuesto de tokens: la decisión de diseño que más pesa

El error clásico de un MCP es devolver el JSON de la API tal cual. `GET /productos/buscar` con
`per_page=12` trae descripciones largas, galería, relacionados: unos pocos miles de tokens para
una pregunta que se contesta con doce nombres y doce precios.

**Cada tool proyecta a una forma delgada.** Ejemplo para `buscar_productos`:

```json
{
  "total": 34,
  "mostrando": 8,
  "productos": [
    {"id": 1235, "nombre": "...", "precio_pen": 189.00, "categoria_slug": "desayunos-criollos"}
  ],
  "hint": "Usa donregalo_detalle_producto para descripción, stock e imágenes."
}
```

Y `outputSchema` declarado, para que el host reciba `structuredContent` además del texto.

### 5.4 Las descripciones son el prompt

Es donde se codifica lo que en este repo son guardarraíles. La descripción de
`donregalo_buscar_productos` debe decir, literalmente:

> Los slugs de `categoria` salen del recurso `donregalo://catalogo/navegacion`. No inventes
> slugs ni nombres de categoría. Si el resultado es vacío, la categoría existe pero no tiene
> stock — no la sustituyas por otra.

### 5.5 Errores

Dos canales distintos, y confundirlos es un bug:
- Error de protocolo (JSON mal formado, método inexistente) → error JSON-RPC.
- Error de ejecución que el modelo debe leer y corregir (slug inexistente, id inválido) →
  resultado con `isError: true` y un texto accionable: `"Categoría 'desayunos-premium' no
  existe. Slugs válidos en donregalo://catalogo/navegacion."`

## 6. Fases

### Fase 0 — Fundación (sin MCP todavía)
- Extraer `ProductoService::buscar/detalle/activos`, `DistritoService`, `CatalogoService`.
- `handlers/*.php` pasan a ser adaptadores: parsear `$_GET` → llamar service → `jsonResponse()`.
- `Support/ImageUrl.php` con base URL inyectada; `Support/Money.php` con USD→PEN.
- **Hecho cuando:** la respuesta de cada endpoint REST es byte-idéntica a la de hoy. Guardar
  las respuestas actuales como golden files antes de tocar nada.

### Fase 1 — MCP mínimo
- `mcp/index.php` con `initialize`, `tools/list`, `tools/call`, `resources/*`.
- Las 4 tools de §5.2 y el resource de taxonomía.
- Bearer token + rate limit.
- **Hecho cuando:** MCP Inspector lista las tools y `buscar_productos` con
  `categoria: "desayunos"` devuelve productos reales.

### Fase 2 — Contrato
- OpenAPI generado desde los services (o escrito a mano y validado contra ellos).
- Los JSON Schema de las tools salen de ahí, no se escriben dos veces.
- **Hecho cuando:** cambiar un campo en un service rompe un test si el schema no se actualizó.

### Fase 3 — Consumo desde este repo
- Registrar el server en `.mcp.json` de `agente-don-regalo`.
- Test de contrato en `tests/`: compara lo que `adapters.py` espera contra el output schema
  publicado. Es lo que habría cazado `categoria_url` → `url_categoria` el día que cambió.

## 7. Qué gana `agente-don-regalo`

Concreto, no hipotético:

1. **El mandato del CLAUDE.md** ("verifica contra la API real cuando toques catálogo/cobertura/
   precios") pasa de curl manual a una llamada de tool.
2. **Depurar incidentes.** "¿Por qué el bot dijo que no hay terrarios?" se responde llamando la
   misma consulta que hizo el bot, sin levantar el harness.
3. **Escribir casos de eval** con datos reales del catálogo en vez de mocks inventados — que es
   textualmente el fallo que documenta el CLAUDE.md ("el test lo tapaba con un mock inventado").
4. **Detección de deriva** (fase 3).

## 8. Riesgos

| Riesgo | Mitigación |
|---|---|
| El refactor de fase 0 cambia una respuesta REST y rompe al agente en producción | golden files antes de tocar; fase 0 se despliega y se observa antes de fase 1 |
| El MCP se convierte en un tercer sitio donde vive el schema | fase 2 antes de añadir más tools; si fase 2 se salta, el riesgo se materializa |
| Alguien enchufa el bot de WhatsApp al MCP | no exponer tools de escritura; el harness no gana nada y pierde latencia |
| Endpoint público sin auth | bearer token es requisito de fase 1, no de fase 2 |

## 9. Alternativa si hay prisa

Un server stdio en TypeScript con el SDK oficial, corriendo local, que llama a la API REST
remota: se monta en una tarde y da los puntos 1–3 de §7. No toca el proyecto PHP y no resuelve
la deriva de contrato. Es un buen puente si fase 0 no cabe ahora.

---

## 10. Estado real y cambio de rumbo (jul 2026)

Lo de §1–§9 es el diseño original. Esto es lo que HAY, y en qué se apartó del plan.

### 10.1 Lo que ya estaba construido

Las Fases 0 y 1 se hicieron: `donregalo/clienteApiApp/` tiene `src/Service/` (`Catalogo`,
`Producto`, `Distrito`), `src/Support/` (`ImageUrl`, `Money`) y `src/Mcp/` (`Server`, `Tools`,
`Auth`, `RateLimit`, `Resources`). El endpoint vive en `https://www.donregalo.pe/clienteApiApp/mcp/`
(Streamable HTTP, stateless, Bearer). `serverInfo.name = donregalo-catalogo`, protocolo
`2025-06-18`. Publicaba 5 tools: `navegacion_catalogo`, `buscar_productos`, `detalle_producto`,
`cobertura_distrito`, `validar_activos`.

### 10.2 El cambio de rumbo: el bot SÍ consume el MCP

§1 decía "el bot de WhatsApp NO consume este MCP" y §8 lo listaba como riesgo. **Eso se
revirtió a propósito**, con los ojos abiertos:

- El motivo de peso en contra era la latencia sin ganancia. Pero con el fix de imágenes (§10.3)
  el MCP pasó a devolver algo que el REST de detalle NO daba (una `imagen_url` viva), así que sí
  hay ganancia observable.
- El riesgo se acota con **opt-in + degradado a HTTP**: el bot solo usa MCP si
  `DONREGALO_USE_MCP=1`, y ante cualquier fallo cae al camino HTTP de siempre. Un MCP caído no
  tumba un turno.

### 10.3 El bloqueador y su causa raíz (imágenes)

Verificado contra producción: `buscar` no traía ningún campo de imagen y `detalle` devolvía
`imagen_url: null`. Sin URL, el emisor de WhatsApp **no puede** mandar el producto como foto —
la regla #1 del `CLAUDE.md` del agente. Causa: `Tools::detalle` tomaba `imagenes[0].medium`
(columna `middle_producto_imagen`, que da 404), y `buscar` no proyectaba el `imagen_url` que el
service **ya calculaba**. La única URL viva es `medium/<imagen_producto_imagen>` (la del
listado). Comprobado: esa URL da `200 image/webp`; la del detalle daba `200 text/html` (error).

**Fix (server, `donregalo`):**

| Archivo | Cambio |
|---|---|
| `src/Service/ProductoService.php` | `detalle()` expone `imagen_url` = URL viva del listado. **De paso arregla el REST `GET /productos/{id}`**, deuda del servidor que el `CLAUDE.md` tenía abierta. |
| `src/Mcp/Tools.php` | `buscar` proyecta `imagen_url`; `detalle` usa la URL viva; `outputSchema` y descripción al día. |

### 10.4 Tools añadidas (server, `donregalo`)

De 5 a 9 tools. `productos_por_ocasion` NO se añadió: `buscar` ya acepta `ocasion`.

| Tool | Service | Nota |
|---|---|---|
| `donregalo_productos_destacados` | `ProductoService::destacados` (existía) | trae `imagen_url` |
| `donregalo_productos_ofertas` | `ProductoService::ofertas` (existía) | añade `precio_antes_pen`, `descuento_pct` |
| `donregalo_metodos_pago` | **`PagoService`** (nuevo) | `descripcion` multilínea que conserva cuenta y CCI |
| `donregalo_rastrear_pedido` | **`PedidoService`** (nuevo) | read-only protegido: exige email + código |

Los handlers REST `metodos_pago.php` y `pedidos.php` se refactorizaron para llamar a esos
services: contrato único, salida REST byte-idéntica.

### 10.5 Cliente MCP en el agente (`agente-don-regalo`) — Fase 3

Reemplaza la §3 original ("consumir vía `.mcp.json` para desarrollo"): aquí el que consume es el
**runtime del bot**.

| Archivo | Cambio |
|---|---|
| `app/config.py` | `donregalo_mcp_url`, `donregalo_mcp_token` (**desde env, nunca hardcodeado**), `donregalo_use_mcp` (default `False`; exige token) |
| `app/tools/mcp_client.py` | **nuevo**. Cliente Streamable HTTP sobre httpx: lifecycle MCP, JSON/SSE, mappers MCP→forma canónica, validación de imágenes y 7 funciones compatibles con `catalog.*` que degradan a HTTP |
| `app/tools/executor.py` | selector `_pick(name, default)`: MCP si el flag está activo y la tool soportada; si no, `catalog` |
| `tests/test_mcp_client.py` | **nuevo**. 9 tests offline (mappers, isError→vacío, degradado, sobre REST del rastreo) |

Decisiones:

- **Salida canónica idéntica.** El MCP devuelve productos casi canónicos; solo se remapea
  `id→id_producto` y `en_oferta→tiene_oferta`, y `adapters.products_payload` hace el resto. El
  precio a soles lo calcula el agente con SU tipo de cambio, no el `precio_pen` del MCP: una sola
  fuente de verdad. El resto del harness no nota la diferencia.
- **Híbrido a propósito.** `distritos_cobertura` (lista completa), `explorar_catalogo` (taxonomía)
  y `tipo_cambio` siguen en HTTP: el MCP no tiene "lista completa" y enrutarlos rompería el
  matcher determinista de cobertura y el menú (`taxonomy.render_menu`).
- **Validación defensiva de fotos también en MCP.** Construir una URL no prueba que sea una
  imagen: el consumidor aplica `valid_products` igual que REST; en detalle conserva la ficha y
  limpia solo la foto si está rota.

### 10.6 Verificación

- Server: **108 pruebas PHP aprobadas**. Incluyen golden REST, 9 tools contra MySQL,
  `structuredContent↔outputSchema`, lifecycle, auth, rate limit y Origin.
- Agente: pruebas de transporte JSON/SSE, lifecycle, imágenes, ofertas y contrato cruzado
  schema↔consumidor. El espejo `sandbox/` se verifica con `check_mirror.py`.

### 10.7 Pendiente

Los siguientes pasos son responsabilidad del propietario de la infraestructura; no requieren
compartir acceso a cPanel, EasyPanel ni el token:

1. ~~**Desplegar `donregalo`** al hosting (solo PHP, sin SQL) usando el paquete preparado.~~
   Verificado el 24 de julio de 2026: el endpoint exige Bearer, acepta el Origin legítimo y
   rechaza un Origin ajeno.
2. ~~**Smoke test autenticado** del lifecycle, las 9 definiciones, catálogo, detalle,
   cobertura, activos, destacados, ofertas, pagos y una imagen real.~~ Aprobado el 24 de
   julio de 2026. Queda como control posterior probar un rastreo positivo con un pedido
   real de prueba.
3. ~~**Activar en el agente** únicamente después: `DONREGALO_MCP_TOKEN=…` +
   `DONREGALO_USE_MCP=1` en EasyPanel.~~ Activado por el propietario después del smoke.
   Rotar el token si circuló por un canal inseguro.

Activar antes del despliegue sí sería incorrecto: las tools antiguas pueden responder con éxito
pero sin imagen, caso que no dispara fallback.

El procedimiento, hash del paquete, resultado esperado y rollback están en
`docs/MCP_DEPLOY_CHECKLIST.md`.

### 10.8 Contrato y deriva

Los `outputSchema` describen todas las propiedades consumidas, cierran
`additionalProperties` y el servidor prueba cada respuesta real contra su schema. El agente
mantiene `tests/fixtures/mcp/consumer_contract.json`; `tests/test_mcp_contract.py` carga las
definiciones PHP del repo hermano y falla si desaparece una tool o un campo requerido.

Ofertas conserva ambos precios (`precio_usd`, `precio_antes_usd`) y `descuento_pct`; REST y MCP
producen la misma forma canónica.

### 10.9 Checklist previo a producción

- [x] Schemas completos y mappers fieles, incluidas ofertas.
- [x] Pruebas PHP para las 9 tools y golden REST actualizado.
- [x] Contrato cruzado PHP→Python.
- [x] Validación defensiva de imágenes en MCP.
- [x] Lifecycle `initialize`→`notifications/initialized`, header de versión y soporte JSON/SSE.
- [x] Validación de `Origin`.
- [x] Documentación sincronizada en ambos repos.
- [x] Despliegue PHP al hosting — verificado con controles Bearer y Origin.
- [x] Smoke principal en producción — lifecycle, 9 definiciones, tools públicas e imagen.
- [ ] Rastreo positivo con un pedido real de prueba.
- [x] Activación opt-in en EasyPanel — confirmada por el propietario.
