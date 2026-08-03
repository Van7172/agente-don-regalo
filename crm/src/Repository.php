<?php

declare(strict_types=1);

final class Repository
{
    private static function tenantSlug(): string
    {
        return (string) (Auth::config()['tenant_slug'] ?? 'don-regalo');
    }

    public static function ensureTenantId(): int
    {
        $row = Database::fetchOne(
            'SELECT id_tenant FROM crm_tenants WHERE slug_tenant = :slug LIMIT 1',
            ['slug' => self::tenantSlug()]
        );
        if ($row) {
            return (int) $row['id_tenant'];
        }
        return Database::execute(
            'INSERT INTO crm_tenants (slug_tenant, nombre_tenant, config_tenant)
             VALUES (:slug, :nombre, :config)',
            [
                'slug' => self::tenantSlug(),
                'nombre' => 'Don Regalo',
                'config' => json_encode(['locale' => 'es-PE'], JSON_UNESCAPED_UNICODE),
            ]
        );
    }

    public static function getOrCreateContact(int $tenantId, string $waId, string $name = ''): int
    {
        $existing = Database::fetchOne(
            'SELECT id_contact, nombre_contact FROM crm_contacts
             WHERE id_tenant = :tenantId AND wa_id = :waId LIMIT 1',
            ['tenantId' => $tenantId, 'waId' => $waId]
        );
        if ($existing) {
            if ($name !== '' && ($existing['nombre_contact'] === '' || $existing['nombre_contact'] === null)) {
                Database::exec(
                    'UPDATE crm_contacts SET nombre_contact = :name WHERE id_contact = :id',
                    ['name' => $name, 'id' => $existing['id_contact']]
                );
            }
            return (int) $existing['id_contact'];
        }
        return Database::execute(
            'INSERT INTO crm_contacts (id_tenant, wa_id, nombre_contact)
             VALUES (:tenantId, :waId, :name)',
            ['tenantId' => $tenantId, 'waId' => $waId, 'name' => $name]
        );
    }

