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

    public static function listConversations(int $limit = 80): array
    {
        $tenantId = self::ensureTenantId();
        $limit = max(1, min(200, $limit));
        // `sale`: el agente cerró la venta con todos los datos del pedido. El panel
        // pinta ese chat en verde para que el vendedor entre directo a cobrarlo.
        return Database::fetchAll(
            "SELECT c.id_conversation, c.status_conversation, c.mode_conversation,
                    c.bot_active, c.human_support, c.last_message_at,
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
                      COALESCE(c.last_message_at, c.fecha_creacion) DESC
             LIMIT {$limit}",
            ['tenantId' => $tenantId]
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

    public static function addMessage(array $input): int
    {
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

    public static function listPendingOutbox(int $limit = 30): array
    {
        $limit = max(1, min(100, $limit));
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
