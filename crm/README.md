  # CRM PHP — Don Regalo (panel + API agente)

  Inbox WhatsApp para asesores en el **servidor PHP del cliente**.
  MySQL local (`crm_*` + `usuarios`/`roles`). Sin Remote MySQL.

  ## Documentación completa

  Ver **[`../docs/SANDBOX_Y_CRM_PHP.md`](../docs/SANDBOX_Y_CRM_PHP.md)** — estado del agente sandbox + este CRM, flujos, API, fixes y checklist.

  El worker RAG usa `POST /api/embedding-jobs/claim` y
  `PATCH /api/embedding-jobs/{id}`, protegidos por el mismo token interno. Antes de
  activarlo aplica `../database/mysql/001_producto_embeddings.sql` sobre la base
  del catálogo.

  ## Estructura

  ```
  crm/
    public/           ← document root (o /crm/public)
      index.php       inbox
      login.php
      reports.php
      operaciones.php  panel de cola, errores, circuitos, latencias y handoffs
      api/index.php   API del agente (X-CRM-Token)
    sql/              migraciones MySQL
    src/              PDO, Auth, Repository
    views/
    config.example.php
  ```

  ## Setup rápido

  1. Copia `config.example.php` → `config.php` y completa `db`, tokens.
  2. Asegura el schema `crm/sql/001_crm_schema.sql` (y migraciones posteriores) en la BD.
  3. Publicación actual (carpeta): `https://donregalo.pe/crm/public/` con `base_path => '/crm/public'`.
  4. Login con `login_usuario` de la tabla `usuarios`.

  Guía de deploy: [`docs/DEPLOY.md`](docs/DEPLOY.md)

  ## Agente (VPS EasyPanel)

  ```env
  CRM_MODE=external
  CRM_BASE_URL=https://donregalo.pe/crm/public
  CRM_INTERNAL_TOKEN=mismo-que-config.php
  AGENT_INTERNAL_TOKEN=mismo-que-config.php
  WATCHDOG_ENABLED=0
  ```

  Health: `GET https://donregalo.pe/crm/public/api/health`

  Panel operacional autenticado:
  `GET https://donregalo.pe/crm/public/operaciones.php`.

  ## Módulos del asesor

  Cinco cosas que el panel no sabía hacer y que salían caras:

  | Módulo | Qué resuelve | Dónde se ve |
  |---|---|---|
  | **Venta manual** | El historial solo recogía cierres del bot, y el chat que llega a un humano es justo el que el bot NO pudo cerrar: la mayoría de las ventas no existían en ningún registro. | Botón «Registrar venta» en el chat; columna «Cerró» en Historial |
  | **Asignación** | «Tomar conversación» pasaba el chat a HUMAN sin decir a qué humano, y dos asesores podían escribirle cosas distintas al mismo cliente. | Chip en la cabecera, badge en la lista, filtro Todas/Mías/Sin asignar |
  | **Ventana de 24 h** | Fuera de la ventana de servicio de WhatsApp, Meta rechaza el texto libre: el mensaje moría en `failed` y el panel solo decía «No se envió». | Chip con el tiempo restante y composer bloqueado con el motivo |
  | **Notas internas** | Todo lo que se escribía salía a WhatsApp: no había forma de dejar contexto para el turno siguiente. | Panel del lead |
  | **Seguimientos** | El lead que dijo «lo consulto y te aviso» se hundía en la bandeja y no volvía nadie. | Rail superior de vencidos + panel del lead |

  Migraciones: `009` … `012`. **Van antes que el PHP** (ver [`docs/DEPLOY.md`](docs/DEPLOY.md)).

  Detalles que no son obvios y conviene no deshacer:

  - **Responder reclama el chat.** Si además hiciera falta pulsar un botón, la
    asignación se quedaría vacía y el módulo no serviría de nada.
  - **Tomar el chat lo saca de la cola de atención** (`human_support = 0`): una
    franja que sigue avisando por un chat que un compañero ya tiene abierto se
    ignora, y entonces deja de avisar de los de verdad.
  - **El origen de la venta viaja dentro del snapshot**, no como argumento:
    `markSaleDelivered` vuelve a archivar la ficha al confirmar la entrega y le
    borraría la autoría al asesor.
  - **El claim no se decide con `rowCount()`**: MySQL cuenta filas cambiadas, no
    coincidentes, así que el dueño re-reclamando la suya recibía 0.
  - **Sin mensajes entrantes NO se bloquea el envío**: sin evidencia de que la
    ventana esté cerrada, no se le quita al equipo la posibilidad de escribir.

  Contrato: `crm/tests/asesor_modulos_contract.php` (lo corre `scripts/check_crm.py`).

  ## Inteligencia comercial

  Dos módulos más, **escritos y sin desplegar**: **Campañas** (qué anuncio trae
  compradores) y **Oportunidades** (qué nos piden que no tenemos). Migraciones
  `013` y `014`, y como siempre van antes que el PHP.

  Ojo con Oportunidades: la señal **no es recuperable hacia atrás** — empieza a
  contar el día que se despliega el agente, igual que pasó con el anuncio del
  lead en la `007`.

  La parte de comparar contra la competencia está **en pausa** a la espera de la
  lista de dominios.

  Ver [`docs/INTELIGENCIA_COMERCIAL.md`](docs/INTELIGENCIA_COMERCIAL.md).

  ## El nombre del adjunto (migración `015`)

  `crm_outbox` no guardaba el nombre del archivo: viajaba solo en el payload del
  push CRM→agente. Mientras el push funciona no se nota, pero cuando falla la
  fila se queda en `pending` para que la recoja el drenaje del agente — y ahí el
  nombre ya no existe. El PDF le llegaba al cliente como **"documento"**, sin
  extensión, o sea que el camino de rescate entregaba un archivo que muchos
  clientes de WhatsApp no abren, con el asesor creyendo que había salido bien.

  Contrato: `crm/tests/outbox_filename_contract.php`. La mitad del agente está
  en `tests/test_outbox_nombre_de_archivo.py`.

  ## El límite de subida de los adjuntos

  El guardia del panel eran 16 MB fijos (copiados de `Media::MAX_BYTES`)
  mientras PHP corta antes, en `upload_max_filesize` / `post_max_size`, que en
  hosting compartido suelen venir en **2M y 8M**. Un catálogo en PDF de 8 MB
  pasaba el filtro del navegador, se subía entero, el servidor lo tiraba y el
  asesor veía "No se envió" tras esperar la subida. Reintentar el mismo archivo
  no podía funcionar nunca. En el log del agente no aparecía nada, con razón: el
  envío moría en el CRM, antes de que hubiera fila en `crm_outbox`.

  Dos mitades, y hacen falta las dos:

  - **Subir el techo**: `public/.user.ini` (PHP-FPM/CGI) y los `php_value` del
    `.htaccess` (mod_php) lo llevan a 16M/20M. Ningún hosting lee los dos, por
    eso están ambos. **Los `php_value` van dentro de `<IfModule>` siempre**: sin
    mod_php, un `php_value` suelto devuelve un 500 y tumba el panel entero.
  - **Decir la verdad cuando el hosting no deje subirlo**:
    `Media::effectiveMaxBytes()` cruza nuestro tope con los de PHP y la vista lo
    publica en `data-max-upload`, así que el panel rechaza el archivo **antes**
    de subirlo y con la cifra real.

  Contrato: `crm/tests/limite_de_subida_contract.php`.

  Si tras desplegar sigue fallando, mira el valor real: `Media::effectiveMaxBytes()`
  es lo que el panel promete ahora, y el mensaje de error trae la cifra.

  ## El panel suena con cada mensaje del cliente

  El aviso sonoro solo saltaba en dos transiciones: handoff y lead nuevo. O sea
  que mientras el agente atendía —que es la mayoría del tiempo— el panel estaba
  mudo, y el equipo que sigue las conversaciones aunque no las lleve se enteraba
  tarde o no se enteraba. Ahora suena **cada mensaje entrante**, tenga el chat la
  IA o un asesor. Sonar no es tomar el chat: el aviso no toca el modo.

  - **La señal es `window.last_inbound_at`**, que sale de los mensajes `inbound`
    reales. `last_message_at` no vale: también lo mueven el bot y el asesor, y
    eso no es "llegó un mensaje".
  - **Marca de agua por conversación**, no "cambió la lista": la bandeja se
    refresca cada 4 s y el mismo mensaje pitaría en cada tick. Solo suena cuando
    la hora avanza.
  - **Un pitido por refresco.** El primer mensaje de un lead es a la vez
    "entrante", "lead nuevo" y a veces "handoff"; tres pitidos pegados se oyen
    como una avería. Gana el más urgente.
  - **Dos tonos distintos.** El handoff sube (880→1320); un mensaje normal es un
    toque corto y más grave. Si todo sonara igual, el urgente dejaría de
    distinguirse y el equipo silenciaría la pestaña.
  - **La primera carga siembra, no avisa** — abrir el panel por la mañana no
    suelta una ráfaga de pitidos por los chats de ayer.

  Contrato: `crm/tests/aviso_mensaje_entrante_contract.php`. Es **solo CRM**: no
  hay cambios en el agente ni migraciones, basta con subir `public/assets/inbox.js`.
