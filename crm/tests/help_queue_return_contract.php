<?php

declare(strict_types=1);

/**
 * La cola de atención tiene UNA salida: devolverle el chat al bot.
 *
 * Hubo un tercer botón, "Quitar de la cola", que sacaba el chat de la franja
 * sin devolvérselo a Don Regalo. Sonaba a un estado propio y no lo era: dejaba
 * `mode=HUMAN` con el bot mudo y `human_support=0`, o sea el chat fuera de la
 * vista de todos y asignado a un asesor que ya había terminado. Y a los 20
 * minutos el releaser lo pasaba a AI por su cuenta, así que el bot lo retomaba
 * igual — tarde y sin que nadie lo hubiera decidido.
 *
 * Quedan tres acciones, cada una con un significado real:
 *   · "Devolver a Don Regalo" y la × de la franja → mode=AI, el bot sigue.
 *   · «Mantener humano» → keep_human=1, el releaser NO te lo quita.
 *   · "Tomar conversación" → mode=HUMAN, el chat es tuyo.
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

function forbidsText(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) !== false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

$view = source('views/inbox.php');
$js = source('public/assets/inbox.js');

// 1. El botón del estado a medias no vuelve.
forbidsText(
    $view,
    'id="btn-dismiss-help"',
    'El botón que sacaba de la cola SIN devolver al bot no debe reaparecer: '
        . 'dejaba el chat en HUMAN y mudo hasta que el releaser lo soltaba solo'
);
forbidsText($js, 'btnDismissHelp', 'Quedó cableado del botón eliminado');

// 2. La × de la franja hace lo mismo que "Devolver a Don Regalo".
requiresText($js, 'chip-dismiss', 'Falta la × en los chips de la cola');
requiresText($js, 'function returnToBot', 'Falta la acción de la ×');
requiresText(
    $js,
    'mode: "AI", human_support: false',
    'La × debe devolver el chat al bot, no solo sacarlo de la franja'
);
requiresText($js, 'title="Devolver a Don Regalo"', 'La × debe decir lo que hace');

// 3. Las otras dos salidas siguen existiendo, que son las que le dan sentido.
requiresText($view, 'Devolver a Don Regalo', 'Falta la salida hacia el bot');
requiresText($view, 'Mantener humano', 'Sin esto no hay forma de quedarse el chat');
requiresText($js, 'keep_human: !!on', '«Mantener humano» debe fijar keep_human');

$css = source('public/assets/app.css');
requiresText($css, '.rail-chip .chip-dismiss', 'Falta el estilo de la × del chip');

echo "help queue return contract: OK\n";
