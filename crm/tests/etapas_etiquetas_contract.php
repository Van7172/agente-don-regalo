<?php

declare(strict_types=1);

/**
 * Etapas, etiquetas, respuestas rápidas y CSV.
 *
 * Cada aserción protege algo que se puede romper sin que nada falle a la vista:
 * un embudo que contradice al historial, un aviso sonoro que deja de sonar o una
 * fórmula que se ejecuta al abrir el Excel.
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

$repository = source('src/Repository.php');
$api = source('public/api/index.php');
$inbox = source('public/assets/inbox.js');
$view = source('views/inbox.php');
$export = source('public/export.php');

// 1. Vendido y Entregado salen de las ventas, en la misma transacción.
requiresText($repository, "const STAGES_MANUAL = ['nuevo', 'cotizado', 'perdido']", 'Vendido y Entregado no se marcan a mano');
foreach (['storeActiveSale', 'markSaleDelivered', 'setSaleStatus'] as $function) {
    $start = strpos($repository, "public static function {$function}");
    $body = substr($repository, (int) $start, 4000);
    $sync = strpos($body, 'syncStageFromSales');
    $commit = strpos($body, '$pdo->commit()');
    if ($sync === false || $commit === false || $sync > $commit) {
        throw new RuntimeException("{$function} debe alinear la etapa ANTES del commit");
    }
}

// 2. Un fallo de la etapa no puede hacer perder un mensaje entrante.
$reopen = strpos($api, 'Repository::reopenStageOnInbound');
if ($reopen === false || strpos(substr($api, max(0, $reopen - 200), 200), 'try {') === false) {
    throw new RuntimeException('La reapertura va en try/catch: el mensaje ya está guardado');
}

// 3. El filtro se resuelve en el servidor y no toca la lista de los avisos.
requiresText($api, 'Repository::parseConversationFilters($_GET)', 'El filtro va en el servidor');
requiresText($inbox, 'alertOnInbound(next)', 'El aviso sonoro lee la lista completa');
requiresText($inbox, 'filteredList ?? conversations', 'Lo filtrado es solo para mostrar');
requiresText($inbox, 'api("/conversations"),', 'Siempre se pide la lista sin filtrar');

// 4. Las respuestas rápidas salen de la BD y no se pintan como HTML sin escapar.
forbidsText($inbox, 'const QUICK_REPLIES', 'Las respuestas ya no van fijas en el JS');
requiresText($inbox, 'api("/quick-replies")', 'El inbox carga las respuestas desde la API');
requiresText($inbox, '${esc(r.label)}', 'El título lo escribe el equipo: se escapa');

// 5. Las rutas nuevas exigen sesión: dejan constancia de quién hizo el cambio.
requiresText($api, '$panelUser = static function', 'Falta la exigencia de sesión');
foreach ([
    "'#^/conversations/(\\d+)/stage$#'",
    "'#^/conversations/(\\d+)/tags$#'",
    "'#^/tags/(\\d+)$#'",
    "'#^/quick-replies/(\\d+)$#'",
] as $route) {
    $at = strpos($api, $route);
    if ($at === false || strpos(substr($api, $at, 150), '$panelUser()') === false) {
        throw new RuntimeException("La ruta {$route} debe exigir sesión");
    }
}

// 6. Una respuesta rápida nunca se envía sola: solo llena el borrador.
forbidsText(
    substr($repository, (int) strpos($repository, 'function createQuickReply'), 1500),
    'crm_outbox',
    'Guardar una respuesta no puede enviar nada'
);

// 7. CSV: sesión obligatoria, BOM y celdas neutralizadas.
requiresText($export, 'Auth::requireLogin()', 'Exportar exige sesión');
requiresText($export, '"\xEF\xBB\xBF"', 'Sin BOM, Excel rompe tildes y emojis');
requiresText($export, "array_map('csv_cell'", 'Toda celda pasa por csv_cell');

require_once dirname(__DIR__) . '/src/helpers.php';
foreach (['=HYPERLINK("http://x")', '+1+cmd|calc', '-1+1', '@SUM(A1)', "  =1+1", "\t=1+1"] as $danger) {
    if (substr(csv_cell($danger), 0, 1) !== "'") {
        throw new RuntimeException('Celda sin neutralizar: ' . $danger);
    }
}
// Un número puro no ejecuta nada, y una comilla delante de un monto lo volvería texto.
foreach (['Ana', '999888777', '+51999888777', '12.50', '-12.50', ''] as $safe) {
    if (csv_cell($safe) !== $safe) {
        throw new RuntimeException('No debe alterar: ' . $safe);
    }
}

// 8. El verificador de despliegue conoce las migraciones nuevas.
foreach (['017_etapas', '018_etiquetas', '019_respuestas_rapidas'] as $migration) {
    requiresText($repository, "'{$migration}'", 'schemaState debe vigilar la migración');
    if (!is_file(dirname(__DIR__) . "/sql/{$migration}.sql")) {
        throw new RuntimeException("Falta sql/{$migration}.sql");
    }
}

// 9. La vista expone los catálogos del servidor: una sola lista de etapas.
requiresText($view, 'Repository::STAGES', 'Las etapas salen del servidor');
requiresText($view, 'Repository::LOST_REASONS', 'Los motivos salen del servidor');

echo "etapas_etiquetas_contract: OK\n";
