<?php

declare(strict_types=1);

/**
 * Tomar un chat reinicia el reloj del releaser.
 *
 * Chat real (sep 2026): el asesor tomó dos conversaciones y el bot se las
 * quitó. El claim ponía mode=HUMAN y dueño, pero el ancla del releaser seguía
 * siendo el `handoff_at` viejo (a veces de hace horas). Al escribir el
 * cliente, `(now - handoff_at) >= 20 min` liberaba el chat a AI aunque el
 * asesor acabara de tomarlo.
 *
 * Al reclamar se marca `last_human_outbound_{id}` = ahora: el idle se mide
 * desde que alguien se hizo cargo, no desde la derivación anterior.
 */

function source(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

$api = source('public/api/index.php');
$inbox = source('public/assets/inbox.js');

if (strpos($api, 'claimConversation') === false) {
    throw new RuntimeException('El endpoint de claim desapareció');
}

// Tras un claim ganado hay que sellar actividad del asesor para el releaser.
if (strpos($api, "last_human_outbound_'") === false
    && strpos($api, 'last_human_outbound_') === false) {
    throw new RuntimeException(
        'Al tomar/reclamar un chat debe actualizarse last_human_outbound '
        . '(si no, el releaser usa el handoff_at viejo y el bot recupera el chat)'
    );
}

// La ubicación no puede quedar como marcador invisible.
if (strpos($inbox, 'locationMarkup') === false
    && strpos($inbox, 'media-location') === false) {
    throw new RuntimeException(
        'El hilo debe pintar ubicaciones (media-location / locationMarkup); '
        . 'antes [location] se ocultaba como placeholder y salía una burbuja vacía'
    );
}

echo "claim releaser anchor + location contract: OK\n";
