<?php

declare(strict_types=1);

/**
 * El panel no puede prometer más de lo que el servidor acepta.
 *
 * Un asesor adjuntó «catalogodedesayunos.pdf» (20 páginas de foto a sangre) y
 * el panel dijo "No se envió". En el log del agente no había NADA: el drenaje
 * encuestaba `GET /api/outbox` cada 12 s y no encontraba ninguna fila, o sea
 * que el envío murió en el CRM, antes de que el agente existiera para el caso.
 *
 * El guardia del navegador eran 16 MB fijos —copiados de `Media::MAX_BYTES`—
 * mientras PHP corta en `upload_max_filesize` / `post_max_size`, que en hosting
 * compartido suelen venir en 2M y 8M. El archivo pasaba el filtro del panel, se
 * subía entero, el servidor lo tiraba y el asesor veía la burbuja roja después
 * de esperar. Reintentar el mismo archivo no podía funcionar nunca.
 *
 * Dos mitades, y hacen falta las dos: subir el techo donde el hosting deje, y
 * decir la verdad cuando no deje.
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

function assertTrue(bool $ok, string $message): void
{
    if (!$ok) {
        throw new RuntimeException($message);
    }
}

require_once dirname(__DIR__) . '/src/Media.php';

// 1. El límite efectivo cruza NUESTRO tope con los de PHP, y manda el menor.
$efectivo = Media::effectiveMaxBytes();
assertTrue($efectivo > 0, 'El límite efectivo no puede ser cero: bloquearía toda subida');
assertTrue(
    $efectivo <= Media::MAX_BYTES,
    'El límite efectivo no puede pasar de MAX_BYTES'
);

// 2. Las unidades del ini se leen bien. Un "8M" mal parseado son 8 bytes, y el
//    panel rechazaría cualquier cosa.
//
//    `upload_max_filesize` y `post_max_size` son PHP_INI_PERDIR y no se dejan
//    cambiar con `ini_set`, por eso el parser vive aparte de la lectura del ini.
assertTrue(Media::iniBytes('__no_existe__') === 0, 'Una directiva ausente debe dar 0');

$casos = [
    '2M' => 2 * 1024 * 1024,
    '8M' => 8 * 1024 * 1024,
    '128K' => 128 * 1024,
    '1G' => 1024 * 1024 * 1024,
    '5000000' => 5000000,
    '16m' => 16 * 1024 * 1024,   // minúscula: el ini no obliga a mayúscula
    ' 8M ' => 8 * 1024 * 1024,   // con espacios alrededor
    // "0" y "-1" significan SIN límite, no "cero bytes". Confundirlos dejaría
    // el panel rechazando hasta un archivo de 1 KB.
    '0' => 0,
    '-1' => 0,
    '' => 0,
];
foreach ($casos as $valor => $esperado) {
    $leido = Media::parseBytes((string) $valor);
    assertTrue(
        $leido === $esperado,
        "«{$valor}» debería dar {$esperado} bytes y dio {$leido}"
    );
}

// 4. El techo del servidor, por las dos vías. `.user.ini` cubre PHP-FPM/CGI y
//    los `php_value` cubren mod_php: ningún hosting lee las dos.
$userIni = source('public/.user.ini');
requiresText($userIni, 'upload_max_filesize = 16M', 'El .user.ini no sube el tope de subida');
requiresText($userIni, 'post_max_size = 20M', 'post_max_size debe ir POR ENCIMA de upload_max_filesize');

$htaccess = source('public/.htaccess');
requiresText($htaccess, 'php_value upload_max_filesize 16M', 'Falta el tope para mod_php');
// Un `php_value` suelto en un servidor sin mod_php devuelve 500 y tumba el
// panel entero. Va dentro de <IfModule> siempre.
assertTrue(
    strpos($htaccess, '<IfModule mod_php') !== false,
    'Los php_value tienen que ir dentro de <IfModule>: sin mod_php dan un 500'
);

// 5. La cifra real llega al navegador y el guardia la usa. Sin esto seguiría
//    prometiendo 16 MB mientras el servidor corta en 2.
$vista = source('views/inbox.php');
requiresText($vista, 'data-max-upload', 'La vista no publica el límite real');
requiresText($vista, 'Media::effectiveMaxBytes()', 'El límite no sale de ini_get, sigue siendo fijo');

$js = source('public/assets/inbox.js');
requiresText($js, 'root.dataset.maxUpload', 'El panel no lee el límite del servidor');
assertTrue(
    strpos($js, 'el máximo es 16 MB') === false,
    'El mensaje de error sigue diciendo 16 MB a pelo'
);
requiresText($js, 'el servidor acepta hasta', 'El error no dice cuánto acepta el servidor');

echo "limite de subida contract: OK\n";
