<?php

declare(strict_types=1);

/**
 * Sirve un archivo de conversación.
 *
 * Acceso: sesión de asesor (el inbox lo pinta) o token interno (el agente lo
 * descarga para reenviarlo a WhatsApp). Sin una de las dos, 401.
 */

$config = require dirname(__DIR__) . '/bootstrap.php';

if (!Auth::user() && !Auth::hasValidInternalToken()) {
    http_response_code(401);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Unauthorized';
    exit;
}

$key = (string) ($_GET['f'] ?? '');
$path = Media::pathFor($key);

if ($path === null) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo 'Not found';
    exit;
}

$mime = Media::mimeFor($key);
$size = filesize($path);

header('Content-Type: ' . $mime);
if ($size !== false) {
    header('Content-Length: ' . $size);
}
// inline: el asesor ve la foto y escucha el audio dentro del inbox.
header('Content-Disposition: inline; filename="' . basename($key) . '"');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: private, max-age=86400');

readfile($path);
