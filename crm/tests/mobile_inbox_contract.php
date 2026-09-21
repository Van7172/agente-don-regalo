<?php

declare(strict_types=1);

function mobileSource(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function mobileRequires(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

$view = mobileSource('views/inbox.php');
$css = mobileSource('public/assets/app.css');
$javascript = mobileSource('public/assets/inbox.js');
$layout = mobileSource('views/layout.php');

mobileRequires(
    $layout,
    'viewport-fit=cover',
    'La interfaz debe respetar las áreas seguras de iPhone'
);

foreach ([
    ['id="btn-back"', 'El chat móvil necesita volver a la bandeja'],
    ['id="btn-chat-actions"', 'Falta el menú compacto de acciones'],
    ['id="chat-actions"', 'Las acciones del chat no tienen contenedor móvil'],
    ['mobile-only-label', 'El icono del lead necesita una etiqueta táctil'],
] as [$needle, $message]) {
    mobileRequires($view, $needle, $message);
}

foreach ([
    ['.inbox-shell[data-mobile-chat="false"] .list-pane', 'Falta el estado de bandeja'],
    ['.inbox-shell[data-mobile-chat="true"] .chat-pane', 'Falta el estado de conversación'],
    ['body.mobile-chat-open .topbar', 'La barra global debe ceder espacio al chat'],
    ['--crm-viewport-height', 'El alto móvil no contempla el teclado'],
    ['env(safe-area-inset-bottom)', 'El compositor no respeta el área segura'],
    ['.lead-panel.mobile-open', 'El resumen del cliente debe existir en móvil'],
    ['.chat-actions.is-open', 'El menú de acciones no tiene estado visible'],
] as [$needle, $message]) {
    mobileRequires($css, $needle, $message);
}

foreach ([
    ['function setMobileChat', 'Falta una transición única entre lista y chat'],
    ['window.history.pushState', 'Abrir un chat debe integrarse con Atrás del teléfono'],
    ['window.addEventListener("popstate"', 'Atrás no devuelve a la bandeja'],
    ['window.visualViewport', 'El teclado móvil puede tapar el compositor'],
    ['classList.contains("mobile-open")', 'No se controla la ficha móvil del lead'],
] as [$needle, $message]) {
    mobileRequires($javascript, $needle, $message);
}

if (strpos($css, '#btn-lead { display: none; }') !== false) {
    throw new RuntimeException('El resumen del cliente no puede desaparecer en móvil');
}

echo "mobile inbox contract: OK\n";
