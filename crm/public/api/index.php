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
// o subcarpeta /crm/public/api/...
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
            'service' => 'crm',
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
    if ($method === 'PATCH' && preg_match('#^/conversations/\d+/sale/delivered$#', $path)) {
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
        // El cliente respondió a un mensaje: WhatsApp solo manda el id del citado.
        // El texto lo tiene el CRM, así que lo resolvemos aquí y lo devolvemos:
        // el agente lo necesita para saber a qué producto se refiere el "quiero este".
        $quotedText = $body['quoted_text'] ?? null;
        // Y su FOTO: el cliente suele responder a una imagen ("podría optar por
        // esta opción?"), y el texto de una imagen es solo el marcador "[image]".
        $quotedMediaUrl = null;
        if (!empty($body['quoted_wa_id'])) {
            $citado = Repository::findQuotedByWaId((string) $body['quoted_wa_id']);
            if ($citado !== null) {
                $quotedText = $quotedText ?? $citado['text'];
                $quotedMediaUrl = $citado['media_url'];
            }
        }
        // De qué anuncio viene el lead. Llega SOLO en el primer mensaje: si no
        // se guarda ahora, el asesor abre "¡Hola! Quiero más información." sin
        // saber que lo trajo un anuncio de desayunos.
        if (!empty($body['referral']) && is_array($body['referral'])) {
            Repository::setConversationAd($ids['conversationId'], $body['referral']);
        }
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
                'quotedText' => $quotedText,
                'quotedMediaUrl' => $quotedMediaUrl,
            ]);
        }
        $conv = Repository::getConversation($ids['conversationId']);
        Http::jsonOk([
            'ok' => true,
            'tenant_id' => $ids['tenantId'],
            'contact_id' => $ids['contactId'],
            'conversation_id' => $ids['conversationId'],
            'message_id' => $messageId,
            'quoted_text' => $quotedText,
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
                    // El agente cerró la venta: el panel pinta el chat en verde y
                    // muestra el pedido para que el vendedor no lo reconstruya.
                    'sale' => isset($conv['sale']) && $conv['sale'] !== null
                        ? json_decode((string) $conv['sale'], true)
                        : null,
                    // De qué anuncio vino el lead. Sin esto el asesor abre un
                    // "¡Hola! Quiero más información." sin saber qué lo provocó.
                    'ad' => !empty($conv['ad_source_id']) ? [
                        'source_type' => $conv['ad_source_type'],
                        'source_id' => $conv['ad_source_id'],
                        'headline' => $conv['ad_headline'],
                        'body' => $conv['ad_body'],
                        'url' => $conv['ad_source_url'],
                    ] : null,
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
                        // La foto citada, resuelta igual que la del mensaje:
                        // clave de storage o URL absoluta del catálogo.
                        'quoted_media_url' => $msg['quoted_media_url'],
                        'quoted_media_external' => !empty($msg['quoted_media_url'])
                            && preg_match('#^https?://#i', (string) $msg['quoted_media_url']) === 1,
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
            $content = (string) ($body['content'] ?? '');
            $mediaUrl = $body['media_url'] ?? null;
            // Una foto/audio/doc puede ir sin pie de texto; el marcador lo usa el inbox.
            if (trim($content) === '' && empty($mediaUrl)) {
                Http::jsonError('content or media_url required');
            }
            if (trim($content) === '' && !empty($mediaUrl)) {
                $content = '[media]';
            }
            $messageId = Repository::addMessage([
                'conversationId' => $id,
                'direction' => $body['direction'] ?? 'outbound',
                'senderType' => $body['sender_type'] ?? 'bot',
                'role' => $body['role'] ?? 'assistant',
                'content' => $content,
                'waMessageId' => $body['wa_message_id'] ?? null,
                'mediaUrl' => $mediaUrl,
                // Si el asesor respondió citando, el hilo debe mostrar la cita.
                'quotedText' => $body['quoted_text'] ?? null,
                'quotedMediaUrl' => $body['quoted_media_url'] ?? null,
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
        if (array_key_exists('keep_human', $body)) {
            Repository::setSetting('keep_human_' . $id, !empty($body['keep_human']) ? '1' : '0');
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

    // PATCH /conversations/{id}/sale/delivered — acción manual del asesor.
    if (preg_match('#^/conversations/(\d+)/sale/delivered$#', $path, $m) && $method === 'PATCH') {
        $user = Auth::user();
        if (!$user) {
            Http::jsonError('Unauthorized', 401);
        }
        try {
            $sale = Repository::markSaleDelivered((int) $m[1], (int) $user['id']);
        } catch (RuntimeException $error) {
            Http::jsonError($error->getMessage(), 404);
        }
        Http::jsonOk(['ok' => true, 'sale' => $sale]);
    }

    // PATCH /sales/{id}/status — cambio de estado desde el Historial de Ventas.
    // Va por id de venta (no por conversación, como /sale/delivered): en el
    // historial el vendedor tiene delante una fila concreta, y una conversación
    // puede tener varias ventas.
    if (preg_match('#^/sales/(\d+)/status$#', $path, $m) && $method === 'PATCH') {
        $user = Auth::user();
        if (!$user) {
            Http::jsonError('Unauthorized', 401);
        }
        $body = Http::readJson();
        $status = (string) ($body['status'] ?? '');
        if (!in_array($status, Repository::SALE_STATUSES, true)) {
            Http::jsonError('Invalid status');
        }
        try {
            $sale = Repository::setSaleStatus((int) $m[1], $status, (int) $user['id']);
        } catch (RuntimeException $error) {
            Http::jsonError($error->getMessage(), 404);
        }
        Http::jsonOk(['ok' => true, 'sale' => $sale]);
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
            $key = (string) $key;
            if (preg_match('/^sale_(\d+)$/', $key, $saleMatch)) {
                $sale = json_decode($stored, true);
                if (
                    !is_array($sale) ||
                    empty($sale['producto']) ||
                    empty($sale['cerrada_en'])
                ) {
                    Http::jsonError('Invalid sale payload', 422);
                }
                Repository::storeActiveSale((int) $saleMatch[1], $sale);
                continue;
            }
            Repository::setSetting($key, $stored);
        }
        Http::jsonOk(['ok' => true]);
    }

    // POST /media — sube un archivo y devuelve su clave de almacenamiento.
    if ($path === '/media' && $method === 'POST') {
        // Cuando el archivo pasa del límite de PHP (`upload_max_filesize` /
        // `post_max_size`), $_FILES llega vacío o con código de error y esto
        // respondía "file required" — que hace pensar en un bug del panel cuando
        // en realidad es el hosting rechazando el archivo. El asesor veía "no se
        // envió" sin ninguna pista de por qué.
        $subida = $_FILES['file'] ?? null;
        $codigo = is_array($subida) ? (int) ($subida['error'] ?? UPLOAD_ERR_NO_FILE) : UPLOAD_ERR_NO_FILE;
        if (!is_array($subida) || $codigo !== UPLOAD_ERR_OK) {
            $limite = ini_get('upload_max_filesize') ?: '?';
            $post = ini_get('post_max_size') ?: '?';
            $motivos = [
                UPLOAD_ERR_INI_SIZE => "El archivo supera el máximo del servidor (upload_max_filesize = {$limite}).",
                UPLOAD_ERR_FORM_SIZE => 'El archivo supera el máximo del formulario.',
                UPLOAD_ERR_PARTIAL => 'La subida se cortó a medias. Reintenta.',
                UPLOAD_ERR_NO_FILE => 'No llegó ningún archivo.',
                UPLOAD_ERR_NO_TMP_DIR => 'El servidor no tiene carpeta temporal para subidas.',
                UPLOAD_ERR_CANT_WRITE => 'El servidor no pudo escribir el archivo.',
                UPLOAD_ERR_EXTENSION => 'Una extensión de PHP bloqueó la subida.',
            ];
            // Sin $_FILES y con CONTENT_LENGTH grande, fue post_max_size: PHP
            // descarta TODO el POST antes de poblar $_FILES.
            $enviado = (int) ($_SERVER['CONTENT_LENGTH'] ?? 0);
            if (!is_array($subida) && $enviado > 0) {
                Http::jsonError(
                    "El envío pesa " . round($enviado / 1048576, 1) . " MB y el servidor "
                    . "acepta hasta {$post} por POST (post_max_size).",
                    413
                );
            }
            Http::jsonError($motivos[$codigo] ?? 'No se pudo subir el archivo.', 413);
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
    // Reclamar una fila antes de mandarla a WhatsApp. `claimed: false` significa
    // que otro camino (el push o el drenaje) ya la tiene: quien recibe eso NO
    // debe enviar nada. Es lo que impide que el mismo mensaje del asesor salga
    // dos y tres veces.
    if ($path === '/outbox/claim' && $method === 'POST') {
        Auth::assertInternalToken();
        $body = Http::readJson();
        if (empty($body['outbox_id'])) {
            Http::jsonError('outbox_id required');
        }
        $claimed = Repository::claimOutbox((int) $body['outbox_id']);
        Http::jsonOk(['ok' => true, 'claimed' => $claimed]);
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
        // El asesor respondió a un mensaje desde el inbox: la cita viaja hasta la
        // Cloud API para que el cliente la vea en su WhatsApp, y se guarda en el
        // hilo del CRM para que el resto del equipo sepa a qué se respondía.
        $replyToWaId = trim((string) ($body['reply_to_wa_id'] ?? '')) ?: null;
        $replyCitado = $replyToWaId !== null
            ? Repository::findQuotedByWaId($replyToWaId)
            : null;
        $replyQuoted = $replyCitado['text'] ?? null;
        // El asesor también cita fotos (las suyas o las del cliente).
        $replyQuotedMedia = $replyCitado['media_url'] ?? null;

        $outboxId = Repository::enqueueOutbox([
            'conversationId' => $convId,
            'waId' => $conv['wa_id'],
            'content' => $content,
            'type' => $type,
            'mediaPath' => $mediaPath !== '' ? $mediaPath : null,
            'replyToWaId' => $replyToWaId,
        ]);
        // Marca actividad del asesor para el auto-releaser HUMAN→AI del agente.
        Repository::setSetting('last_human_outbound_' . $convId, (string) time());

        $agentUrl = rtrim((string) ($config['agent_base_url'] ?? ''), '/');
        $agentToken = (string) ($config['agent_internal_token'] ?? '');
        $urlLooksFake = $agentUrl === ''
            || strpos($agentUrl, 'XXXX') !== false
            || strpos($agentUrl, 'example') !== false
            || strpos($agentUrl, 'cambia-') !== false;

        // Sin URL válida: dejamos pending para que el agente lo drene; avisamos al panel.
        if ($urlLooksFake) {
            Http::jsonOk([
                'ok' => true,
                'outbox_id' => $outboxId,
                'queued' => true,
                'pushed' => false,
                'warning' =>
                    'agent_base_url vacío o de ejemplo en config.php. '
                    . 'El mensaje quedó en cola; configura la URL pública del agente en EasyPanel.',
            ]);
        }

        $payload = json_encode([
            'outbox_id' => $outboxId,
            'wa_id' => $conv['wa_id'],
            'content' => $content,
            'conversation_id' => $convId,
            'type' => $type,
            'media_path' => $mediaPath !== '' ? $mediaPath : null,
            'filename' => (string) ($body['filename'] ?? ''),
            'reply_to_wa_id' => $replyToWaId,
            'quoted_text' => $replyQuoted,
            'quoted_media_url' => $replyQuotedMedia,
        ], JSON_UNESCAPED_UNICODE);

        $ch = curl_init($agentUrl . '/internal/outbox/send');
        if ($ch === false) {
            // Pending: el drenaje del agente puede recuperarlo.
            Http::jsonOk([
                'ok' => true,
                'outbox_id' => $outboxId,
                'queued' => true,
                'pushed' => false,
                'warning' => 'No se pudo iniciar curl hacia el agente; mensaje en cola.',
            ]);
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
            CURLOPT_TIMEOUT => $type === 'text' ? 25 : 60,
        ]);
        $resBody = (string) curl_exec($ch);
        $curlErr = curl_error($ch);
        $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        // El agente ya: envió a WA, marcó el outbox, guardó el mensaje y puso modo HUMAN.
        // No insertar el mensaje aquí (evita duplicados en el inbox).
        if ($code < 200 || $code >= 300) {
            // Dejamos pending (no failed) para que el drenaje del agente reintente.
            $detail = $curlErr !== '' ? $curlErr : (substr($resBody, 0, 300) ?: "HTTP {$code}");
            Http::jsonOk([
                'ok' => true,
                'outbox_id' => $outboxId,
                'queued' => true,
                'pushed' => false,
                'warning' =>
                    'El agente no respondió bien al push (' . $detail . '). '
                    . 'Mensaje en cola; si en ~30s no llega a WhatsApp, revisa agent_base_url y AGENT_INTERNAL_TOKEN.',
            ]);
        }

        Http::jsonOk(['ok' => true, 'outbox_id' => $outboxId, 'queued' => false, 'pushed' => true]);
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
