<?php

declare(strict_types=1);

/**
 * Front controller API del agente.
 * Rutas compatibles con sandbox/app/crm/http_client.py
 */

$config = require dirname(__DIR__, 2) . '/bootstrap.php';
Http::cors();

$method = Http::method();
$uri = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$uri = preg_replace('#/+#', '/', $uri);

// Soporta document root = public/ → /api/...
// o subcarpeta /crm-php/public/api/...
$path = $uri;
if (preg_match('#/api(/.*)?$#', $uri, $m)) {
    $path = $m[1] ?? '/';
}
$path = rtrim($path, '/') ?: '/';

try {
    // Health público (sin token) — útil para smoke test
    if ($path === '/health' && $method === 'GET') {
        Http::jsonOk([
            'ok' => true,
            'service' => 'crm-php',
            'tenant' => $config['tenant_slug'] ?? 'don-regalo',
        ]);
    }

    // Rutas de agente: token interno
    // Panel JSON (sesión): conversations GET, outbox POST, mode PATCH también vía session
    $needsToken = true;
    $sessionOk = Auth::user() !== null;

    if ($method === 'GET' && ($path === '/conversations' || preg_match('#^/conversations/\d+$#', $path))) {
        $needsToken = false; // sesión o token
    }
    if ($method === 'POST' && $path === '/outbox') {
        $needsToken = false;
    }
    // Subida de medios: el asesor desde el panel (sesión) o el agente (token).
    if ($method === 'POST' && $path === '/media') {
        $needsToken = false;
    }
    if ($method === 'PATCH' && preg_match('#^/conversations/\d+/mode$#', $path)) {
        $needsToken = false;
    }
    if ($method === 'GET' && strpos($path, '/reports') === 0) {
        $needsToken = false;
    }

    if ($needsToken) {
        Auth::assertInternalToken();
    } elseif (!$sessionOk && !Auth::hasValidInternalToken()) {
        Http::jsonError('Unauthorized', 401);
    }

    // GET /conversations
    if ($path === '/conversations' && $method === 'GET') {
        $rows = Repository::listConversations(80);
        $tenantId = Repository::ensureTenantId();
        $totalTenant = count($rows);
        // Si la lista viene vacía, distinguir "no hay chats" vs "tenant equivocado".
        $totalAll = 0;
        try {
            $totalAll = (int) (Database::fetchOne(
                'SELECT COUNT(*) AS n FROM crm_conversations'
            )['n'] ?? 0);
        } catch (Throwable $e) {
            $totalAll = $totalTenant;
        }
        Http::jsonOk([
            'data' => array_map([Repository::class, 'mapConversationList'], $rows),
            'meta' => [
                'tenant_id' => $tenantId,
                'tenant_slug' => Auth::config()['tenant_slug'] ?? 'don-regalo',
                'count' => $totalTenant,
                'count_all_tenants' => $totalAll,
            ],
        ]);
    }

    // POST /conversations — inbound agente
    if ($path === '/conversations' && $method === 'POST') {
        Auth::assertInternalToken();
        $body = Http::readJson();
        if (empty($body['wa_id'])) {
            Http::jsonError('wa_id required');
        }
        $ids = Repository::ensureInboundConversation(
            (string) $body['wa_id'],
            (string) ($body['name'] ?? '')
        );
        $messageId = null;
        if (!empty($body['content'])) {
            $messageId = Repository::addMessage([
                'conversationId' => $ids['conversationId'],
                'direction' => $body['direction'] ?? 'inbound',
                'senderType' => $body['sender_type'] ?? 'contact',
                'role' => $body['role'] ?? 'user',
                'content' => (string) $body['content'],
                'waMessageId' => $body['wa_message_id'] ?? null,
                'mediaUrl' => $body['media_url'] ?? null,
                'quotedText' => $body['quoted_text'] ?? null,
            ]);
        }
        $conv = Repository::getConversation($ids['conversationId']);
        Http::jsonOk([
            'ok' => true,
            'tenant_id' => $ids['tenantId'],
            'contact_id' => $ids['contactId'],
            'conversation_id' => $ids['conversationId'],
            'message_id' => $messageId,
            'conversation' => $conv ? [
                'id' => (int) $conv['id_conversation'],
                'mode' => $conv['mode_conversation'],
                'bot_active' => (bool) $conv['bot_active'],
                'human_support' => (bool) $conv['human_support'],
            ] : null,
        ]);
    }

    // GET|POST /conversations/{id}
    if (preg_match('#^/conversations/(\d+)$#', $path, $m)) {
        $id = (int) $m[1];
        if ($method === 'GET') {
            $conv = Repository::getConversation($id);
            if (!$conv) {
                Http::jsonError('Conversation not found', 404);
            }
            $messages = Repository::getMessages($id);
            $memory = Repository::getMemory((string) $conv['wa_id']);
            Http::jsonOk([
                'conversation' => [
                    'id' => (int) $conv['id_conversation'],
                    'status' => $conv['status_conversation'],
                    'mode' => $conv['mode_conversation'],
                    'bot_active' => (bool) $conv['bot_active'],
                    'human_support' => (bool) $conv['human_support'],
                    'last_message_at' => Repository::iso($conv['last_message_at']),
                    'contact' => [
                        'wa_id' => $conv['wa_id'],
                        'name' => $conv['nombre_contact'],
                    ],
                ],
                // Alimenta el panel "Resumen del lead" del inbox.
                'lead' => $memory ? [
                    'nombre' => $memory['nombre_memory'] ?? null,
                    'email' => $memory['email_memory'] ?? null,
                    'objetivo' => $memory['objetivo_memory'] ?? null,
                    'situacion' => $memory['situacion_memory'] ?? null,
                    'temperatura' => $memory['temperatura_memory'] ?? null,
                    'resumen' => $memory['resumen_memory'] ?? null,
                ] : null,
                'messages' => array_map(static function (array $msg): array {
                    // media_url puede ser una clave de storage (medio de la conversación)
                    // o una URL absoluta (imagen de catálogo que envía el bot).
                    $media = (string) ($msg['media_url'] ?? '');
                    $isExternal = $media !== '' && preg_match('#^https?://#i', $media);
                    $kind = null;
                    if ($media !== '') {
                        $kind = $isExternal ? 'image' : Media::kindFor($media);
                    }

                    return [
                        'id' => (int) $msg['id_message'],
                        'direction' => $msg['direction_message'],
                        'sender_type' => $msg['sender_type'],
                        'role' => $msg['role_message'],
                        'content' => $msg['content_message'],
                        'media_url' => $msg['media_url'],
                        'media_kind' => $kind,
                        'media_external' => (bool) $isExternal,
                        'quoted_text' => $msg['quoted_text'],
                        'wa_message_id' => $msg['wa_message_id'],
                        'created_at' => Repository::iso($msg['fecha_creacion']),
                    ];
                }, $messages),
            ]);
        }
        if ($method === 'POST') {
            Auth::assertInternalToken();
            $conv = Repository::getConversation($id);
            if (!$conv) {
                Http::jsonError('Conversation not found', 404);
            }
            $body = Http::readJson();
            if (empty($body['content'])) {
                Http::jsonError('content required');
            }
            $messageId = Repository::addMessage([
                'conversationId' => $id,
                'direction' => $body['direction'] ?? 'outbound',
                'senderType' => $body['sender_type'] ?? 'bot',
                'role' => $body['role'] ?? 'assistant',
                'content' => (string) $body['content'],
                'waMessageId' => $body['wa_message_id'] ?? null,
                'mediaUrl' => $body['media_url'] ?? null,
            ]);
            Http::jsonOk(['ok' => true, 'message_id' => $messageId]);
        }
    }

    // PATCH /conversations/{id}/mode
    if (preg_match('#^/conversations/(\d+)/mode$#', $path, $m) && $method === 'PATCH') {
        $id = (int) $m[1];
        $body = Http::readJson();
        if (($body['mode'] ?? '') === 'AI' || ($body['mode'] ?? '') === 'HUMAN') {
            Repository::setMode($id, (string) $body['mode']);
        }
        if (array_key_exists('bot_active', $body)) {
            Repository::setBotActive($id, (bool) $body['bot_active']);
        }
        if (array_key_exists('human_support', $body)) {
            Repository::setHumanSupport($id, (bool) $body['human_support']);
        }
        $conv = Repository::getConversation($id);
        if (!$conv) {
            Http::jsonError('Conversation not found', 404);
        }
        Http::jsonOk([
            'ok' => true,
            'conversation' => [
                'id' => (int) $conv['id_conversation'],
                'mode' => $conv['mode_conversation'],
                'bot_active' => (bool) $conv['bot_active'],
                'human_support' => (bool) $conv['human_support'],
            ],
        ]);
    }

    // Memory
    if (preg_match('#^/memory/([^/]+)$#', $path, $m)) {
        $waId = preg_replace('/\D/', '', $m[1]) ?: $m[1];
        if ($method === 'GET') {
            Http::jsonOk(['ok' => true, 'memory' => Repository::getMemory($waId)]);
        }
        if ($method === 'PUT') {
            Auth::assertInternalToken();
            Repository::upsertMemory($waId, Http::readJson());
            Http::jsonOk(['ok' => true, 'memory' => Repository::getMemory($waId)]);
        }
    }

    // Leads
    if ($path === '/leads' && $method === 'GET') {
        $phone = preg_replace('/\D/', '', (string) ($_GET['phone'] ?? '')) ?: '';
        if ($phone === '') {
            Http::jsonError('phone required');
        }
        Http::jsonOk(['ok' => true, 'lead' => Repository::getLeadByPhone($phone)]);
    }
    if ($path === '/leads' && $method === 'POST') {
        Auth::assertInternalToken();
        $body = Http::readJson();
        if (empty($body['wa_id'])) {
            Http::jsonError('wa_id required');
        }
        Repository::upsertLead([
            'waId' => (string) $body['wa_id'],
            'name' => $body['name'] ?? null,
            'email' => $body['email'] ?? null,
            'notes' => $body['notes'] ?? null,
            'temperatura' => $body['temperatura'] ?? null,
        ]);
        Http::jsonOk(['ok' => true]);
    }

    // Settings
    if ($path === '/settings' && $method === 'GET') {
        $key = $_GET['key'] ?? null;
        if ($key) {
            Http::jsonOk(['ok' => true, 'key' => $key, 'value' => Repository::getSetting((string) $key)]);
        }
        $paused = Repository::getSetting('paused');
        Http::jsonOk(['ok' => true, 'settings' => ['paused' => $paused === '1']]);
    }
    if ($path === '/settings' && $method === 'PUT') {
        Auth::assertInternalToken();
        $body = Http::readJson();
        foreach ($body as $key => $value) {
            $stored = is_bool($value) ? ($value ? '1' : '0') : (string) $value;
            Repository::setSetting((string) $key, $stored);
        }
        Http::jsonOk(['ok' => true]);
    }

    // POST /media — sube un archivo y devuelve su clave de almacenamiento.
    if ($path === '/media' && $method === 'POST') {
        if (empty($_FILES['file'])) {
            Http::jsonError('file required');
        }
        try {
            $key = Media::storeUploaded($_FILES['file']);
        } catch (RuntimeException $e) {
            Http::jsonError($e->getMessage(), 422);
        }
        Http::jsonOk([
            'ok' => true,
            'key' => $key,
            'kind' => Media::kindFor($key),
            'mime' => Media::mimeFor($key),
            'name' => (string) ($_FILES['file']['name'] ?? ''),
        ]);
    }

    // Outbox
    if ($path === '/outbox' && $method === 'GET') {
        Auth::assertInternalToken();
        Http::jsonOk(['ok' => true, 'data' => Repository::listPendingOutbox(30)]);
    }
    if ($path === '/outbox' && $method === 'POST') {
        $body = Http::readJson();
        if (empty($body['conversation_id'])) {
            Http::jsonError('conversation_id required');
        }

        $mediaPath = (string) ($body['media_path'] ?? '');
        $content = (string) ($body['content'] ?? '');
        // Un adjunto puede ir sin texto (una foto sola), pero un mensaje de texto no puede ir vacío.
        if ($mediaPath === '' && trim($content) === '') {
            Http::jsonError('content or media_path required');
        }
        if ($mediaPath !== '' && Media::pathFor($mediaPath) === null) {
            Http::jsonError('media_path not found', 404);
        }

        $type = $mediaPath !== '' ? Media::kindFor($mediaPath) : 'text';

        $convId = (int) $body['conversation_id'];
        $conv = Repository::getConversation($convId);
        if (!$conv) {
            Http::jsonError('Conversation not found', 404);
        }
        $outboxId = Repository::enqueueOutbox([
            'conversationId' => $convId,
            'waId' => $conv['wa_id'],
            'content' => $content,
            'type' => $type,
            'mediaPath' => $mediaPath !== '' ? $mediaPath : null,
        ]);

        $agentUrl = rtrim((string) ($config['agent_base_url'] ?? ''), '/');
        $agentToken = (string) ($config['agent_internal_token'] ?? '');
        if ($agentUrl === '') {
            Http::jsonOk(['ok' => true, 'outbox_id' => $outboxId, 'queued' => true]);
        }

        $payload = json_encode([
            'outbox_id' => $outboxId,
            'wa_id' => $conv['wa_id'],
            'content' => $content,
            'conversation_id' => $convId,
            'type' => $type,
            'media_path' => $mediaPath !== '' ? $mediaPath : null,
            'filename' => (string) ($body['filename'] ?? ''),
        ], JSON_UNESCAPED_UNICODE);

        $ch = curl_init($agentUrl . '/internal/outbox/send');
        if ($ch === false) {
            Repository::markOutbox($outboxId, 'failed', 'curl_init failed');
            Http::jsonError('No se pudo contactar al agente', 502);
        }
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_HTTPHEADER => array_filter([
                'Content-Type: application/json',
                $agentToken !== '' ? 'X-Agent-Token: ' . $agentToken : null,
            ]),
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_RETURNTRANSFER => true,
            // Los medios tardan más: el agente descarga, convierte y sube a Meta.
            CURLOPT_TIMEOUT => $type === 'text' ? 20 : 60,
        ]);
        $resBody = (string) curl_exec($ch);
        $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        // El agente ya: envió a WA, marcó el outbox, guardó el mensaje y puso modo HUMAN.
        // No insertar el mensaje aquí (evita duplicados en el inbox).
        if ($code < 200 || $code >= 300) {
            Repository::markOutbox($outboxId, 'failed', substr($resBody, 0, 500) ?: "HTTP {$code}");
            // Antes fallaba en silencio y el asesor creía que se había enviado.
            Http::jsonError('El agente no pudo enviar el mensaje', 502);
        }

        Http::jsonOk(['ok' => true, 'outbox_id' => $outboxId]);
    }
    if ($path === '/outbox' && $method === 'PATCH') {
        Auth::assertInternalToken();
        $body = Http::readJson();
        if (empty($body['outbox_id']) || empty($body['status'])) {
            Http::jsonError('outbox_id and status required');
        }
        Repository::markOutbox((int) $body['outbox_id'], (string) $body['status'], $body['error'] ?? null);
        Http::jsonOk(['ok' => true]);
    }

    // Watchdog
    if ($path === '/watchdog/unanswered' && $method === 'GET') {
        Auth::assertInternalToken();
        $minSec = (int) ($_GET['min_sec'] ?? 180);
        $maxSec = (int) ($_GET['max_sec'] ?? 7200);
        $rows = Repository::getUnansweredConversations($minSec, $maxSec);
        Http::jsonOk([
            'ok' => true,
            'data' => array_map(static function (array $r): array {
                return [
                    'id' => (int) $r['id_conversation'],
                    'phone' => $r['phone'],
                    'name' => $r['name'],
                    'last_role' => $r['last_role'],
                    'last_at' => $r['last_at'],
                ];
            }, $rows),
        ]);
    }

    // Reports (sesión o token)
    if ($path === '/reports/overview' && $method === 'GET') {
        Http::jsonOk([
            'ok' => true,
            'data' => Repository::reportsOverview(
                isset($_GET['from']) ? (string) $_GET['from'] : null,
                isset($_GET['to']) ? (string) $_GET['to'] : null
            ),
        ]);
    }
    if ($path === '/reports/conversations' && $method === 'GET') {
        Http::jsonOk([
            'ok' => true,
            'data' => Repository::reportsConversations(
                isset($_GET['from']) ? (string) $_GET['from'] : null,
                isset($_GET['to']) ? (string) $_GET['to'] : null
            ),
        ]);
    }

    Http::jsonError('Not found', 404);
} catch (Throwable $e) {
    Http::jsonError($e->getMessage(), 500);
}
