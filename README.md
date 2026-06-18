# Agente Don Regalo

Agente de WhatsApp basado en FastAPI que recibe webhooks de Chatwoot y Evolution API,
procesa mensajes de texto, audio, imagenes y PDF con OpenAI, consulta el catalogo real
de Don Regalo, usa busqueda semantica con Qdrant cuando corresponde y responde en la
conversacion.

## Requisitos

- Python 3.11+
- Instancia de Chatwoot con webhook configurado
- Evolution API conectada al numero de WhatsApp
- API key de OpenAI
- Qdrant para busqueda semantica de productos y conocimiento del equipo

## Instalacion local

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edita `.env` con las credenciales reales.

## Uso

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

En Docker/EasyPanel el contenedor expone el puerto `80` y usa:

```bash
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-80}
```

## Endpoints

| Endpoint | Uso |
|---|---|
| `GET /health` | Healthcheck simple del servicio |
| `POST /webhook` | Webhook principal de Chatwoot |
| `POST /debug-webhook` | Diagnostico para inspeccionar payloads de Chatwoot |
| `POST /evolution-webhook` | Webhook nativo de Evolution para capturar mensajes citados de WhatsApp |

En Chatwoot configura `POST https://tu-dominio/webhook` para eventos como
`message_created` y cambios de estado de conversacion. En Evolution configura
`POST https://tu-dominio/evolution-webhook`.

## Flujo de mensajes

1. El cliente escribe por WhatsApp.
2. Evolution/Chatwoot entregan el evento a `/webhook`.
3. `app.services.buffer` agrupa mensajes cercanos usando `BUFFER_SECONDS`.
4. El agente arma contexto con memoria corta, memoria larga y mensaje citado si existe.
5. OpenAI decide que herramienta usar.
6. El agente consulta APIs del negocio, Qdrant o conocimiento del equipo.
7. La respuesta se divide en texto e imagenes.
8. Las imagenes de producto se descargan, se normalizan a JPEG si es necesario y se envian por Evolution como media base64. Si falla, se intenta Chatwoot y luego texto con enlace como ultimo respaldo.

## Activacion del bot

El bot solo responde en conversaciones que tengan la etiqueta configurada en:

```env
BOT_ACTIVE_LABEL=agente_on
```

Si una conversacion no tiene esa etiqueta, el webhook responde `ignored` y no procesa el mensaje.

## Reglas de busqueda

La API del negocio es la fuente de verdad para datos exactos. Qdrant es un motor de
descubrimiento, no la base final de verdad.

Usa busqueda semantica cuando el cliente expresa intencion, estilo o necesidad:

- "algo bonito para mi mama"
- "un detalle elegante para mi jefe"
- "rosas blancas para nacimiento"
- "algo parecido a este producto"

Usa APIs directas cuando el cliente pide datos exactos:

- categorias del sitio
- productos de una categoria
- precio, stock o detalle de un producto
- delivery por distrito
- metodos de pago
- tipo de cambio
- rastreo de pedido

## Campanas temporales

Las campanas como Dia del Padre, Dia de la Madre, Navidad, San Valentin o Fiestas
Patrias son categorias curadas del sitio, no busquedas semanticas libres.

Para esos casos el flujo correcto es:

1. `listar_categorias`
2. detectar la categoria temporal activa, por ejemplo `dia-del-padre`
3. `catalogo_categoria` con ese slug

La busqueda semantica queda bloqueada para campanas si no incluye `categoria_slug`.
Si se usa Qdrant dentro de una campana, debe ir filtrado con el slug temporal.

Ejemplo confirmado en base de datos:

- `categorias.id_categoria = 24`
- `categorias.url_categoria = dia-del-padre`
- `categorias.temporal_categoria = 1`
- productos nuevos de campana como `Desayuno con cariño para papa`, `Gustito para Papa`,
  `Pack detalle para papa` y `Monsieur Prestige` pertenecen a esa categoria.

## Envio de imagenes

Los listados de productos se generan con una URL de imagen por producto. El servicio:

1. detecta la URL con `split_reply`
2. descarga la imagen
3. convierte WebP u otros formatos poco confiables a JPEG usando Pillow
4. envia la imagen por Evolution API como media base64
5. si Evolution falla, intenta adjunto en Chatwoot
6. si ambos fallan, envia texto con el enlace de la foto

Esto evita depender de que Chatwoot reenvie correctamente adjuntos hacia WhatsApp.

## Estructura del proyecto

