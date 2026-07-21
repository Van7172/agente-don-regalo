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
        // Un lead nuevo sube por encima del resto, pero NO por encima del dinero
        // por cobrar ni de quien lleva horas esperando asesor: la cola de ayuda ya
        // va fijada aparte, en las fichas de arriba. Sin esta banda el lead recién
        // llegado caía detrás de todos los `human_support` y el vendedor no lo veía.
        return Database::fetchAll(
            "SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
                    c.bot_active, c.human_support, c.last_message_at,
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
             ORDER BY (s.valor_setting IS NOT NULL) DESC,
                      c.human_support DESC,
                      es_nuevo DESC,
                      COALESCE(c.last_message_at, c.fecha_creacion) DESC
             LIMIT {$limit}",
            ['tenantId' => $tenantId, 'nuevoMin' => self::LEAD_NUEVO_MIN]
        );
    }

    public static function getConversation(int $id): ?array
    {
        return Database::fetchOne(
            'SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
                    c.bot_active, c.human_support, c.last_message_at,
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
                    wa_message_id, content_message, media_url, quoted_text, fecha_creacion
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
    public static function findMessageTextByWaId(string $waMessageId): ?string
    {
        if ($waMessageId === '') {
            return null;
        }
        $row = Database::fetchOne(
            'SELECT content_message FROM crm_messages
             WHERE wa_message_id = :waId
             ORDER BY id_message DESC LIMIT 1',
            ['waId' => $waMessageId]
        );
        if (!$row || $row['content_message'] === null) {
            return null;
        }
        return mb_substr((string) $row['content_message'], 0, 400);
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
               content_message, media_url, quoted_text, raw_message)
             VALUES
              (:conversationId, :direction, :senderType, :role, :waMessageId,
               :content, :mediaUrl, :quotedText, :raw)',
            [
                'conversationId' => $input['conversationId'],
                'direction' => $input['direction'],
                'senderType' => $input['senderType'],
                'role' => $input['role'],
                'waMessageId' => $input['waMessageId'] ?? null,
                'content' => $input['content'],
                'mediaUrl' => $input['mediaUrl'] ?? null,
                'quotedText' => $input['quotedText'] ?? null,
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

        Database::exec(
            'INSERT INTO crm_ventas_historiales (
               id_tenant, id_conversation, id_contact,
               wa_id_venta_historial, nombre_contacto_venta_historial,
               producto_venta_historial, distrito_venta_historial,
               envio_sol_venta_historial, fecha_entrega_venta_historial,
               horario_venta_historial, id_pedido_temporal,
               motivo_venta_historial, marca_cierre_venta_historial,
               fecha_cierre_venta_historial, estado_venta_historial,
               snapshot_venta_historial
             ) VALUES (
               :tenantId, :conversationId, :contactId,
               :waId, :contactName, :product, :district, :shipping,
               :deliveryDate, :schedule, :temporaryOrderId, :reason,
               :closedMark, FROM_UNIXTIME(:closedAt), \'pendiente\', :snapshot
             )
             ON DUPLICATE KEY UPDATE
               producto_venta_historial = VALUES(producto_venta_historial),
               distrito_venta_historial = VALUES(distrito_venta_historial),
               envio_sol_venta_historial = VALUES(envio_sol_venta_historial),
               fecha_entrega_venta_historial = VALUES(fecha_entrega_venta_historial),
               horario_venta_historial = VALUES(horario_venta_historial),
               id_pedido_temporal = VALUES(id_pedido_temporal),
               motivo_venta_historial = VALUES(motivo_venta_historial),
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

    public static function enqueueOutbox(array $input): int
    {
        return Database::execute(
            'INSERT INTO crm_outbox
              (id_conversation, wa_id, content_outbox, type_outbox, media_path, reply_to_wa_id, status_outbox)
             VALUES (:conversationId, :waId, :content, :type, :mediaPath, :replyToWaId, \'pending\')',
            [
                'conversationId' => $input['conversationId'],
                'waId' => $input['waId'],
                'content' => $input['content'],
                'type' => $input['type'] ?? 'text',
                'mediaPath' => $input['mediaPath'] ?? null,
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
            'contact' => [
                'wa_id' => $c['wa_id'],
                'name' => $c['nombre_contact'],
            ],
            'last_message' => substr((string) ($c['last_message_preview'] ?? ''), 0, 120),
        ];
    }
}
