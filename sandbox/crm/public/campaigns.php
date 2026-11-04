<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

// Igual que en sales-history.php: una fecha basura en la query string no puede
// convertirse en un rango sin sentido. reports.php pasa el $_GET crudo y por eso
// un `?from=ayer` le devuelve la tabla entera.
$validDate = static function ($value, string $fallback): string {
    $value = (string) $value;
    return preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) ? $value : $fallback;
};

$from = $validDate($_GET['from'] ?? '', date('Y-m-d', strtotime('-30 days')));
$to = $validDate($_GET['to'] ?? '', date('Y-m-d'));

$raw = Repository::campaignPerformance($from, $to);

$totalLeads = 0;
$totalVentas = 0;
$totalMonto = 0.0;

$rows = array_map(static function (array $row) use (&$totalLeads, &$totalVentas, &$totalMonto): array {
    $sourceId = $row['ad_source_id'] ?? null;
    $leads = (int) $row['leads'];
    $ventas = (int) $row['ventas'];
    $monto = (float) $row['monto'];
    $leadsHuman = (int) $row['leads_human'];

    $totalLeads += $leads;
    $totalVentas += $ventas;
    $totalMonto += $monto;

    $headline = trim((string) ($row['ad_headline'] ?? ''));
    if ($sourceId === null || $sourceId === '') {
        // Ni orgánico ni anónimo: es "no lo sabemos". Mezcla el tráfico que
        // llegó sin anuncio con todo lo anterior a la migración 007, que no
        // guardaba el referral. Llamarlo "orgánico" sería afirmar de más.
        $label = 'Sin anuncio identificado';
    } elseif ($headline !== '') {
        $label = $headline;
    } else {
        // El nombre del anuncio ("PORTADA FAMILIA") no viaja en el referral de
        // Meta; solo el id. Sin titular, el id es lo único que lo identifica.
        $label = 'Anuncio ' . $sourceId;
    }

    return [
        'source_id' => $sourceId !== null ? (string) $sourceId : '',
        'is_unknown' => $sourceId === null || $sourceId === '',
        'label' => $label,
        'url' => (string) ($row['ad_source_url'] ?? ''),
        'type' => (string) ($row['ad_source_type'] ?? ''),
        'leads' => $leads,
        'leads_human' => $leadsHuman,
        'ventas' => $ventas,
        'monto' => $monto,
        'monto_label' => 'S/' . number_format($monto, 2),
        // Sin chats no hay conversión que calcular. Un 0% ahí se leería como
        // "este anuncio no cierra" cuando lo que pasa es que no trajo a nadie.
        'conversion' => $leads > 0 ? round(($ventas / $leads) * 100, 1) : null,
        'ticket' => $ventas > 0 ? 'S/' . number_format($monto / $ventas, 2) : '—',
        'primer_lead' => (string) ($row['primer_lead'] ?? ''),
    ];
}, $raw);

view('campaigns', [
    'from' => $from,
    'to' => $to,
    'rows' => $rows,
    'totals' => [
        'leads' => $totalLeads,
        'ventas' => $totalVentas,
        'monto_label' => 'S/' . number_format($totalMonto, 2),
        'conversion' => $totalLeads > 0 ? round(($totalVentas / $totalLeads) * 100, 1) : null,
    ],
]);
