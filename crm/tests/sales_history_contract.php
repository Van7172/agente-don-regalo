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

$migration = source('sql/004_sales_history.sql');
requiresText($migration, 'CREATE TABLE IF NOT EXISTS crm_ventas_historiales', 'Falta tabla histórica');
requiresText($migration, 'INSERT IGNORE INTO crm_ventas_historiales', 'Falta backfill idempotente');
requiresText($migration, 'ON DELETE RESTRICT', 'El historial no debe borrarse en cascada');

$gitignore = source('../.gitignore');
requiresText($gitignore, '!crm/sql/*.sql', 'Las migraciones CRM deben versionarse');

$repository = source('src/Repository.php');
requiresText($repository, 'function archiveSale', 'Falta archivado de ventas');
requiresText($repository, 'function storeActiveSale', 'Anuncio y ficha deben ser atómicos');
requiresText($repository, 'function markSaleDelivered', 'Falta confirmación de entrega');
// El cambio de estado desde el historial va por id de venta, no por conversación:
// una conversación puede tener varias ventas y `markSaleDelivered` toma la última.
requiresText($repository, 'function setSaleStatus', 'Falta cambio de estado del historial');
requiresText($repository, 'id_venta_historial = :saleId AND id_tenant = :tenantId', 'El cambio de estado debe aislar tenant');
requiresText($repository, 'const SALE_STATUSES', 'Los estados válidos deben estar en un solo sitio');
requiresText($repository, 'function listSalesHistory', 'Falta listado histórico');
requiresText($repository, 'id_tenant = :tenantId', 'Las consultas deben aislar tenant');
requiresText($repository, 'beginTransaction()', 'La entrega debe ser atómica');
requiresText($repository, "deleteSetting('sale_' . \$conversationId)", 'La ficha activa debe retirarse');
requiresText($repository, ':queryContact', 'La búsqueda PDO necesita parámetros únicos');

$api = source('public/api/index.php');
requiresText($api, '/sale/delivered', 'Falta endpoint de entrega');
requiresText($api, 'Repository::storeActiveSale', 'Settings debe guardar sale_* atómicamente');
requiresText($api, 'Auth::user()', 'La entrega requiere usuario de sesión');

// El agente se llama Don Regalo (jul 2026). Basta con que el nombre viejo quede
// en un sitio para que el panel y el bot se contradigan delante del cliente.
foreach (['public/assets/inbox.js', 'views/inbox.php', 'views/login.php',
          'views/reports.php', 'views/sales-history.php'] as $archivo) {
    if (strpos(source($archivo), 'Regalito') !== false) {
        throw new RuntimeException("Quedó 'Regalito' en {$archivo}");
    }
}

$inbox = source('public/assets/inbox.js');
requiresText($inbox, 'Marcar como entregado', 'Falta acción en la ficha');
requiresText($inbox, 'alertOnNewLead', 'Falta el aviso de lead nuevo');
requiresText($inbox, 'leadsAvisados', 'El aviso debe ser por lead, no por refresco');
requiresText($inbox, 'tag-new', 'Falta el badge de lead nuevo en la lista');

$repositoryLista = source('src/Repository.php');
requiresText($repositoryLista, 'LEAD_NUEVO_MIN', 'La ventana de "nuevo" debe estar en un solo sitio');
requiresText($repositoryLista, 'es_nuevo DESC', 'El lead nuevo debe subir en la lista');
requiresText($repositoryLista, "'is_new'", 'El panel necesita el flag para avisar');
requiresText($inbox, '/sale/delivered', 'Falta llamada del inbox');
requiresText($inbox, 'window.confirm', 'Falta confirmación previa');

$controller = source('public/sales-history.php');
requiresText($controller, 'Auth::requireLogin()', 'Historial debe requerir login');
requiresText($controller, 'listSalesHistory', 'Controlador no consulta historial');

$api = source('public/api/index.php');
requiresText($api, '/sales/(\d+)/status', 'Falta endpoint de cambio de estado');
requiresText($api, 'Repository::SALE_STATUSES', 'El endpoint debe validar el estado recibido');

$controller = source('public/sales-history.php');
requiresText($controller, "'id' => (int) (\$row['id_venta_historial']", 'La vista necesita el id para cambiar el estado');

$view = source('views/sales-history.php');
requiresText($view, 'Historial de ventas', 'Falta título del módulo');
requiresText($view, 'e(', 'La vista debe escapar datos');
requiresText($view, 'data-sale-status', 'Falta el selector de estado en la tabla');
requiresText($view, 'data-sale-id', 'Cada fila debe llevar su id de venta');

$historyJs = source('public/assets/sales-history.js');
requiresText($historyJs, '/sales/', 'Falta la llamada al endpoint');
requiresText($historyJs, 'select.value = previous', 'Si el API falla el select debe volver atrás');

$layout = source('views/layout.php');
requiresText($layout, 'sales-history.php', 'Falta navegación al historial');

echo "sales history contract: OK\n";
