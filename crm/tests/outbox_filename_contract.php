<?php

declare(strict_types=1);

/**
 * El nombre del adjunto se GUARDA, no solo se empuja.
 *
 * Un asesor adjuntó «catalogodedesayunos.pdf» y el panel dijo "No se envió".
 * El nombre viajaba solo en el payload del push CRM→agente y nunca llegaba a
 * `crm_outbox`. Mientras el push funciona no se nota; el problema es qué pasa
 * cuando NO funciona, que es justo cuando importa: la fila se queda en
 * 'pending' para que el drenaje del agente la recoja, y en la fila el nombre ya
 * no existe. El PDF salía llamado "documento", sin extensión — un archivo que
 * varios clientes de WhatsApp no abren, con el asesor creyendo que salió bien.
 *
 * La cadena cruza las tres capas (SQL → PHP → agente) y este contrato
 * comprueba que no falte ningún tramo. El del agente vive en
 * `tests/test_outbox_nombre_de_archivo.py`.
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

// 1. La columna donde vive el nombre. Sin ella el resto no tiene dónde escribir.
$sql = source('sql/015_outbox_filename.sql');
requiresText($sql, 'ALTER TABLE crm_outbox', 'La migración no toca crm_outbox');
requiresText($sql, 'filename_outbox', 'Falta la columna del nombre del adjunto');

// 2. El INSERT la rellena. Era exactamente lo que faltaba: el push ya mandaba
//    el nombre, pero nadie lo persistía.
$repo = source('src/Repository.php');
requiresText($repo, 'filename_outbox', 'enqueueOutbox no guarda el nombre');
requiresText($repo, ':filename', 'El INSERT del outbox no vincula el nombre');

// 3. El endpoint lo lee del cuerpo y lo pasa a las DOS vías: la fila (para el
//    drenaje) y el payload del push (para el camino rápido). Si solo fuera al
//    push volveríamos al bug.
$api = source('public/api/index.php');
requiresText($api, "\$filename = trim((string) (\$body['filename'] ?? ''))", 'El endpoint no lee el nombre');
requiresText($api, "'filename' => \$filename", 'El nombre no llega a enqueueOutbox ni al push');

// 4. El panel lo manda al encolar el adjunto.
$js = source('public/assets/inbox.js');
requiresText($js, 'filename: file.name', 'El inbox no envía el nombre del archivo');

echo "outbox filename contract: OK\n";