    public static function getOrCreateConversation(int $tenantId, int $contactId): int
    {
        $existing = Database::fetchOne(
            'SELECT id_conversation FROM crm_conversations
             WHERE id_tenant = :tenantId AND id_contact = :contactId AND status_conversation = \'open\'
             ORDER BY id_conversation DESC LIMIT 1',
            ['tenantId' => $tenantId, 'contactId' => $contactId]
        );
        if ($existing) {
            return (int) $existing['id_conversation'];
        }
        return Database::execute(
            'INSERT INTO crm_conversations (id_tenant, id_contact, status_conversation, mode_conversation, bot_active)
             VALUES (:tenantId, :contactId, \'open\', \'AI\', 1)',
            ['tenantId' => $tenantId, 'contactId' => $contactId]
        );
    }

    public static function ensureInboundConversation(string $waId, string $name = ''): array
    {
        $tenantId = self::ensureTenantId();
        $contactId = self::getOrCreateContact($tenantId, $waId, $name);
        $conversationId = self::getOrCreateConversation($tenantId, $contactId);
        return [
            'tenantId' => $tenantId,
            'contactId' => $contactId,
            'conversationId' => $conversationId,
        ];
    }

    /**
     * Minutos que una conversación cuenta como "lead nuevo".
     *
     * Es la banda de orden Y el badge del panel. Corta: pasado ese rato deja de
     * ser una novedad y vuelve a ordenarse por recencia como todas.
     */
    const LEAD_NUEVO_MIN = 30;

    public static function listConversations(int $limit = 80): array
    {
        $tenantId = self::ensureTenantId();
        $limit = max(1, min(200, $limit));
        // `sale`: el agente cerró la venta con todos los datos del pedido. El panel
        // pinta ese chat en verde para que el vendedor entre directo a cobrarlo.
        //
        // La bandeja sigue el modelo mental de WhatsApp: el chat cuyo ÚLTIMO
        // mensaje (cliente, bot o asesor) es más reciente aparece primero. La
        // cola de ayuda ya vive fijada en el rail superior; volver a priorizarla
        // aquí, junto con "nuevo" y "venta", dejaba conversaciones de las 09:00
        // por encima de una respuesta del vendedor de las 12:00.
        //
        // La fecha de crm_messages es la fuente de verdad. `last_message_at`
        // permanece como respaldo para datos históricos, pero no puede ocultar
        // una interacción que sí existe en el hilo.
        return Database::fetchAll(
            "SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
                    c.bot_active, c.human_support,
                    c.id_usuario_asignado, c.nombre_usuario_asignado,
                    COALESCE(
                      (SELECT MAX(activity.fecha_creacion)
                       FROM crm_messages activity
                       WHERE activity.id_conversation = c.id_conversation),
                      c.last_message_at,
                      c.fecha_creacion
                    ) AS last_message_at,
                    -- Solo los mensajes del CLIENTE reabren la ventana de 24h de
                    -- WhatsApp. Lo que escriba el bot o el asesor no cuenta.
                    (SELECT MAX(entrante.fecha_creacion)
                     FROM crm_messages entrante
                     WHERE entrante.id_conversation = c.id_conversation
                       AND entrante.direction_message = 'inbound') AS last_inbound_at,
                    (SELECT MIN(seg.fecha_programada)
                     FROM crm_seguimientos seg
                     WHERE seg.id_conversation = c.id_conversation
                       AND seg.estado_seguimiento = 'pendiente') AS next_followup_at,
                    c.fecha_creacion,
                    (c.fecha_creacion >= DATE_SUB(NOW(), INTERVAL :nuevoMin MINUTE))
                      AS es_nuevo,
                    ct.wa_id, ct.nombre_contact,
                    s.valor_setting AS sale,
                    (SELECT m.content_message FROM crm_messages m
                     WHERE m.id_conversation = c.id_conversation
                     ORDER BY m.id_message DESC LIMIT 1) AS last_message_preview
             FROM crm_conversations c
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             LEFT JOIN crm_settings s
                    ON s.id_tenant = c.id_tenant
                   AND s.llave_setting = CONCAT('sale_', c.id_conversation)
             WHERE c.id_tenant = :tenantId
             ORDER BY last_message_at DESC, c.id_conversation DESC
             LIMIT {$limit}",
            ['tenantId' => $tenantId, 'nuevoMin' => self::LEAD_NUEVO_MIN]
        );
    }

    public static function getConversation(int $id): ?array
    {
        return Database::fetchOne(
            'SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
                    c.bot_active, c.human_support, c.last_message_at,
                    c.ad_source_type, c.ad_source_id, c.ad_headline,
                    c.ad_body, c.ad_source_url,
                    c.id_usuario_asignado, c.nombre_usuario_asignado, c.fecha_asignacion,
                    (SELECT MAX(entrante.fecha_creacion)
                     FROM crm_messages entrante
                     WHERE entrante.id_conversation = c.id_conversation
                       AND entrante.direction_message = \'inbound\') AS last_inbound_at,
                    ct.wa_id, ct.nombre_contact,
                    s.valor_setting AS sale
             FROM crm_conversations c
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             LEFT JOIN crm_settings s
                    ON s.id_tenant = c.id_tenant
                   AND s.llave_setting = CONCAT(\'sale_\', c.id_conversation)
             WHERE c.id_conversation = :id LIMIT 1',
            ['id' => $id]
        );
    }

    public static function getMessages(int $conversationId, int $limit = 200): array
    {
        $limit = max(1, min(500, $limit));
        return Database::fetchAll(
            "SELECT id_message, id_conversation, direction_message, sender_type, role_message,
                    wa_message_id, content_message, media_url,
                    quoted_text, quoted_media_url, fecha_creacion
             FROM crm_messages
             WHERE id_conversation = :conversationId
             ORDER BY id_message ASC
             LIMIT {$limit}",
            ['conversationId' => $conversationId]
        );
    }

    /**
     * Texto del mensaje citado, buscado por su id de WhatsApp.
     *
     * Cuando el cliente responde a un mensaje, WhatsApp solo manda el id del
     * citado (context.id); el texto lo tiene el CRM, que es quien guarda los
     * mensajes. Sin resolverlo, un "quiero este" citando un producto llegaba sin
     * referencia y el bot volvía a preguntar cuál de todos.
     */
    /**
     * El mensaje citado: su texto Y su medio.
     *
     * Antes esto devolvía solo el texto, y el texto de una imagen sin caption es
     * el marcador `[image]`. El cliente respondía a una foto señalando "podría
     * optar por esta opción?" y el asesor veía `[image]`: justo en el momento en
     * que el lead elige, el vendedor se quedaba sin saber qué había elegido.
     *
     * @return array{text: ?string, media_url: ?string}|null
     */
    public static function findQuotedByWaId(string $waMessageId): ?array
    {
        if ($waMessageId === '') {
            return null;
        }
        $row = Database::fetchOne(
            'SELECT content_message, media_url FROM crm_messages
             WHERE wa_message_id = :waId
             ORDER BY id_message DESC LIMIT 1',
            ['waId' => $waMessageId]
        );
        if (!$row) {
            return null;
        }
        $text = $row['content_message'] !== null
            ? mb_substr((string) $row['content_message'], 0, 400)
            : null;
        $media = (string) ($row['media_url'] ?? '');
        return ['text' => $text, 'media_url' => $media !== '' ? $media : null];
    }

    public static function findMessageTextByWaId(string $waMessageId): ?string
    {
        return self::findQuotedByWaId($waMessageId)['text'] ?? null;
    }

    /** Ventana del filtro anti-duplicados del hilo, en segundos. */
    const DUPLICATE_WINDOW = 90;

    /**
     * ¿Este mensaje ya está en el hilo? Devuelve su id, o null.
     *
     * Última línea de defensa: aunque el claim del outbox impide el doble envío,
     * cualquier reintento (webhook redelivery de Meta, doble submit del panel,
     * push + drenaje) podía pintar el mismo texto dos veces en el inbox.
     *
     * El criterio NO es "mismo contenido" a secas: un cliente puede escribir "sí"
     * dos veces seguidas con toda la intención, y borrarle el segundo sería
     * inventarnos una conversación que no ocurrió. Hace falta que coincidan
     * conversación, dirección, emisor Y contenido dentro de una ventana corta.
     * Un `wa_message_id` repetido, en cambio, es una redelivery pura y dura: ahí
     * no hace falta ventana ninguna.
     */
    public static function findDuplicateMessage(array $input): ?int
    {
        $waId = (string) ($input['waMessageId'] ?? '');
        if ($waId !== '') {
            $row = Database::fetchOne(
                'SELECT id_message FROM crm_messages WHERE wa_message_id = :waId LIMIT 1',
                ['waId' => $waId]
            );
            if ($row) {
                return (int) $row['id_message'];
            }
        }

        $content = (string) ($input['content'] ?? '');
        // Los adjuntos comparten contenido ("[image]") sin ser el mismo archivo:
        // los deja pasar el claim del outbox, no este filtro.
        if (trim($content) === '' || !empty($input['mediaUrl'])) {
            return null;
        }

        $row = Database::fetchOne(
            'SELECT id_message FROM crm_messages
             WHERE id_conversation = :conversationId
               AND direction_message = :direction
               AND sender_type = :senderType
               AND content_message = :content
               AND fecha_creacion >= DATE_SUB(NOW(), INTERVAL :window SECOND)
             ORDER BY id_message DESC LIMIT 1',
            [
                'conversationId' => $input['conversationId'],
                'direction' => $input['direction'],
                'senderType' => $input['senderType'],
                'content' => $content,
                'window' => self::DUPLICATE_WINDOW,
            ]
        );
        return $row ? (int) $row['id_message'] : null;
    }

    /**
     * Guarda de qué anuncio vino el lead. Se fija UNA vez y no se pisa.
     *
     * Meta manda el `referral` solo con el primer mensaje de la conversación,
     * así que en la práctica esto se ejecuta una vez. La guarda de
     * `ad_source_id IS NULL` es por si el cliente vuelve a escribir desde otro
     * anuncio meses después: el lead es de quien lo trajo la primera vez, y
     * sobrescribirlo le cambiaría la atribución al asesor bajo los pies.
     */
    public static function setConversationAd(int $conversationId, array $referral): void
    {
        $sourceId = (string) ($referral['source_id'] ?? '');
        if ($sourceId === '') {
            return; // sin id no hay anuncio que atribuir
        }
        Database::exec(
            'UPDATE crm_conversations
                SET ad_source_type = :type,
                    ad_source_id   = :sourceId,
                    ad_headline    = :headline,
                    ad_body        = :body,
                    ad_source_url  = :url,
                    ad_ctwa_clid   = :clid,
                    ad_captured_at = NOW()
              WHERE id_conversation = :id
                AND ad_source_id IS NULL',
            [
                'id' => $conversationId,
                'type' => $referral['source_type'] ?? null,
                'sourceId' => $sourceId,
                'headline' => $referral['headline'] ?? null,
                'body' => $referral['body'] ?? null,
                'url' => $referral['source_url'] ?? null,
                'clid' => $referral['ctwa_clid'] ?? null,
            ]
        );
    }

    public static function addMessage(array $input): int
    {
        $duplicate = self::findDuplicateMessage($input);
        if ($duplicate !== null) {
            error_log(sprintf(
                '[CRM] mensaje duplicado descartado conv=%s sender=%s wa_message_id=%s',
                $input['conversationId'] ?? '?',
                $input['senderType'] ?? '?',
                $input['waMessageId'] ?? '-'
            ));
            return $duplicate;
        }

        $id = Database::execute(
            'INSERT INTO crm_messages
              (id_conversation, direction_message, sender_type, role_message, wa_message_id,
               content_message, media_url, quoted_text, quoted_media_url, raw_message)
             VALUES
              (:conversationId, :direction, :senderType, :role, :waMessageId,
               :content, :mediaUrl, :quotedText, :quotedMediaUrl, :raw)',
            [
                'conversationId' => $input['conversationId'],
                'direction' => $input['direction'],
                'senderType' => $input['senderType'],
                'role' => $input['role'],
                'waMessageId' => $input['waMessageId'] ?? null,
                'content' => $input['content'],
                'mediaUrl' => $input['mediaUrl'] ?? null,
                'quotedText' => $input['quotedText'] ?? null,
                // La foto que el cliente señaló al responder: sin esto la cita
                // de una imagen sale como el literal "[image]".
                'quotedMediaUrl' => $input['quotedMediaUrl'] ?? null,
                'raw' => isset($input['raw']) ? json_encode($input['raw'], JSON_UNESCAPED_UNICODE) : null,
            ]
        );
        Database::exec(
            'UPDATE crm_conversations SET last_message_at = NOW() WHERE id_conversation = :id',
            ['id' => $input['conversationId']]
        );
        return $id;
    }

    public static function setMode(int $conversationId, string $mode): void
    {
        $mode = $mode === 'HUMAN' ? 'HUMAN' : 'AI';
        Database::exec(
            'UPDATE crm_conversations
             SET mode_conversation = :mode,
                 human_support = :human,
                 bot_active = :bot
             WHERE id_conversation = :id',
            [
                'mode' => $mode,
                'human' => $mode === 'HUMAN' ? 1 : 0,
                'bot' => $mode === 'AI' ? 1 : 0,
                'id' => $conversationId,
            ]
        );
    }

    /** Marca necesidad de ayuda sin forzar modo HUMAN (bot pide soporte). */
    public static function setHumanSupport(int $conversationId, bool $on): void
    {
        Database::exec(
            'UPDATE crm_conversations SET human_support = :v WHERE id_conversation = :id',
            ['v' => $on ? 1 : 0, 'id' => $conversationId]
        );
    }

    public static function setBotActive(int $conversationId, bool $value): void
    {
        Database::exec(
            'UPDATE crm_conversations SET bot_active = :value WHERE id_conversation = :id',
            ['value' => $value ? 1 : 0, 'id' => $conversationId]
        );
    }

    // ── asignación de asesor ────────────────────────────────────────────────

    /**
     * Reclama la conversación para un asesor. `claimed: false` = la tiene otro.
     *
     * Mismo patrón que `claimOutbox`, y por el mismo motivo: un UPDATE
     * condicional es atómico bajo el lock de fila de InnoDB, así que de dos
     * asesores que pulsan "Tomar" a la vez exactamente uno ve rowCount() === 1.
     * Comprobar antes con un SELECT y actualizar después dejaría la ventana
     * abierta justo cuando importa — la cola se despacha en ráfagas y los dos
     * clics ocurren dentro del mismo segundo.
     *
     * `force` existe para el supervisor que necesita entrar a un chat que otro
     * dejó tomado (turno terminado, alguien se fue): sin salida de emergencia, un
     * chat se quedaría bloqueado hasta que el releaser lo devuelva al bot.
     *
     * @return array{claimed: bool, assigned: array{id: int, name: string}|null}
     */
    public static function claimConversation(
        int $conversationId,
        int $userId,
        string $userName = '',
        bool $force = false
    ): array {
        $name = self::resolveUserName($userId, $userName);
        $condition = $force
            ? ''
            : ' AND (id_usuario_asignado IS NULL OR id_usuario_asignado = :userIdGuard)';
        $params = [
            'id' => $conversationId,
            'userId' => $userId,
            'userName' => $name,
        ];
        if (!$force) {
            $params['userIdGuard'] = $userId;
        }

        Database::exec(
            'UPDATE crm_conversations
                SET id_usuario_asignado = :userId,
                    nombre_usuario_asignado = :userName,
                    fecha_asignacion = COALESCE(fecha_asignacion, NOW())
              WHERE id_conversation = :id' . $condition,
            $params
        );

        // El veredicto NO sale de rowCount(). MySQL cuenta filas CAMBIADAS, no
        // coincidentes: cuando el dueño vuelve a reclamar la suya —y lo hace en
        // cada mensaje, porque responder reclama— el UPDATE no cambia ningún
        // valor y devuelve 0. Con eso, el panel le decía al asesor que su propio
        // chat lo tenía otro y le ofrecía quitárselo a sí mismo.
        //
        // La pregunta real es "¿de quién es la fila AHORA?", y esa se responde
        // leyéndola. La atomicidad sigue viniendo del UPDATE condicional de
        // arriba: quien no cumple la guarda no escribe nada, así que en una
        // carrera el perdedor lee al ganador y se retira.
        $assigned = self::assignmentOf($conversationId);
        return [
            'claimed' => $assigned !== null && $assigned['id'] === $userId,
            'assigned' => $assigned,
        ];
    }

    /**
     * Suelta la conversación. Se llama al devolverla al bot: un chat en manos de
     * Don Regalo no tiene dueño, y dejarlo asignado haría que el filtro "Mis
     * chats" se llenara de conversaciones que el asesor ya terminó.
     */
    public static function releaseConversation(int $conversationId): void
    {
        Database::exec(
            'UPDATE crm_conversations
                SET id_usuario_asignado = NULL,
                    nombre_usuario_asignado = NULL,
                    fecha_asignacion = NULL
              WHERE id_conversation = :id',
            ['id' => $conversationId]
        );
    }

    /** @return array{id: int, name: string}|null */
    public static function assignmentOf(int $conversationId): ?array
    {
        $row = Database::fetchOne(
            'SELECT id_usuario_asignado, nombre_usuario_asignado
             FROM crm_conversations WHERE id_conversation = :id LIMIT 1',
            ['id' => $conversationId]
        );
        if (!$row || $row['id_usuario_asignado'] === null) {
            return null;
        }
        return [
            'id' => (int) $row['id_usuario_asignado'],
            'name' => (string) ($row['nombre_usuario_asignado'] ?? ''),
        ];
    }

    // ── ventana de servicio de WhatsApp ─────────────────────────────────────

    /** Horas que Meta da para responder libremente tras un mensaje del cliente. */
    const SERVICE_WINDOW_HOURS = 24;

    /**
     * Estado de la ventana de 24 horas, calculado sobre el último mensaje del
     * cliente.
     *
     * Pasadas 24h desde que el cliente escribió por última vez, la Cloud API
     * rechaza cualquier texto libre: solo entran plantillas aprobadas por Meta.
     * El CRM no sabía nada de esto, así que el mensaje del asesor se encolaba,
     * moría con `failed` y en el panel salía un "No se envió" sin ninguna pista
     * de por qué — el asesor reintentaba, y volvía a fallar.
     *
     * `known: false` cuando la conversación no tiene ni un mensaje entrante. Ahí
     * NO se bloquea nada: sin evidencia no se le quita al equipo la posibilidad
     * de escribir. Es la diferencia entre "sabemos que está cerrada" y "no lo
     * sabemos", y solo la primera justifica frenar un envío.
     *
     * @return array{known: bool, open: bool, last_inbound_at: ?string, expires_at: ?string, minutes_left: int}
     */
    public static function serviceWindow(?string $lastInboundAt): array
    {
        $raw = trim((string) ($lastInboundAt ?? ''));
        if ($raw === '') {
            return [
                'known' => false,
                'open' => true,
                'last_inbound_at' => null,
                'expires_at' => null,
                'minutes_left' => 0,
            ];
        }
        $last = strtotime($raw);
        if ($last === false) {
            return [
                'known' => false,
                'open' => true,
                'last_inbound_at' => null,
                'expires_at' => null,
                'minutes_left' => 0,
            ];
        }
        $expires = $last + self::SERVICE_WINDOW_HOURS * 3600;
        $left = (int) floor(($expires - time()) / 60);
        return [
            'known' => true,
            'open' => $left > 0,
            'last_inbound_at' => self::iso($raw),
            'expires_at' => date('c', $expires),
            'minutes_left' => max(0, $left),
        ];
    }

    /** La ventana de una conversación concreta, para validar antes de encolar. */
    public static function serviceWindowFor(int $conversationId): array
    {
        $row = Database::fetchOne(
            'SELECT MAX(fecha_creacion) AS last_inbound_at
             FROM crm_messages
             WHERE id_conversation = :id AND direction_message = \'inbound\'',
            ['id' => $conversationId]
        );
        return self::serviceWindow($row['last_inbound_at'] ?? null);
    }

    public static function upsertLead(array $input): void
    {
        $tenantId = self::ensureTenantId();
        Database::exec(
            'INSERT INTO crm_leads (id_tenant, wa_id, nombre_lead, email_lead, notas_lead, temperatura_lead)
             VALUES (:tenantId, :waId, :name, :email, :notes, :temperatura)
             ON DUPLICATE KEY UPDATE
               nombre_lead = COALESCE(VALUES(nombre_lead), nombre_lead),
               email_lead = COALESCE(VALUES(email_lead), email_lead),
               notas_lead = COALESCE(VALUES(notas_lead), notas_lead),
               temperatura_lead = COALESCE(VALUES(temperatura_lead), temperatura_lead)',
            [
                'tenantId' => $tenantId,
                'waId' => $input['waId'],
                'name' => $input['name'] ?? null,
                'email' => $input['email'] ?? null,
                'notes' => $input['notes'] ?? null,
                'temperatura' => $input['temperatura'] ?? null,
            ]
        );
    }

    public static function getLeadByPhone(string $waId): ?array
    {
        $tenantId = self::ensureTenantId();
        return Database::fetchOne(
            'SELECT * FROM crm_leads WHERE id_tenant = :tenantId AND wa_id = :waId LIMIT 1',
            ['tenantId' => $tenantId, 'waId' => $waId]
        );
    }

    public static function getMemory(string $waId): ?array
    {
        $tenantId = self::ensureTenantId();
        return Database::fetchOne(
            'SELECT * FROM crm_lead_memory WHERE id_tenant = :tenantId AND wa_id = :waId LIMIT 1',
            ['tenantId' => $tenantId, 'waId' => $waId]
        );
    }

    public static function upsertMemory(string $waId, array $patch): void
    {
        $tenantId = self::ensureTenantId();
        $existing = self::getMemory($waId);
        $nombre = $patch['nombre_memory'] ?? $patch['name'] ?? null;
        $email = $patch['email_memory'] ?? $patch['email'] ?? null;
        $objetivo = $patch['objetivo_memory'] ?? $patch['objetivo'] ?? null;
        $situacion = $patch['situacion_memory'] ?? $patch['situacion'] ?? null;
        $temperatura = $patch['temperatura_memory'] ?? $patch['temperatura'] ?? null;
        $resumen = $patch['resumen_memory'] ?? $patch['resumen'] ?? null;

        if (!$existing) {
            Database::exec(
                'INSERT INTO crm_lead_memory
                  (id_tenant, wa_id, nombre_memory, email_memory, objetivo_memory, situacion_memory,
                   temperatura_memory, resumen_memory, first_seen, last_seen)
                 VALUES
                  (:tenantId, :waId, :nombre, :email, :objetivo, :situacion,
                   :temperatura, :resumen, NOW(), NOW())',
                [
                    'tenantId' => $tenantId,
                    'waId' => $waId,
                    'nombre' => $nombre,
                    'email' => $email,
                    'objetivo' => $objetivo,
                    'situacion' => $situacion,
                    'temperatura' => $temperatura,
                    'resumen' => $resumen,
                ]
            );
            return;
        }

        Database::exec(
            'UPDATE crm_lead_memory SET
               nombre_memory = COALESCE(:nombre, nombre_memory),
               email_memory = COALESCE(:email, email_memory),
               objetivo_memory = COALESCE(:objetivo, objetivo_memory),
               situacion_memory = COALESCE(:situacion, situacion_memory),
               temperatura_memory = COALESCE(:temperatura, temperatura_memory),
               resumen_memory = COALESCE(:resumen, resumen_memory),
               last_seen = NOW()
             WHERE id_tenant = :tenantId AND wa_id = :waId',
            [
                'tenantId' => $tenantId,
                'waId' => $waId,
                'nombre' => $nombre,
                'email' => $email,
                'objetivo' => $objetivo,
                'situacion' => $situacion,
                'temperatura' => $temperatura,
                'resumen' => $resumen,
            ]
        );
    }

    public static function getSetting(string $key): ?string
    {
        $tenantId = self::ensureTenantId();
        $row = Database::fetchOne(
            'SELECT valor_setting FROM crm_settings WHERE id_tenant = :tenantId AND llave_setting = :key LIMIT 1',
            ['tenantId' => $tenantId, 'key' => $key]
        );
        return $row ? (string) $row['valor_setting'] : null;
    }

    public static function setSetting(string $key, string $value): void
    {
        $tenantId = self::ensureTenantId();
        Database::exec(
            'INSERT INTO crm_settings (id_tenant, llave_setting, valor_setting)
             VALUES (:tenantId, :key, :value)
             ON DUPLICATE KEY UPDATE valor_setting = VALUES(valor_setting)',
            ['tenantId' => $tenantId, 'key' => $key, 'value' => $value]
        );
    }

    /**
     * Escribe un setting SOLO si su versión sigue siendo la esperada.
     *
     * El estado del harness (`harness_state_{id}`) es un documento JSON completo
     * que varios escritores leen, modifican y vuelven a escribir: el turno del
     * cliente (que tarda segundos, con el LLM en medio), el releaser en segundo
     * plano y el handoff. El lock de Redis serializa los turnos ENTRANTES, pero
     * el releaser corre fuera de él: leía una foto vieja del documento y al
     * guardar se llevaba por delante todo lo que el turno había escrito mientras
     * tanto — el clásico lost update, y sin dejar rastro.
     *
     * Aquí está la única parte que puede ser atómica de verdad: `SELECT ... FOR
     * UPDATE` + `UPDATE` dentro de una transacción. El agente reintenta cuando
     * esto devuelve `false`.
     *
     * `false` NO es un error: es "alguien escribió antes que tú, vuelve a leer".
     * Por eso el endpoint responde 200 y no 409 — un 409 contaría como fallo del
     * CRM en el circuit breaker del agente y acabaría abriéndolo por un caso que
     * es funcionamiento normal.
     */
    public static function casSetting(string $key, string $value, int $expectedVersion): bool
    {
        $tenantId = self::ensureTenantId();
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            $row = Database::fetchOne(
                'SELECT valor_setting FROM crm_settings
                 WHERE id_tenant = :tenantId AND llave_setting = :key
                 LIMIT 1 FOR UPDATE',
                ['tenantId' => $tenantId, 'key' => $key]
            );

            if (!$row) {
                // No existe todavía: solo puede crearlo quien cree estar
                // partiendo de cero. Si alguien esperaba la versión 3, la fila
                // que leyó se borró bajo sus pies y no puede seguir a ciegas.
                if ($expectedVersion !== 0) {
                    $pdo->commit();
                    return false;
                }
                Database::exec(
                    'INSERT INTO crm_settings (id_tenant, llave_setting, valor_setting)
                     VALUES (:tenantId, :key, :value)',
                    ['tenantId' => $tenantId, 'key' => $key, 'value' => $value]
                );
                $pdo->commit();
                return true;
            }

            if (self::settingVersion($row['valor_setting']) !== $expectedVersion) {
                $pdo->commit();
                return false;
            }

            Database::exec(
                'UPDATE crm_settings SET valor_setting = :value
                 WHERE id_tenant = :tenantId AND llave_setting = :key',
                ['tenantId' => $tenantId, 'key' => $key, 'value' => $value]
            );
            $pdo->commit();
            return true;
        } catch (Throwable $error) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $error;
        }
    }

    /**
     * La versión que lleva dentro un documento de estado. 0 si no la tiene.
     *
     * Se lee en PHP y no con JSON_EXTRACT para no depender del soporte de JSON
     * del motor: este CRM corre sobre el MySQL del hosting, pero en desarrollo
     * se levanta sobre MariaDB y las funciones JSON no se comportan igual. Un
     * documento sin `version` (los que existían antes de esto) vale 0, que es
     * justo lo que el agente enviará la primera vez.
     */
    private static function settingVersion(?string $raw): int
    {
        if ($raw === null || $raw === '') {
            return 0;
        }
        $data = json_decode($raw, true);
        if (!is_array($data) || !isset($data['version'])) {
            return 0;
        }
        return (int) $data['version'];
    }

    public static function deleteSetting(string $key): void
    {
        $tenantId = self::ensureTenantId();
        Database::exec(
            'DELETE FROM crm_settings WHERE id_tenant = :tenantId AND llave_setting = :key',
            ['tenantId' => $tenantId, 'key' => $key]
        );
    }

    /** Conserva una venta anunciada por Don Regalo sin duplicar sus reintentos. */
    public static function archiveSale(int $conversationId, array $sale): array
    {
        $tenantId = self::ensureTenantId();
        $conversation = Database::fetchOne(
            'SELECT c.id_conversation, c.id_contact, ct.wa_id, ct.nombre_contact
             FROM crm_conversations c
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             WHERE c.id_tenant = :tenantId AND c.id_conversation = :conversationId
             LIMIT 1',
            ['tenantId' => $tenantId, 'conversationId' => $conversationId]
        );
        if (!$conversation) {
            throw new RuntimeException('Conversation not found');
        }

        $closedAt = isset($sale['cerrada_en']) ? (int) $sale['cerrada_en'] : time();
        if ($closedAt <= 0) {
            $closedAt = time();
        }
        $snapshot = json_encode(
            $sale,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
        if ($snapshot === false) {
            throw new RuntimeException('Invalid sale snapshot');
        }

        // Quién cerró la venta viaja DENTRO del snapshot, no como parámetro
        // aparte. `markSaleDelivered` vuelve a archivar la misma ficha al
        // confirmar la entrega: si el origen fuera un argumento con default
        // 'bot', ese segundo archivado le borraría la autoría a toda venta
        // registrada por un asesor (misma marca de cierre → mismo ON DUPLICATE
        // KEY → misma fila). Leyéndolo del snapshot, sobrevive a los round trips.
        $origin = ($sale['origen'] ?? 'bot') === 'asesor' ? 'asesor' : 'bot';
        $amount = isset($sale['monto_sol']) && $sale['monto_sol'] !== ''
            ? (float) $sale['monto_sol']
            : null;
        $sellerId = isset($sale['registrado_por_id']) && (int) $sale['registrado_por_id'] > 0
            ? (int) $sale['registrado_por_id']
            : null;

        Database::exec(
            'INSERT INTO crm_ventas_historiales (
               id_tenant, id_conversation, id_contact,
               wa_id_venta_historial, nombre_contacto_venta_historial,
               producto_venta_historial, distrito_venta_historial,
               envio_sol_venta_historial, fecha_entrega_venta_historial,
               horario_venta_historial, id_pedido_temporal,
               motivo_venta_historial, marca_cierre_venta_historial,
               fecha_cierre_venta_historial, estado_venta_historial,
               origen_venta_historial, monto_venta_historial,
               id_usuario_registro, nombre_usuario_registro,
               snapshot_venta_historial
             ) VALUES (
               :tenantId, :conversationId, :contactId,
               :waId, :contactName, :product, :district, :shipping,
               :deliveryDate, :schedule, :temporaryOrderId, :reason,
               :closedMark, FROM_UNIXTIME(:closedAt), \'pendiente\',
               :origin, :amount, :sellerId, :sellerName, :snapshot
             )
             ON DUPLICATE KEY UPDATE
               producto_venta_historial = VALUES(producto_venta_historial),
               distrito_venta_historial = VALUES(distrito_venta_historial),
               envio_sol_venta_historial = VALUES(envio_sol_venta_historial),
               fecha_entrega_venta_historial = VALUES(fecha_entrega_venta_historial),
               horario_venta_historial = VALUES(horario_venta_historial),
               id_pedido_temporal = VALUES(id_pedido_temporal),
               motivo_venta_historial = VALUES(motivo_venta_historial),
               origen_venta_historial = VALUES(origen_venta_historial),
               monto_venta_historial = VALUES(monto_venta_historial),
               id_usuario_registro = VALUES(id_usuario_registro),
               nombre_usuario_registro = VALUES(nombre_usuario_registro),
               snapshot_venta_historial = VALUES(snapshot_venta_historial)',
            [
                'tenantId' => $tenantId,
                'conversationId' => $conversationId,
                'contactId' => (int) $conversation['id_contact'],
                'waId' => (string) $conversation['wa_id'],
                'contactName' => $conversation['nombre_contact'],
                'product' => (string) ($sale['producto'] ?? ''),
                'district' => $sale['distrito'] ?? null,
                'shipping' => $sale['envio_sol'] ?? null,
                'deliveryDate' => $sale['fecha'] ?? null,
                'schedule' => $sale['horario'] ?? null,
                'temporaryOrderId' => $sale['pedido_temporal_id'] ?? null,
                'reason' => $sale['motivo'] ?? null,
                'closedMark' => $closedAt,
                'closedAt' => $closedAt,
                'origin' => $origin,
                'amount' => $amount,
                'sellerId' => $sellerId,
                'sellerName' => $sale['registrado_por'] ?? null,
                'snapshot' => $snapshot,
            ]
        );

        return Database::fetchOne(
            'SELECT * FROM crm_ventas_historiales
             WHERE id_tenant = :tenantId
               AND id_conversation = :conversationId
               AND marca_cierre_venta_historial = :closedMark
             LIMIT 1',
            [
                'tenantId' => $tenantId,
                'conversationId' => $conversationId,
                'closedMark' => $closedAt,
            ]
        ) ?? [];
    }

    /** Guarda historial y ficha activa en una sola transacción. */
    public static function storeActiveSale(int $conversationId, array $sale): array
    {
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            $archived = self::archiveSale($conversationId, $sale);
            $key = 'sale_' . $conversationId;
            if (($archived['estado_venta_historial'] ?? '') === 'entregado') {
                self::deleteSetting($key);
            } else {
                $snapshot = json_encode(
                    $sale,
                    JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
                );
                if ($snapshot === false) {
                    throw new RuntimeException('Invalid sale snapshot');
                }
                self::setSetting($key, $snapshot);
            }
            $pdo->commit();
            return $archived;
        } catch (Throwable $error) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $error;
        }
    }

    /**
     * Venta que cerró un asesor a mano, no el bot.
     *
     * El chat que llega a un humano es justo el que el bot NO pudo cerrar, así
     * que la mayoría de lo que se vende por el CRM se cerraba fuera de todo
     * registro: el Historial de ventas mostraba solo los cierres del agente y
     * los reportes contaban esa fracción como si fuera el total.
     *
     * Pasa por `storeActiveSale`, o sea por el mismo camino que el bot: deja la
     * ficha verde en el chat Y la fila en el historial, en una transacción. Una
     * venta manual que solo apareciera en el historial obligaría al equipo a
     * mirar en dos sitios para saber qué falta entregar.
     *
     * @param array<string,mixed> $input
     */
    public static function registerManualSale(
        int $conversationId,
        array $input,
        int $userId,
        string $userName = ''
    ): array {
        $texto = static function ($value, int $max): ?string {
            $clean = trim((string) ($value ?? ''));
            if ($clean === '') {
                return null;
            }
            // Las columnas son VARCHAR cortas y MySQL en modo estricto rechaza el
            // INSERT entero: perder la cola de un texto largo es mejor que perder
            // la venta.
            return mb_substr($clean, 0, $max);
        };
        $numero = static function ($value): ?float {
            if ($value === null || $value === '' || !is_numeric($value)) {
                return null;
            }
            return round((float) $value, 2);
        };

        $product = $texto($input['producto'] ?? null, 255);
        if ($product === null) {
            throw new RuntimeException('El producto es obligatorio');
        }

        $sale = [
            'producto' => $product,
            'distrito' => $texto($input['distrito'] ?? null, 120),
            'envio_sol' => $numero($input['envio_sol'] ?? null),
            'monto_sol' => $numero($input['monto_sol'] ?? null),
            'fecha' => $texto($input['fecha'] ?? null, 20),
            'horario' => $texto($input['horario'] ?? null, 80),
            'motivo' => $texto($input['motivo'] ?? null, 255),
            'origen' => 'asesor',
            'registrado_por_id' => $userId,
            'registrado_por' => self::resolveUserName($userId, $userName),
            'cerrada_en' => time(),
        ];

        $pedido = $input['pedido_temporal_id'] ?? null;
        if ($pedido !== null && $pedido !== '' && ctype_digit((string) $pedido)) {
            $sale['pedido_temporal_id'] = (int) $pedido;
        }

        return self::storeActiveSale($conversationId, $sale);
    }

    /** Marca la ficha activa como entregada y solo entonces la retira del chat. */
    public static function markSaleDelivered(int $conversationId, int $userId): array
    {
        $tenantId = self::ensureTenantId();
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            $raw = self::getSetting('sale_' . $conversationId);
            $sale = $raw !== null ? json_decode($raw, true) : null;
            if (is_array($sale)) {
                self::archiveSale($conversationId, $sale);
            }

            $row = Database::fetchOne(
                'SELECT * FROM crm_ventas_historiales
                 WHERE id_tenant = :tenantId
                   AND id_conversation = :conversationId
                 ORDER BY marca_cierre_venta_historial DESC
                 LIMIT 1 FOR UPDATE',
                ['tenantId' => $tenantId, 'conversationId' => $conversationId]
            );
            if (!$row) {
                throw new RuntimeException('Sale not found');
            }

            $userName = self::userDisplayName($userId);

            Database::exec(
                'UPDATE crm_ventas_historiales
                 SET estado_venta_historial = \'entregado\',
                     fecha_confirmacion_entrega = COALESCE(fecha_confirmacion_entrega, NOW()),
                     id_usuario = COALESCE(id_usuario, :userId),
                     nombre_usuario_confirmacion =
                       COALESCE(nombre_usuario_confirmacion, :userName)
                 WHERE id_venta_historial = :saleId',
                [
                    'userId' => $userId,
                    'userName' => $userName,
                    'saleId' => (int) $row['id_venta_historial'],
                ]
            );

            self::deleteSetting('sale_' . $conversationId);
            $updated = Database::fetchOne(
                'SELECT * FROM crm_ventas_historiales
                 WHERE id_venta_historial = :saleId LIMIT 1',
                ['saleId' => (int) $row['id_venta_historial']]
            ) ?? [];
            $pdo->commit();
            return $updated;
        } catch (Throwable $error) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $error;
        }
    }

    /** Estados que puede tener una venta en el historial. */
    const SALE_STATUSES = ['pendiente', 'entregado'];

    /**
     * Cambia el estado de UNA venta del historial, en los dos sentidos.
     *
     * Distinto de `markSaleDelivered`, que actúa por conversación y toma la
     * última venta de esa conversación: eso vale desde el chat, donde solo hay
     * una ficha activa, pero desde el historial marcaría la venta equivocada si
     * un cliente compró dos veces. Aquí se actúa sobre el `id_venta_historial`
     * que el vendedor tiene delante.
     *
     * Y va en ambos sentidos a propósito: confirmar una entrega era irreversible
     * y un clic por error se quedaba así para siempre.
     */
    public static function setSaleStatus(int $saleId, string $status, int $userId): array
    {
        if (!in_array($status, self::SALE_STATUSES, true)) {
            throw new RuntimeException('Invalid sale status');
        }
        $tenantId = self::ensureTenantId();
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            $row = Database::fetchOne(
                'SELECT * FROM crm_ventas_historiales
                 WHERE id_venta_historial = :saleId AND id_tenant = :tenantId
                 LIMIT 1 FOR UPDATE',
                ['saleId' => $saleId, 'tenantId' => $tenantId]
            );
            if (!$row) {
                throw new RuntimeException('Sale not found');
            }

            $entregado = $status === 'entregado';
            // Sin COALESCE: si se revierte y se vuelve a confirmar, la auditoría
            // debe decir quién dejó la venta como está AHORA, no quién la tocó la
            // primera vez.
            Database::exec(
                'UPDATE crm_ventas_historiales
                 SET estado_venta_historial = :status,
                     fecha_confirmacion_entrega = ' . ($entregado ? 'NOW()' : 'NULL') . ',
                     id_usuario = :userId,
                     nombre_usuario_confirmacion = :userName
                 WHERE id_venta_historial = :saleId',
                [
                    'status' => $status,
                    'userId' => $userId,
                    'userName' => self::userDisplayName($userId),
                    'saleId' => $saleId,
                ]
            );

            // Al confirmar la entrega se retira la ficha verde del chat, igual que
            // hace el botón del inbox: si no, el asesor ve un pendiente que ya no
            // lo es. Al revertir NO se resucita — la venta ya está archivada y
            // devolver la ficha al chat sería reabrir algo que nadie pidió.
            if ($entregado) {
                self::deleteSetting('sale_' . (int) $row['id_conversation']);
            }

            $updated = Database::fetchOne(
                'SELECT * FROM crm_ventas_historiales
                 WHERE id_venta_historial = :saleId LIMIT 1',
                ['saleId' => $saleId]
            ) ?? [];
            $pdo->commit();
            return $updated;
        } catch (Throwable $error) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $error;
        }
    }

    /**
     * Nombre a auditar: el que ya trae la sesión, o el de la tabla `usuarios`.
     *
     * La sesión es la fuente preferida porque `Auth::login` ya compuso nombre +
     * apellidos, y porque `userDisplayName` devuelve '' cuando la tabla legacy
     * del e-commerce no tiene las columnas esperadas — y una nota firmada por
     * nadie no sirve para saber quién la escribió.
     */
    private static function resolveUserName(int $userId, string $given = ''): string
    {
        $given = trim($given);
        return $given !== '' ? $given : self::userDisplayName($userId);
    }

    /** Nombre del usuario para la auditoría; vacío si la tabla legacy difiere. */
    private static function userDisplayName(int $userId): string
    {
        try {
            $user = Database::fetchOne(
                'SELECT nombre_usuario, apellidos_usuario
                 FROM usuarios WHERE id_usuario = :userId LIMIT 1',
                ['userId' => $userId]
            );
        } catch (Throwable $ignored) {
            // La identidad numérica (`id_usuario`) sigue auditada igual.
            return '';
        }
        if (!$user) {
            return '';
        }
        return trim(
            (string) ($user['nombre_usuario'] ?? '') . ' ' .
            (string) ($user['apellidos_usuario'] ?? '')
        );
    }

    /** Listado del módulo de historial, siempre aislado por tenant. */
    public static function listSalesHistory(
        ?string $from,
        ?string $to,
        string $status = '',
        string $query = ''
    ): array {
        $tenantId = self::ensureTenantId();
        $from = $from ?: date('Y-m-d', strtotime('-30 days'));
        $to = $to ?: date('Y-m-d');
        $where = [
            'id_tenant = :tenantId',
            'fecha_cierre_venta_historial >= :fromDate',
            'fecha_cierre_venta_historial < DATE_ADD(:toDate, INTERVAL 1 DAY)',
        ];
        $params = [
            'tenantId' => $tenantId,
            'fromDate' => $from,
            'toDate' => $to,
        ];

        if (in_array($status, ['pendiente', 'entregado'], true)) {
            $where[] = 'estado_venta_historial = :status';
            $params['status'] = $status;
        }
        $query = trim($query);
        if ($query !== '') {
            $where[] = '(nombre_contacto_venta_historial LIKE :queryContact
                         OR wa_id_venta_historial LIKE :queryWhatsapp
                         OR producto_venta_historial LIKE :queryProduct
                         OR CAST(id_pedido_temporal AS CHAR) LIKE :queryOrder)';
            $needle = '%' . $query . '%';
            $params['queryContact'] = $needle;
            $params['queryWhatsapp'] = $needle;
            $params['queryProduct'] = $needle;
            $params['queryOrder'] = $needle;
        }

        return Database::fetchAll(
            'SELECT * FROM crm_ventas_historiales
             WHERE ' . implode(' AND ', $where) . '
             ORDER BY fecha_cierre_venta_historial DESC
             LIMIT 500',
            $params
        );
    }

    // ── notas internas ──────────────────────────────────────────────────────

    /**
     * Notas de la conversación, las recientes primero.
     *
     * Nunca tocan `crm_messages`: todo lo que entra en el hilo es candidato a
     * salir por la Cloud API, y una nota interna que comparta tabla con los
     * mensajes está a un bug de distancia de acabar en el teléfono del cliente.
     */
    public static function listNotes(int $conversationId, int $limit = 50): array
    {
        $limit = max(1, min(200, $limit));
        return Database::fetchAll(
            "SELECT n.id_nota, n.nota_texto, n.id_usuario, n.nombre_usuario, n.fecha_creacion
             FROM crm_conversation_notes n
             WHERE n.id_conversation = :conversationId AND n.id_tenant = :tenantId
             ORDER BY n.id_nota DESC
             LIMIT {$limit}",
            ['conversationId' => $conversationId, 'tenantId' => self::ensureTenantId()]
        );
    }

    public static function addNote(
        int $conversationId,
        string $text,
        int $userId,
        string $userName = ''
    ): array {
        $clean = trim($text);
        if ($clean === '') {
            throw new RuntimeException('La nota está vacía');
        }
        $id = Database::execute(
            'INSERT INTO crm_conversation_notes
               (id_tenant, id_conversation, id_usuario, nombre_usuario, nota_texto)
             VALUES (:tenantId, :conversationId, :userId, :userName, :text)',
            [
                'tenantId' => self::ensureTenantId(),
                'conversationId' => $conversationId,
                'userId' => $userId,
                'userName' => self::resolveUserName($userId, $userName),
                'text' => mb_substr($clean, 0, 4000),
            ]
        );
        return Database::fetchOne(
            'SELECT id_nota, nota_texto, id_usuario, nombre_usuario, fecha_creacion
             FROM crm_conversation_notes WHERE id_nota = :id LIMIT 1',
            ['id' => $id]
        ) ?? [];
    }

    // ── seguimientos ────────────────────────────────────────────────────────

    const FOLLOWUP_STATUSES = ['pendiente', 'hecho', 'cancelado'];

    /** Seguimientos de una conversación: pendientes arriba, por fecha. */
    public static function listFollowups(int $conversationId, int $limit = 30): array
    {
        $limit = max(1, min(100, $limit));
        return Database::fetchAll(
            "SELECT * FROM crm_seguimientos
             WHERE id_conversation = :conversationId AND id_tenant = :tenantId
             ORDER BY (estado_seguimiento = 'pendiente') DESC, fecha_programada ASC
             LIMIT {$limit}",
            ['conversationId' => $conversationId, 'tenantId' => self::ensureTenantId()]
        );
    }

    /**
     * Los que ya vencieron y siguen pendientes, para el rail del inbox.
     *
     * Sin esta lista el módulo no existe: un recordatorio guardado en una tabla
     * que nadie mira es exactamente igual de útil que no haberlo guardado. Se
     * ordena por el más vencido primero, que es a quien peor se le está quedando.
     */
    public static function listDueFollowups(int $limit = 30): array
    {
        $limit = max(1, min(100, $limit));
        return Database::fetchAll(
            "SELECT s.*, ct.wa_id, ct.nombre_contact
             FROM crm_seguimientos s
             JOIN crm_conversations c ON c.id_conversation = s.id_conversation
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             WHERE s.id_tenant = :tenantId
               AND s.estado_seguimiento = 'pendiente'
               AND s.fecha_programada <= NOW()
             ORDER BY s.fecha_programada ASC
             LIMIT {$limit}",
            ['tenantId' => self::ensureTenantId()]
        );
    }

    /**
     * Programa un seguimiento. `$when` en formato 'Y-m-d H:i' (hora local).
     *
     * Se guarda con hora y no solo con día a propósito: un recordatorio que
     * vence a medianoche aparece al fondo del turno siguiente, cuando el motivo
     * por el que se programó ("llamarla antes de que cierre") ya pasó.
     */
    public static function addFollowup(
        int $conversationId,
        string $reason,
        string $when,
        int $userId,
        string $userName = ''
    ): array {
        $motivo = trim($reason);
        if ($motivo === '') {
            throw new RuntimeException('El motivo del seguimiento es obligatorio');
        }
        $timestamp = strtotime($when);
        if ($timestamp === false) {
            throw new RuntimeException('Fecha de seguimiento inválida');
        }

        $id = Database::execute(
            'INSERT INTO crm_seguimientos
               (id_tenant, id_conversation, motivo_seguimiento, fecha_programada,
                estado_seguimiento, id_usuario, nombre_usuario)
             VALUES (:tenantId, :conversationId, :reason, :when, \'pendiente\', :userId, :userName)',
            [
                'tenantId' => self::ensureTenantId(),
                'conversationId' => $conversationId,
                'reason' => mb_substr($motivo, 0, 255),
                'when' => date('Y-m-d H:i:s', $timestamp),
                'userId' => $userId,
                'userName' => self::resolveUserName($userId, $userName),
            ]
        );
        return Database::fetchOne(
            'SELECT * FROM crm_seguimientos WHERE id_seguimiento = :id LIMIT 1',
            ['id' => $id]
        ) ?? [];
    }

    /**
     * Marca un seguimiento como hecho o cancelado, o lo reabre.
     *
     * Va en los dos sentidos como `setSaleStatus`, y por lo mismo: cerrar por
     * error un recordatorio que era el único rastro de un lead a medias no puede
     * ser irreversible.
     */
    public static function setFollowupStatus(
        int $followupId,
        string $status,
        int $userId,
        string $userName = ''
    ): array {
        if (!in_array($status, self::FOLLOWUP_STATUSES, true)) {
            throw new RuntimeException('Invalid followup status');
        }
        $tenantId = self::ensureTenantId();
        $abierto = $status === 'pendiente';
        $changed = Database::affect(
            'UPDATE crm_seguimientos
                SET estado_seguimiento = :status,
                    fecha_cierre = ' . ($abierto ? 'NULL' : 'NOW()') . ',
                    id_usuario_cierre = ' . ($abierto ? 'NULL' : ':userId') . ',
                    nombre_usuario_cierre = ' . ($abierto ? 'NULL' : ':userName') . '
              WHERE id_seguimiento = :id AND id_tenant = :tenantId',
            $abierto
                ? ['status' => $status, 'id' => $followupId, 'tenantId' => $tenantId]
                : [
                    'status' => $status,
                    'userId' => $userId,
                    'userName' => self::resolveUserName($userId, $userName),
                    'id' => $followupId,
                    'tenantId' => $tenantId,
                ]
        );
        if ($changed === 0) {
            // Sin filas tocadas puede ser que no exista, que sea de otro tenant o
            // que ya estuviera en ese estado. Distinguirlo con un SELECT evita
            // decirle "no existe" a un seguimiento que el compañero acaba de cerrar.
            $row = Database::fetchOne(
                'SELECT * FROM crm_seguimientos
                 WHERE id_seguimiento = :id AND id_tenant = :tenantId LIMIT 1',
                ['id' => $followupId, 'tenantId' => $tenantId]
            );
            if (!$row) {
                throw new RuntimeException('Followup not found');
            }
            return $row;
        }
        return Database::fetchOne(
            'SELECT * FROM crm_seguimientos WHERE id_seguimiento = :id LIMIT 1',
            ['id' => $followupId]
        ) ?? [];
    }

    public static function enqueueOutbox(array $input): int
    {
        return Database::execute(
            'INSERT INTO crm_outbox
              (id_conversation, wa_id, content_outbox, type_outbox, media_path, filename_outbox, reply_to_wa_id, status_outbox)
             VALUES (:conversationId, :waId, :content, :type, :mediaPath, :filename, :replyToWaId, \'pending\')',
            [
                'conversationId' => $input['conversationId'],
                'waId' => $input['waId'],
                'content' => $input['content'],
                'type' => $input['type'] ?? 'text',
                'mediaPath' => $input['mediaPath'] ?? null,
                // El nombre del adjunto se GUARDA, no solo se empuja. Iba solo en
                // el payload del push, así que cuando el push fallaba —que es
                // cuando entra el drenaje— el nombre ya no existía y el PDF le
                // llegaba al cliente como "documento", sin extensión. Ver la
                // migración 015.
                'filename' => ($input['filename'] ?? '') !== '' ? $input['filename'] : null,
                // Mensaje al que responde el asesor: viaja hasta la Cloud API para
                // que el cliente vea la cita en su WhatsApp.
                'replyToWaId' => $input['replyToWaId'] ?? null,
            ]
        );
    }

    /**
     * Reclama la fila para enviarla. `true` solo para el primero que llega.
     *
     * Dos caminos entregan el mismo outbox — el push del CRM al agente y el
     * drenaje periódico — y la fila seguía en 'pending' durante toda la llamada
     * a la Cloud API. Si esa llamada tardaba más que el tick del drenaje, ambos
     * la enviaban: un "No disculpe. Somos de Lima" del asesor le llegó tres
     * veces al cliente. El UPDATE condicional es atómico bajo el lock de fila de
     * InnoDB, así que exactamente uno ve rowCount() === 1.
     */
    public static function claimOutbox(int $id): bool
    {
        return Database::affect(
            "UPDATE crm_outbox SET status_outbox = 'sending'
             WHERE id_outbox = :id AND status_outbox = 'pending'",
            ['id' => $id]
        ) === 1;
    }

    /** Segundos que puede quedarse una fila en 'sending' antes de darla por muerta. */
    const OUTBOX_SENDING_TTL = 180;

    public static function listPendingOutbox(int $limit = 30): array
    {
        $limit = max(1, min(100, $limit));
        // Si el agente murió entre el claim y el envío, la fila se quedaría en
        // 'sending' para siempre y el mensaje del asesor no saldría nunca. A los
        // 3 minutos vuelve a la cola: es más tiempo del que tarda cualquier envío
        // real, incluido un adjunto (60s de timeout).
        Database::exec(
            "UPDATE crm_outbox SET status_outbox = 'pending'
             WHERE status_outbox = 'sending'
               AND fecha_creacion < DATE_SUB(NOW(), INTERVAL :ttl SECOND)",
            ['ttl' => self::OUTBOX_SENDING_TTL]
        );
        return Database::fetchAll(
            "SELECT * FROM crm_outbox WHERE status_outbox = 'pending' ORDER BY id_outbox ASC LIMIT {$limit}"
        );
    }

    public static function markOutbox(int $id, string $status, ?string $error = null): void
    {
        Database::exec(
            'UPDATE crm_outbox
             SET status_outbox = :status,
                 error_outbox = :error,
                 fecha_enviado = CASE WHEN :status2 = \'sent\' THEN NOW() ELSE fecha_enviado END
             WHERE id_outbox = :id',
            [
                'id' => $id,
                'status' => $status,
                'status2' => $status,
                'error' => $error,
            ]
        );
    }

    /**
     * Reclama trabajos de embedding con lock transaccional. Los claims que
     * quedaron huérfanos por reinicio vuelven a pending después de 10 minutos.
     *
     * @return array<int,array<string,mixed>>
     */
    public static function claimEmbeddingJobs(int $limit = 10): array
    {
        $limit = max(1, min(50, $limit));
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            $pdo->exec(
                "UPDATE producto_embedding_jobs
                 SET status = 'pending', claimed_at = NULL
                 WHERE status = 'processing'
                   AND claimed_at < DATE_SUB(NOW(), INTERVAL 10 MINUTE)"
            );
            $rows = $pdo->query(
                "SELECT id_job, id_producto, reason, attempts
                 FROM producto_embedding_jobs
                 WHERE status = 'pending' AND available_at <= NOW()
                 ORDER BY id_job ASC
                 LIMIT {$limit}
                 FOR UPDATE"
            )->fetchAll();
            if ($rows) {
                $ids = array_map('intval', array_column($rows, 'id_job'));
                $placeholders = implode(',', array_fill(0, count($ids), '?'));
                $stmt = $pdo->prepare(
                    "UPDATE producto_embedding_jobs
                     SET status = 'processing', claimed_at = NOW(), attempts = attempts + 1
                     WHERE id_job IN ({$placeholders}) AND status = 'pending'"
                );
                $stmt->execute($ids);
            }
            $pdo->commit();
            return $rows;
        } catch (Throwable $error) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $error;
        }
    }

    /**
     * Confirma, elimina o reencola un trabajo reclamado.
     *
     * @param array<string,mixed> $input
     */
    public static function finishEmbeddingJob(int $jobId, array $input): bool
    {
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            $stmt = $pdo->prepare(
                "SELECT id_job, id_producto, attempts
                 FROM producto_embedding_jobs
                 WHERE id_job = ? AND status = 'processing'
                 FOR UPDATE"
            );
            $stmt->execute([$jobId]);
            $job = $stmt->fetch();
            if (!$job) {
                $pdo->rollBack();
                return false;
            }

            $status = (string) ($input['status'] ?? '');
            $productId = (int) $job['id_producto'];

            if ($status === 'done') {
                $binary = base64_decode((string) ($input['embedding_base64'] ?? ''), true);
                $dimensions = (int) ($input['dimensions'] ?? 0);
                if ($binary === false || $dimensions < 1 || strlen($binary) !== $dimensions * 4) {
                    throw new InvalidArgumentException('Embedding binario inválido');
                }
                $upsert = $pdo->prepare(
                    "INSERT INTO producto_embeddings
                      (id_producto, idioma, content_hash, embedding_model, dimensions,
                       embedding, document_version, status, last_error, embedded_at)
                     VALUES (?, 'es', ?, ?, ?, ?, ?, 'ready', NULL, NOW())
                     ON DUPLICATE KEY UPDATE
                       content_hash = VALUES(content_hash),
                       embedding_model = VALUES(embedding_model),
                       dimensions = VALUES(dimensions),
                       embedding = VALUES(embedding),
                       document_version = VALUES(document_version),
                       status = 'ready',
                       last_error = NULL,
                       embedded_at = NOW()"
                );
                $upsert->bindValue(1, $productId, PDO::PARAM_INT);
                $upsert->bindValue(2, (string) ($input['content_hash'] ?? ''));
                $upsert->bindValue(3, (string) ($input['embedding_model'] ?? ''));
                $upsert->bindValue(4, $dimensions, PDO::PARAM_INT);
                $upsert->bindValue(5, $binary, PDO::PARAM_LOB);
                $upsert->bindValue(6, max(1, (int) ($input['document_version'] ?? 1)), PDO::PARAM_INT);
                $upsert->execute();
                $finalStatus = 'done';
                $availableAt = null;
                $errorText = null;
            } elseif ($status === 'deleted') {
                $delete = $pdo->prepare(
                    'DELETE FROM producto_embeddings WHERE id_producto = ?'
                );
                $delete->execute([$productId]);
                $finalStatus = 'done';
                $availableAt = null;
                $errorText = null;
            } elseif ($status === 'retry') {
                $attempts = (int) $job['attempts'];
                $finalStatus = $attempts >= 5 ? 'error' : 'pending';
                $delay = min(900, 15 * (2 ** max(0, $attempts - 1)));
                $availableAt = (new DateTimeImmutable("+{$delay} seconds"))
                    ->format('Y-m-d H:i:s');
                $errorText = substr((string) ($input['error'] ?? 'worker error'), 0, 1000);
            } else {
                throw new InvalidArgumentException('Estado de embedding job inválido');
            }

            $finish = $pdo->prepare(
                "UPDATE producto_embedding_jobs
                 SET status = ?, available_at = COALESCE(?, available_at),
                     claimed_at = NULL,
                     finished_at = CASE WHEN ? IN ('done', 'error') THEN NOW() ELSE NULL END,
                     last_error = ?
                 WHERE id_job = ?"
            );
            $finish->execute([
                $finalStatus,
                $availableAt,
                $finalStatus,
                $errorText,
                $jobId,
            ]);
            $pdo->commit();
            return true;
        } catch (Throwable $error) {
            if ($pdo->inTransaction()) {
                $pdo->rollBack();
            }
            throw $error;
        }
    }

    public static function getUnansweredConversations(int $minSec, int $maxSec): array
    {
        $tenantId = self::ensureTenantId();
        return Database::fetchAll(
            'SELECT c.id_conversation, ct.wa_id AS phone, ct.nombre_contact AS name,
                    lm.role_message AS last_role, lm.fecha_creacion AS last_at
             FROM crm_conversations c
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             JOIN (
               SELECT m1.id_conversation, m1.role_message, m1.fecha_creacion
               FROM crm_messages m1
               INNER JOIN (
                 SELECT id_conversation, MAX(id_message) AS max_id
                 FROM crm_messages GROUP BY id_conversation
               ) t ON t.max_id = m1.id_message
             ) lm ON lm.id_conversation = c.id_conversation
             WHERE c.id_tenant = :tenantId
               AND c.mode_conversation = \'AI\'
               AND c.bot_active = 1
               AND lm.role_message = \'user\'
               AND lm.fecha_creacion <= DATE_SUB(NOW(), INTERVAL :minSec SECOND)
               AND lm.fecha_creacion >= DATE_SUB(NOW(), INTERVAL :maxSec SECOND)
             ORDER BY lm.fecha_creacion ASC',
            ['tenantId' => $tenantId, 'minSec' => $minSec, 'maxSec' => $maxSec]
        );
    }

    /** Snapshot local para el panel operacional. No expone mensajes. */
    public static function operationalOverview(): array
    {
        $tenantId = self::ensureTenantId();
        $handoff = Database::fetchOne(
            'SELECT
                SUM(CASE WHEN human_support = 1 AND status_conversation = \'open\' THEN 1 ELSE 0 END) AS pending_handoffs,
                SUM(CASE WHEN mode_conversation = \'HUMAN\' AND status_conversation = \'open\' THEN 1 ELSE 0 END) AS human_conversations,
                MAX(CASE WHEN human_support = 1 AND status_conversation = \'open\'
                    THEN TIMESTAMPDIFF(MINUTE, COALESCE(last_message_at, fecha_actualizacion), NOW())
                    ELSE 0 END) AS oldest_handoff_minutes
             FROM crm_conversations
             WHERE id_tenant = :tenantId',
            ['tenantId' => $tenantId]
        ) ?: [];

        $outbox = Database::fetchOne(
            'SELECT
                SUM(status_outbox = \'pending\') AS pending,
                SUM(status_outbox = \'sending\') AS sending,
                SUM(status_outbox = \'failed\') AS failed,
                SUM(status_outbox = \'sent\') AS sent,
                MAX(CASE WHEN status_outbox IN (\'pending\', \'sending\')
                    THEN TIMESTAMPDIFF(MINUTE, fecha_creacion, NOW())
                    ELSE 0 END) AS oldest_pending_minutes
             FROM crm_outbox'
        ) ?: [];

        $handoffs = Database::fetchAll(
            'SELECT c.id_conversation, ct.nombre_contact, c.mode_conversation,
                    c.human_support, c.last_message_at,
                    TIMESTAMPDIFF(
                        MINUTE,
                        COALESCE(c.last_message_at, c.fecha_actualizacion),
                        NOW()
                    ) AS waiting_minutes
             FROM crm_conversations c
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             WHERE c.id_tenant = :tenantId
               AND c.status_conversation = \'open\'
               AND (c.human_support = 1 OR c.mode_conversation = \'HUMAN\')
             ORDER BY c.human_support DESC,
                      COALESCE(c.last_message_at, c.fecha_actualizacion) ASC
             LIMIT 12',
            ['tenantId' => $tenantId]
        );

        $failedOutbox = Database::fetchAll(
            'SELECT o.id_outbox, o.id_conversation, o.type_outbox,
                    LEFT(o.error_outbox, 300) AS error_outbox, o.fecha_creacion
             FROM crm_outbox o
             JOIN crm_conversations c ON c.id_conversation = o.id_conversation
             WHERE c.id_tenant = :tenantId AND o.status_outbox = \'failed\'
             ORDER BY o.id_outbox DESC
             LIMIT 10',
            ['tenantId' => $tenantId]
        );

        return [
            'generated_at' => gmdate('c'),
            'handoffs' => [
                'pending' => (int) ($handoff['pending_handoffs'] ?? 0),
                'human_conversations' => (int) ($handoff['human_conversations'] ?? 0),
                'oldest_minutes' => (int) ($handoff['oldest_handoff_minutes'] ?? 0),
                'items' => $handoffs,
            ],
            'outbox' => [
                'pending' => (int) ($outbox['pending'] ?? 0),
                'sending' => (int) ($outbox['sending'] ?? 0),
                'failed' => (int) ($outbox['failed'] ?? 0),
                'sent' => (int) ($outbox['sent'] ?? 0),
                'oldest_pending_minutes' => (int) ($outbox['oldest_pending_minutes'] ?? 0),
                'failed_items' => $failedOutbox,
            ],
        ];
    }

    /** KPIs para la página de reportes. */
    public static function reportsOverview(?string $from, ?string $to): array
    {
        $tenantId = self::ensureTenantId();
        $from = $from ?: date('Y-m-d', strtotime('-30 days'));
        $to = $to ?: date('Y-m-d');
        $fromDt = $from . ' 00:00:00';
        $toDt = $to . ' 23:59:59';

        $conversations = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_conversations
             WHERE id_tenant = :t AND fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $messages = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_messages m
             JOIN crm_conversations c ON c.id_conversation = m.id_conversation
             WHERE c.id_tenant = :t AND m.fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $human = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_conversations
             WHERE id_tenant = :t AND mode_conversation = \'HUMAN\'
               AND fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $needsHelp = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_conversations
             WHERE id_tenant = :t AND human_support = 1 AND status_conversation = \'open\'',
            ['t' => $tenantId]
        )['n'] ?? 0);

        $leads = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_leads
             WHERE id_tenant = :t AND fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $inbound = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_messages m
             JOIN crm_conversations c ON c.id_conversation = m.id_conversation
             WHERE c.id_tenant = :t AND m.direction_message = \'inbound\'
               AND m.fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $outboundBot = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_messages m
             JOIN crm_conversations c ON c.id_conversation = m.id_conversation
             WHERE c.id_tenant = :t AND m.sender_type = \'bot\'
               AND m.fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $outboundAgent = (int) (Database::fetchOne(
            'SELECT COUNT(*) AS n FROM crm_messages m
             JOIN crm_conversations c ON c.id_conversation = m.id_conversation
             WHERE c.id_tenant = :t AND m.sender_type = \'agent\'
               AND m.fecha_creacion BETWEEN :f AND :to',
            ['t' => $tenantId, 'f' => $fromDt, 'to' => $toDt]
        )['n'] ?? 0);

        $ai = max(0, $conversations - $human);
        $pctHuman = $conversations > 0 ? round(($human / $conversations) * 100, 1) : 0.0;

        return [
            'from' => $from,
            'to' => $to,
            'conversations' => $conversations,
            'messages' => $messages,
            'inbound_messages' => $inbound,
            'bot_messages' => $outboundBot,
            'agent_messages' => $outboundAgent,
            'mode_ai' => $ai,
            'mode_human' => $human,
            'pct_human' => $pctHuman,
            'open_needs_help' => $needsHelp,
            'leads' => $leads,
            'catalog_api_base' => Auth::config()['catalog_api_base'] ?? null,
        ];
    }

    public static function reportsConversations(?string $from, ?string $to, int $limit = 100): array
    {
        $tenantId = self::ensureTenantId();
        $from = $from ?: date('Y-m-d', strtotime('-30 days'));
        $to = $to ?: date('Y-m-d');
        $limit = max(1, min(500, $limit));
        return Database::fetchAll(
            "SELECT c.id_conversation, c.mode_conversation, c.human_support, c.bot_active,
                    c.status_conversation, c.last_message_at, c.fecha_creacion,
                    ct.wa_id, ct.nombre_contact,
                    (SELECT COUNT(*) FROM crm_messages m WHERE m.id_conversation = c.id_conversation) AS msg_count
             FROM crm_conversations c
             JOIN crm_contacts ct ON ct.id_contact = c.id_contact
             WHERE c.id_tenant = :t
               AND c.fecha_creacion BETWEEN :f AND :to
             ORDER BY c.fecha_creacion DESC
             LIMIT {$limit}",
            [
                't' => $tenantId,
                'f' => $from . ' 00:00:00',
                'to' => $to . ' 23:59:59',
            ]
        );
    }

    /**
     * Serie "conversaciones por día" para el gráfico de reportes.
     * Rellena con 0 los días sin actividad para que la línea no se corte.
     *
     * @return list<array{date: string, label: string, value: int}>
     */
    public static function reportsDaily(?string $from, ?string $to): array
    {
        $tenantId = self::ensureTenantId();
        $from = $from ?: date('Y-m-d', strtotime('-30 days'));
        $to = $to ?: date('Y-m-d');

        $rows = Database::fetchAll(
            'SELECT DATE(fecha_creacion) AS d, COUNT(*) AS n
             FROM crm_conversations
             WHERE id_tenant = :t AND fecha_creacion BETWEEN :f AND :to
             GROUP BY DATE(fecha_creacion)
             ORDER BY d ASC',
            [
                't' => $tenantId,
                'f' => $from . ' 00:00:00',
                'to' => $to . ' 23:59:59',
            ]
        );

        $counts = [];
        foreach ($rows as $row) {
            $counts[(string) $row['d']] = (int) $row['n'];
        }

        $weekdays = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
        $span = (int) floor((strtotime($to) - strtotime($from)) / 86400) + 1;
        // Hasta una semana el día de la semana se lee solo; más allá se repite y confunde.
        $useWeekday = $span <= 7;

        $series = [];
        $cursor = strtotime($from);
        $end = strtotime($to);
        // Cota de seguridad: rangos enormes no deben reventar el gráfico.
        for ($i = 0; $cursor <= $end && $i < 180; $i++) {
            $key = date('Y-m-d', $cursor);
            $series[] = [
                'date' => $key,
                'label' => $useWeekday ? $weekdays[(int) date('w', $cursor)] : date('j/n', $cursor),
                'value' => $counts[$key] ?? 0,
            ];
            $cursor = strtotime('+1 day', $cursor);
        }

        return $series;
    }

    /**
     * ¿Qué migraciones se han corrido ya en ESTA base?
     *
     * El orden de despliegue es SQL → CRM PHP → agente, y se hace a mano contra
     * el MySQL del hosting. Saltarse un paso no da un error: da una pantalla
     * vacía. Y una tabla de Oportunidades vacía se lee exactamente igual que
     * "no falta nada en el catálogo", que es la conclusión contraria a la
     * verdadera. Esto convierte ese silencio en una respuesta comprobable.
     *
     * Se comprueba una columna concreta por migración, no la tabla a secas: las
     * que añaden columnas (`007`, `012`, `013`) corren sobre tablas que YA
     * existen, así que preguntar por la tabla las daría por aplicadas siempre.
     *
     * @return array{ok: bool, faltan: list<string>, migraciones: array<string, bool>}
     */
    public static function schemaState(): array
    {
        // migración => [tabla, columna]. Añadir aquí cada migración nueva que
        // el código dé por hecha.
        $esperado = [
            '007_lead_anuncio' => ['crm_conversations', 'ad_source_id'],
            '012_venta_manual' => ['crm_ventas_historiales', 'origen_venta_historial'],
            '013_venta_producto_id' => ['crm_ventas_historiales', 'id_producto_venta_historial'],
            '014_demanda_no_cubierta' => ['crm_demanda_no_cubierta', 'consulta_demanda'],
        ];

        $rows = Database::fetchAll(
            'SELECT TABLE_NAME AS t, COLUMN_NAME AS c
               FROM information_schema.COLUMNS
              WHERE TABLE_SCHEMA = DATABASE()',
            []
        );
        $presentes = [];
        foreach ($rows as $row) {
            $presentes[strtolower((string) $row['t']) . '.' . strtolower((string) $row['c'])] = true;
        }

        $migraciones = [];
        $faltan = [];
        foreach ($esperado as $nombre => $par) {
            $clave = strtolower($par[0]) . '.' . strtolower($par[1]);
            $hay = isset($presentes[$clave]);
            $migraciones[$nombre] = $hay;
            if (!$hay) {
                $faltan[] = $nombre;
            }
        }

        return [
            'ok' => $faltan === [],
            'faltan' => $faltan,
            'migraciones' => $migraciones,
        ];
    }

    /**
     * Una búsqueda que el catálogo no pudo satisfacer.
     *
     * Cada miss es una fila, sin unicidad ni contador: dos personas que piden lo
     * mismo el mismo día son dos señales, no una, y agrupando por fecha se ve si
     * algo sube —una tendencia, una fecha del calendario— o si fue un caso
     * suelto. Un contador acumulado borraría el cuándo.
     *
     * La conversación se valida contra el tenant antes de guardarla: el id llega
     * del agente y una FK rota tumbaría el INSERT entero por un dato que es
     * accesorio. Sin conversación la señal de demanda sigue valiendo.
     */
    public static function recordDemandMiss(
        string $query,
        string $resultado = 'vacio',
        int $nResultados = 0,
        ?string $categoria = null,
        ?int $conversationId = null
    ): void {
        $tenantId = self::ensureTenantId();
        $query = trim($query);
        if ($query === '') {
            return;
        }
        if (!in_array($resultado, ['vacio', 'aproximado'], true)) {
            $resultado = 'vacio';
        }

        if ($conversationId !== null) {
            $exists = Database::fetchOne(
                'SELECT id_conversation FROM crm_conversations
                 WHERE id_tenant = :t AND id_conversation = :c LIMIT 1',
                ['t' => $tenantId, 'c' => $conversationId]
            );
            if (!$exists) {
                $conversationId = null;
            }
        }

        Database::execute(
            'INSERT INTO crm_demanda_no_cubierta
                (id_tenant, id_conversation, consulta_demanda, categoria_demanda,
                 resultado_demanda, n_resultados_demanda)
             VALUES (:t, :c, :q, :cat, :res, :n)',
            [
                't' => $tenantId,
                'c' => $conversationId,
                // El ancho de la columna. El agente ya corta, pero este endpoint
                // es la frontera: con el modo estricto apagado MySQL truncaría
                // en silencio y nadie se enteraría de que faltan términos.
                'q' => mb_substr($query, 0, 255),
                'cat' => $categoria !== null ? mb_substr(trim($categoria), 0, 120) : null,
                'res' => $resultado,
                'n' => max(0, $nResultados),
            ]
        );
    }

    /**
     * Lo más pedido que no tenemos, agrupado.
     *
     * Ordena por vacíos y no por total a propósito: un término que se resolvió
     * con alternativas dejó al cliente algo que comprar, y uno que no encontró
     * nada lo dejó sin nada. Diez veces "globos metálicos" sin resultado pesa
     * más que treinta con alternativa.
     *
     * @return list<array<string, mixed>>
     */
    public static function unmetDemand(?string $from, ?string $to, int $limit = 50): array
    {
        $tenantId = self::ensureTenantId();
        $from = $from ?: date('Y-m-d', strtotime('-30 days'));
        $to = $to ?: date('Y-m-d');
        $limit = max(1, min(200, $limit));

        return Database::fetchAll(
            "SELECT
                consulta_demanda,
                COUNT(*) AS veces,
                SUM(resultado_demanda = 'vacio') AS veces_vacio,
                SUM(resultado_demanda = 'aproximado') AS veces_aproximado,
                COUNT(DISTINCT id_conversation) AS chats,
                MAX(categoria_demanda) AS categoria,
                MAX(fecha_creacion) AS ultima_vez
             FROM crm_demanda_no_cubierta
             WHERE id_tenant = :t
               AND fecha_creacion BETWEEN :f AND :to
             GROUP BY consulta_demanda
             ORDER BY veces_vacio DESC, veces DESC
             LIMIT {$limit}",
            [
                't' => $tenantId,
                'f' => $from . ' 00:00:00',
                'to' => $to . ' 23:59:59',
            ]
        );
    }

    /**
     * Qué anuncio trae compradores y cuál trae curiosos.
     *
     * `ad_source_id` se captura desde la migración 007 y hasta ahora solo se
     * pintaba como tarjeta en el inbox: servía para que el asesor supiera de
     * dónde venía ESE chat, pero nadie podía sumar. Cruzarlo con las ventas
     * responde la pregunta cara — dos anuncios que traen los mismos leads
     * pueden cerrar cantidades muy distintas, y sin esto se optimiza a ciegas
     * por volumen de conversaciones.
     *
     * **El rango filtra por llegada del lead, no por cierre de la venta.** Es
     * una cohorte: de los leads que entraron en estas fechas, cuántos
     * compraron (cuando sea que compraran). Rangear las ventas por su fecha de
     * cierre mezclaría numerador y denominador —ventas de leads de otro
     * periodo sobre los leads de este— y daría una conversión que puede pasar
     * del 100%. A cambio, la cohorte reciente sale artificialmente baja: sus
     * leads aún no han tenido tiempo de cerrar. La vista lo advierte.
     *
     * Las conversaciones sin anuncio salen en su propia fila en vez de
     * descartarse: son la referencia contra la que se comparan las campañas, e
     * incluyen todo lo anterior a la migración 007, que no es recuperable.
     *
     * @return list<array<string, mixed>>
     */
    public static function campaignPerformance(?string $from, ?string $to): array
    {
        $tenantId = self::ensureTenantId();
        $from = $from ?: date('Y-m-d', strtotime('-30 days'));
        $to = $to ?: date('Y-m-d');

        return Database::fetchAll(
            "SELECT
                c.ad_source_id,
                MAX(c.ad_headline)   AS ad_headline,
                MAX(c.ad_source_url) AS ad_source_url,
                MAX(c.ad_source_type) AS ad_source_type,
                MIN(c.fecha_creacion) AS primer_lead,
                COUNT(DISTINCT c.id_conversation) AS leads,
                -- CASE dentro de COUNT(DISTINCT ...) y no SUM(cond): el LEFT
                -- JOIN duplica la conversación por cada venta, y un SUM
                -- contaría dos veces al mismo cliente atendido por un humano.
                COUNT(DISTINCT CASE WHEN c.mode_conversation = 'HUMAN'
                                    THEN c.id_conversation END) AS leads_human,
                COUNT(DISTINCT v.id_venta_historial) AS ventas,
                COALESCE(SUM(v.monto_venta_historial), 0) AS monto
             FROM crm_conversations c
             LEFT JOIN crm_ventas_historiales v
                    ON v.id_conversation = c.id_conversation
                   AND v.id_tenant = c.id_tenant
             WHERE c.id_tenant = :t
               AND c.fecha_creacion BETWEEN :f AND :to
             GROUP BY c.ad_source_id
             ORDER BY leads DESC, ventas DESC
             LIMIT 100",
            [
                't' => $tenantId,
                'f' => $from . ' 00:00:00',
                'to' => $to . ' 23:59:59',
            ]
        );
    }

    /**
     * MySQL DATETIME → ISO-8601 con offset del servidor.
     * Sin el offset, el navegador lee "2026-07-12 17:03:00" como hora local suya
     * y las horas del inbox se desplazan si el servidor no está en la misma zona.
     */
    public static function iso(?string $datetime): ?string
    {
        if ($datetime === null || $datetime === '') {
            return null;
        }
        try {
            return (new DateTimeImmutable($datetime))->format(DateTimeInterface::ATOM);
        } catch (Exception $e) {
            return null;
        }
    }

    /** Forma que consume el panel. `is_due` lo decide el servidor, no el navegador. */
    public static function mapFollowup(array $f): array
    {
        $due = self::iso($f['fecha_programada'] ?? null);
        return [
            'id' => (int) ($f['id_seguimiento'] ?? 0),
            'conversation_id' => (int) ($f['id_conversation'] ?? 0),
            'reason' => (string) ($f['motivo_seguimiento'] ?? ''),
            'due_at' => $due,
            'status' => (string) ($f['estado_seguimiento'] ?? 'pendiente'),
            'author' => (string) ($f['nombre_usuario'] ?? ''),
            'closed_by' => (string) ($f['nombre_usuario_cierre'] ?? ''),
            'created_at' => self::iso($f['fecha_creacion'] ?? null),
            // El contacto solo viaja en la lista global (el rail necesita
            // nombrar al cliente sin abrir el chat).
            'contact' => isset($f['wa_id'])
                ? ['wa_id' => (string) $f['wa_id'], 'name' => (string) ($f['nombre_contact'] ?? '')]
                : null,
        ];
    }

    public static function mapConversationList(array $c): array
    {
        return [
            'id' => (int) $c['id_conversation'],
            'status' => $c['status_conversation'],
            'mode' => $c['mode_conversation'],
            'bot_active' => (bool) $c['bot_active'],
            'human_support' => (bool) $c['human_support'],
            'last_message_at' => self::iso($c['last_message_at']),
            // Lead nuevo: el panel lo sube en la lista, lo marca y hace sonar el
            // aviso. `created_at` va aparte porque el "nuevo" caduca (LEAD_NUEVO_MIN)
            // y el panel necesita el dato crudo para no depender solo del flag.
            'created_at' => self::iso($c['fecha_creacion'] ?? null),
            'is_new' => (bool) ($c['es_nuevo'] ?? false),
            // Venta cerrada por el agente: el panel lo pinta en verde.
            'sale' => isset($c['sale']) && $c['sale'] !== null
                ? json_decode((string) $c['sale'], true)
                : null,
            // Quién tiene el chat. Sin esto, dos asesores pueden estar
            // atendiendo al mismo cliente sin enterarse.
            'assigned' => ($c['id_usuario_asignado'] ?? null) !== null
                ? [
                    'id' => (int) $c['id_usuario_asignado'],
                    'name' => (string) ($c['nombre_usuario_asignado'] ?? ''),
                ]
                : null,
            // Ventana de 24h de WhatsApp: el panel avisa ANTES de que el asesor
            // escriba un mensaje que Meta va a rechazar.
            'window' => self::serviceWindow($c['last_inbound_at'] ?? null),
            'next_followup_at' => self::iso($c['next_followup_at'] ?? null),
            'contact' => [
                'wa_id' => $c['wa_id'],
                'name' => $c['nombre_contact'],
            ],
            'last_message' => substr((string) ($c['last_message_preview'] ?? ''), 0, 120),
        ];
    }
}