```text
app/
├── main.py              # Crea la app FastAPI y registra routers
├── config.py            # Configuracion centralizada
├── api/
│   ├── webhook.py       # POST /webhook y /debug-webhook
│   └── evolution.py     # POST /evolution-webhook
├── services/
│   ├── buffer.py        # Debounce y orquestacion del flush
│   ├── agent.py         # Loop LLM con function calling
│   ├── content.py       # Audio, imagen y PDF entrante
│   ├── memory.py        # Memoria corta y larga
│   ├── messenger.py     # Envio de texto, media y typing
│   └── knowledge.py     # Captura de conocimiento del equipo
├── tools/
│   ├── definitions.py   # Esquemas OpenAI
│   ├── catalog.py       # APIs HTTP del catalogo
│   ├── search.py        # Busqueda semantica Qdrant
│   └── executor.py      # Dispatcher de herramientas
└── prompts/
    └── system.py        # System prompt del agente

scripts de mantenimiento:
  sync_qdrant.py         # Sincroniza catalogo a Qdrant
  sync_conocimiento.py   # Backfill de conocimiento del equipo
```

## Variables de entorno

| Variable | Descripcion |
|---|---|
| `CHATWOOT_URL` | URL base de Chatwoot |
| `CHATWOOT_API_TOKEN` | Token de API de Chatwoot |
| `CHATWOOT_ACCOUNT_ID` | ID de la cuenta de Chatwoot |
| `OPENAI_API_KEY` | API key de OpenAI |
| `OPENAI_MODEL` | Modelo usado por el agente |
| `BOT_ACTIVE_LABEL` | Etiqueta requerida para activar el bot |
| `EVOLUTION_API_URL` | URL base de Evolution API |
| `EVOLUTION_API_KEY` | API key de Evolution |
| `EVOLUTION_INSTANCE` | Nombre de la instancia Evolution |
| `BUFFER_SECONDS` | Segundos de silencio antes de procesar mensajes agrupados |
| `TYPING_SECONDS_PER_CHAR` | Delay de typing por caracter |
| `TYPING_MIN_DELAY` | Delay minimo de typing |
| `TYPING_MAX_DELAY` | Delay maximo de typing |
| `MEMORY_WINDOW_HOURS` | Ventana de memoria corta |
| `MEMORY_MAX_MESSAGES` | Maximo de mensajes de memoria corta |
| `MAX_TOOL_ROUNDS` | Maximo de rondas de herramientas por respuesta |
| `QDRANT_URL` | URL de Qdrant |
| `QDRANT_API_KEY` | API key de Qdrant |
| `QDRANT_COLLECTION` | Coleccion de productos |
| `EMBED_MODEL` | Modelo de embeddings |
| `EMBED_DIM` | Dimension de embeddings |
| `SEMANTIC_LIMIT` | Cantidad maxima de resultados semanticos |
| `DONREGALO_API_BASE` | API base del negocio |
| `CACHE_TTL_SECONDS` | TTL del cache para endpoints poco variables |
| `KB_COLLECTION` | Coleccion de conocimiento del equipo |
| `KB_LIMIT` | Cantidad maxima de respuestas de conocimiento |
| `KB_MIN_SCORE` | Score minimo para usar conocimiento del equipo |
| `PDF_MAX_CHARS` | Maximo de caracteres extraidos de PDF |

## Sincronizacion de catalogo

Para cargar o refrescar Qdrant:

```bash
python sync_qdrant.py
```

El job descarga `/productos/export`, genera embeddings y guarda payloads con campos como
`id_producto`, `nombre`, `precio`, `categoria`, `categoria_slug`, `ocasiones_ids`,
`es_funebre`, `stock`, `descripcion_corta`, `imagen_url` y `url`.

Despues de cambios importantes en categorias, campañas o productos, vuelve a sincronizar.

## Conocimiento del equipo

Cuando una conversacion se resuelve en Chatwoot, el webhook puede extraer conocimiento de
respuestas humanas y guardarlo en Qdrant (`KB_COLLECTION`).

Para hacer backfill:

```bash
python sync_conocimiento.py
```

## Troubleshooting

- Si el bot no responde, verifica que la conversacion tenga `BOT_ACTIVE_LABEL`.
- Si llegan textos pero no imagenes, revisa los logs `[IMG]`. Evolution debe recibir `sendMedia` y responder correctamente.
- Si Chatwoot muestra adjuntos pero WhatsApp no, no dependas de adjuntos de Chatwoot: usa Evolution directo.
- Si aparecen productos de otra campana u ocasion, revisa que el flujo haya usado `catalogo_categoria` y no `buscar_semantico` libre.
- Si Qdrant devuelve productos antiguos, ejecuta `sync_qdrant.py`.
- Si una respuesta parece inventada, revisa que la herramienta correspondiente haya sido llamada en logs `[TOOL]`.

