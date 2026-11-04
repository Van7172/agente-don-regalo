<?php

declare(strict_types=1);

/**
 * Menú lateral ampliable. Protege lo que se rompe sin dar error: que en modo simple
 * un enlace se quede sin nombre, que el lateral parpadee en cada página o que una
 * sección desaparezca del menú.
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

$layout = source('views/layout.php');
$css = source('public/assets/app.css');
$js = source('public/assets/sidebar.js');

// 1. Todas las secciones siguen en el menú.
foreach ([
    "'/'", 'sales-history.php', 'reports.php', 'campaigns.php', 'opportunities.php',
    'competition.php', 'content.php', 'operaciones.php', 'settings.php',
] as $page) {
    requiresText($layout, $page, 'Falta una sección en el menú');
}

// 2. En modo simple solo queda el icono: el enlace necesita nombre propio.
requiresText($layout, 'aria-label="<?= e($label) ?>"', 'Un enlace solo con icono no tiene nombre accesible');
requiresText($layout, 'title="<?= e($label) ?>"', 'Falta el texto al pasar el ratón sobre el icono');
requiresText($layout, 'aria-current="page"', 'El menú debe marcar la página actual');

// 3. El modo se pone antes de pintar, o el lateral parpadea en cada cambio de página.
$head = substr($layout, 0, (int) strpos($layout, '</head>'));
requiresText($head, 'localStorage.getItem("dr.sidebarMode")', 'El modo se decide en <head>');
requiresText($js, 'localStorage.setItem("dr.sidebarMode"', 'La preferencia debe recordarse');
requiresText($head, 'try {', 'localStorage puede lanzar en modo privado');
requiresText($head, 'var mode = "simple"', 'Por defecto el menú va en modo simple');
requiresText($head, '=== "full"', 'Solo arranca ampliado si el asesor lo eligió');

// 4. Los dos modos existen en CSS y el panel móvil no ocupa foco cerrado.
requiresText($css, 'html[data-sidebar="simple"] .sidebar-label', 'El modo simple oculta los textos');
requiresText($css, 'body.sidebar-open .sidebar', 'Falta el panel abierto en móvil');
requiresText($css, '.app-main { margin-left: 68px; }', 'En pantalla mediana el menú ampliado va encima del contenido');
requiresText($js, 'if (!compact.matches)', 'En pantalla mediana ampliar el menú no es una preferencia');
requiresText($css, 'visibility: hidden', 'El panel cerrado no debe recibir foco con el tabulador');
requiresText($css, 'prefers-reduced-motion', 'La animación debe respetar la preferencia del sistema');

// 5. Dentro de un chat en móvil, la barra cede la pantalla a la conversación.
requiresText($css, 'body.mobile-chat-open .topbar', 'La barra móvil debe ceder espacio al chat');

// 6. El panel se puede cerrar sin ratón.
requiresText($js, '"Escape"', 'Escape debe cerrar el panel');

echo "sidebar contract: OK\n";
