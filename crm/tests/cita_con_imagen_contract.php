<?php

declare(strict_types=1);

/**
 * La cita de una imagen muestra la imagen, no el literal "[image]".
 *
 * El asesor manda una foto, el cliente responde CITÁNDOLA ("podría optar por
 * esta opción?") y el panel mostraba `[image]`: justo en el turno en que el
 * lead elige, el vendedor era el único que no veía qué había elegido.
 *
 * La cadena va del webhook al pixel y cruza los dos sentidos (el cliente cita,
 * y el asesor cita). Esto comprueba que no falte ningún tramo.
 */

function source(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function requiresText(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

// 1. La columna donde vive la foto citada.
$sql = source('sql/008_cita_con_imagen.sql');
requiresText($sql, 'quoted_media_url', 'Falta la columna de la foto citada');

// 2. El resolvedor devuelve texto Y medio, y lo persiste.
$repo = source('src/Repository.php');
requiresText($repo, 'function findQuotedByWaId', 'Falta el resolvedor de la cita');
requiresText($repo, "'SELECT content_message, media_url FROM crm_messages", 'El resolvedor no trae el medio');
requiresText($repo, ':quotedMediaUrl', 'addMessage no guarda la foto citada');
requiresText($repo, 'quoted_text, quoted_media_url, fecha_creacion', 'getMessages no devuelve la foto citada');

// 3. Los DOS sentidos: el cliente citando y el asesor citando.
$api = source('public/api/index.php');
requiresText($api, "\$quotedMediaUrl = \$citado['media_url']", 'El cliente citando no guarda la foto');
requiresText($api, "\$replyQuotedMedia = \$replyCitado['media_url']", 'El asesor citando no resuelve la foto');
requiresText($api, "'quoted_media_url' => \$replyQuotedMedia", 'La cita del asesor no viaja al agente');
requiresText($api, "'quotedMediaUrl' => \$body['quoted_media_url']", 'El saliente no persiste la foto citada');
requiresText($api, "'quoted_media_url' => \$msg['quoted_media_url']", 'El panel no recibe la foto citada');

// 4. El pintado.
$js = source('public/assets/inbox.js');
requiresText($js, 'quoted_media_url', 'quotedMarkup no mira la foto citada');
requiresText($js, 'quoted-thumb', 'Falta la miniatura en la cita');
requiresText(
    $js,
    '!isPlaceholder(clean)',
    'Con miniatura, "[image]" sobra: era justo el texto inútil que se veía'
);

$css = source('public/assets/app.css');
requiresText($css, '.bubble .quoted-thumb img', 'Falta el estilo de la miniatura');

echo "cita con imagen contract: OK\n";
