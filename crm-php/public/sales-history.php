<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

$validDate = static function ($value, string $fallback): string {
    $value = (string) $value;
    return preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) ? $value : $fallback;
};

$from = $validDate(
    $_GET['from'] ?? '',
    date('Y-m-d', strtotime('-30 days'))
);
$to = $validDate($_GET['to'] ?? '', date('Y-m-d'));
$status = in_array(
    $_GET['status'] ?? '',
    ['pendiente', 'entregado'],
    true
) ? (string) $_GET['status'] : '';
$query = trim(substr((string) ($_GET['q'] ?? ''), 0, 100));

$rows = array_map(static function (array $row): array {
    $formatDate = static function ($value): string {
        if (!$value) {
            return '—';
        }
        $timestamp = strtotime((string) $value);
        return $timestamp ? date('d/m/Y H:i', $timestamp) : (string) $value;
    };
    $shipping = $row['envio_sol_venta_historial'] ?? null;
    return [
        'status' => (string) ($row['estado_venta_historial'] ?? 'pendiente'),
        'status_label' => ($row['estado_venta_historial'] ?? '') === 'entregado'
            ? 'Entregado'
            : 'Pendiente de entrega',
        'contact_name' => (string) ($row['nombre_contacto_venta_historial'] ?? ''),
        'wa_id' => (string) ($row['wa_id_venta_historial'] ?? ''),
        'product' => (string) ($row['producto_venta_historial'] ?? ''),
        'district' => (string) ($row['distrito_venta_historial'] ?? ''),
        'shipping' => $shipping !== null ? 'S/' . number_format((float) $shipping, 2) : '—',
        'delivery_date' => (string) ($row['fecha_entrega_venta_historial'] ?? ''),
        'schedule' => (string) ($row['horario_venta_historial'] ?? ''),
        'temporary_order_id' => $row['id_pedido_temporal'] ?? null,
        'closed_at' => $formatDate($row['fecha_cierre_venta_historial'] ?? null),
        'delivered_at' => $formatDate($row['fecha_confirmacion_entrega'] ?? null),
        'advisor' => (string) ($row['nombre_usuario_confirmacion'] ?? ''),
    ];
}, Repository::listSalesHistory($from, $to, $status, $query));

view('sales-history', [
    'from' => $from,
    'to' => $to,
    'status' => $status,
    'query' => $query,
    'rows' => $rows,
]);
