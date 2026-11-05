<?php

declare(strict_types=1);

/**
 * El chat se mantiene compacto: lo que se midió es cuántos mensajes caben a la vez.
 *
 * Con el aire de antes solo cabían 9 mensajes en un monitor grande y 5 en un
 * portátil. Lo compactó un bloque de reglas que se apoya en unas pocas piezas
 * delicadas; si alguien las quita "por limpieza", el chat vuelve a ocupar el doble
 * sin que ningún otro contrato se entere.
 */

function source(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function requiresPattern(string $source, string $pattern, string $message): void
{
    if (preg_match($pattern, $source) !== 1) {
        throw new RuntimeException($message . " [{$pattern}]");
    }
}

$css = source('public/assets/app.css');

// 1. Separación mínima entre mensajes, y más solo donde cambia quien habla.
requiresPattern(
    $css,
    '/\.thread\s*\{[^}]*gap:\s*3px/s',
    'El hilo volvió a separar los mensajes con aire de sobra'
);
requiresPattern(
    $css,
    '/from-contact\)\s*\+\s*\.msg-row:not\(:has\(\.bubble\.from-contact\)\)[^{]*\{[^}]*margin-top/s',
    'Falta el aire extra al cambiar de emisor: las rachas dejan de leerse como bloques'
);

// 2. La etiqueta solo al empezar una racha. Se distingue por la clase de la burbuja y
//    no por el lado: el bot y el asesor van ambos a la derecha.
foreach (['from-contact', 'from-agent', 'from-bot'] as $emisor) {
    requiresPattern(
        $css,
        '/\.msg-row:has\(\.bubble\.' . $emisor . '\)\s*\+\s*\.msg-row:has\(\.bubble\.' . $emisor . '\)\s*\.who/',
        "Falta ocultar la etiqueta repetida de {$emisor}"
    );
}

// 3. La hora comparte línea con el texto corto; las burbujas con medio quedan en bloque.
requiresPattern(
    $css,
    '/\.bubble:not\(\.has-media\)\s*\{[^}]*display:\s*flex[^}]*flex-wrap:\s*wrap/s',
    'La burbuja de texto dejó de ser flex-wrap: la hora vuelve a ocupar su propia línea'
);
requiresPattern(
    $css,
    '/\.bubble:not\(\.has-media\)\s*>\s*\.at\s*\{[^}]*margin-left:\s*auto/s',
    'La hora ya no se pega al borde derecho de la burbuja'
);

// 4. La foto: altura acotada Y ancho mínimo. Sin el mínimo no se recorta, se encoge:
//    la burbuja se ajusta al ancho escalado y un comprobante vertical queda como una
//    tira de ~100 px donde no se lee nada.
requiresPattern(
    $css,
    '/\.media-img\s*\{[^}]*min-width:\s*1[0-9]{2}px[^}]*max-height:\s*2[0-9]{2}px/s',
    'La foto del chat perdió su ancho mínimo o su altura máxima'
);
requiresPattern(
    $css,
    '/\.media-img\s*\{[^}]*object-position:\s*top/s',
    'La foto recortada debe mostrar la parte de arriba: ahí van el monto y el nombre'
);

echo "chat densidad contract: OK\n";
