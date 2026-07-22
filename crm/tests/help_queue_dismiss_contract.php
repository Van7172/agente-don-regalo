<?php

declare(strict_types=1);

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

$view = source('views/inbox.php');
requiresText($view, 'btn-dismiss-help', 'Falta el botón Quitar de la cola');
requiresText($view, 'Quitar de la cola', 'Falta la etiqueta del botón');

$js = source('public/assets/inbox.js');
requiresText($js, 'dismissHelp', 'Falta la acción de quitar de la cola');
requiresText($js, 'human_support: false', 'Debe apagar human_support sin tocar el modo');
requiresText($js, 'chip-dismiss', 'Falta la × en los chips de la cola');

$css = source('public/assets/app.css');
requiresText($css, '.rail-chip .chip-dismiss', 'Falta el estilo de la × del chip');

echo "help queue dismiss contract: OK\n";
