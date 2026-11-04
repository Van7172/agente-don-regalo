<?php

declare(strict_types=1);

/**
 * Exportar a CSV lo que se ve en Historial de ventas y en Reportes, con sus filtros.
 *
 * Son datos de clientes y hoy cualquier asesor con sesión puede descargarlos, así
 * que cada exportación deja en el log quién, qué y cuántas filas. La búsqueda no se
 * anota: puede llevar el nombre o el teléfono de un cliente.
 */

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

// Excel en español abre un CSV con ";" en columnas. Si en el equipo sale todo en
// una columna, este es el único valor que hay que cambiar.
const CSV_SEPARATOR = ';';

$validDate = static function ($value, string $fallback): string {
    $value = (string) $value;
    return preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) ? $value : $fallback;
};
$from = $validDate($_GET['from'] ?? '', date('Y-m-d', strtotime('-30 days')));
$to = $validDate($_GET['to'] ?? '', date('Y-m-d'));
$tipo = (string) ($_GET['tipo'] ?? '');

if ($tipo === 'ventas') {
    $status = in_array($_GET['status'] ?? '', ['pendiente', 'entregado'], true)
        ? (string) $_GET['status']
        : '';
    $search = trim(substr((string) ($_GET['q'] ?? ''), 0, 100));
    $rows = Repository::listSalesHistory($from, $to, $status, $search, Repository::EXPORT_MAX_ROWS);
    $header = [
        'Estado', 'Cliente', 'WhatsApp', 'Producto', 'Distrito', 'Monto (S/)', 'Envío (S/)',
        'Cerró', 'Vendedor', 'Fecha de entrega', 'Horario', 'Pedido', 'Cierre',
        'Confirmación', 'Confirmó',
    ];
    $toRow = static function (array $row): array {
        return [
            ($row['estado_venta_historial'] ?? '') === 'entregado' ? 'Entregado' : 'Pendiente de entrega',
            $row['nombre_contacto_venta_historial'] ?? '',
            $row['wa_id_venta_historial'] ?? '',
            $row['producto_venta_historial'] ?? '',
            $row['distrito_venta_historial'] ?? '',
            $row['monto_venta_historial'] ?? '',
            $row['envio_sol_venta_historial'] ?? '',
            ($row['origen_venta_historial'] ?? 'bot') === 'asesor' ? 'Asesor' : 'Don Regalo',
            $row['nombre_usuario_registro'] ?? '',
            $row['fecha_entrega_venta_historial'] ?? '',
            $row['horario_venta_historial'] ?? '',
            $row['id_pedido_temporal'] ?? '',
            $row['fecha_cierre_venta_historial'] ?? '',
            $row['fecha_confirmacion_entrega'] ?? '',
            $row['nombre_usuario_confirmacion'] ?? '',
        ];
    };
} elseif ($tipo === 'conversaciones') {
    $rows = Repository::reportsConversations($from, $to, Repository::EXPORT_MAX_ROWS);
    $header = ['ID', 'Cliente', 'Teléfono', 'Estado', 'Etapa', 'Mensajes', 'Última actividad', 'Creada'];
    $toRow = static function (array $row): array {
        if (!empty($row['human_support'])) {
            $estado = 'AYUDA';
        } else {
            $estado = ($row['mode_conversation'] ?? '') === 'HUMAN' ? 'HUMAN' : 'AI';
        }
        return [
            $row['id_conversation'],
            $row['nombre_contact'] ?? '',
            $row['wa_id'] ?? '',
            $estado,
            Repository::STAGES[$row['etapa_conversation'] ?? ''] ?? 'Sin etapa',
            $row['msg_count'] ?? 0,
            $row['last_message_at'] ?? '',
            $row['fecha_creacion'] ?? '',
        ];
    };
} else {
    http_response_code(400);
    echo 'Tipo de exportación no válido';
    exit;
}

$user = Auth::user();
error_log(sprintf(
    '[CRM] exportación CSV tipo=%s desde=%s hasta=%s filas=%d usuario=%d (%s)',
    $tipo,
    $from,
    $to,
    count($rows),
    (int) ($user['id'] ?? 0),
    (string) ($user['login'] ?? '')
));

header('Content-Type: text/csv; charset=utf-8');
header('Content-Disposition: attachment; filename="' . $tipo . '_' . $from . '_a_' . $to . '.csv"');
header('Cache-Control: private, no-store');
header('X-Content-Type-Options: nosniff');

$out = fopen('php://output', 'wb');
// BOM: sin él, Excel lee el UTF-8 como ANSI y rompe tildes y emojis.
fwrite($out, "\xEF\xBB\xBF");
$write = static function (array $cells) use ($out): void {
    fputcsv($out, array_map('csv_cell', $cells), CSV_SEPARATOR, '"', '');
};
$write($header);
foreach ($rows as $row) {
    $write($toRow($row));
}
fclose($out);
