<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

$validDate = static function ($value, string $fallback): string {
    $value = (string) $value;
    return preg_match('/^\d{4}-\d{2}-\d{2}$/', $value) ? $value : $fallback;
};

$from = $validDate($_GET['from'] ?? '', date('Y-m-d', strtotime('-30 days')));
$to = $validDate($_GET['to'] ?? '', date('Y-m-d'));

$totalVacio = 0;
$totalAprox = 0;

$rows = array_map(static function (array $row) use (&$totalVacio, &$totalAprox): array {
    $vacio = (int) $row['veces_vacio'];
    $aprox = (int) $row['veces_aproximado'];
    $totalVacio += $vacio;
    $totalAprox += $aprox;

    $ultima = (string) ($row['ultima_vez'] ?? '');
    $ts = $ultima !== '' ? strtotime($ultima) : false;

    return [
        'query' => (string) $row['consulta_demanda'],
        'categoria' => (string) ($row['categoria'] ?? ''),
        'veces' => (int) $row['veces'],
        'vacio' => $vacio,
        'aproximado' => $aprox,
        'chats' => (int) $row['chats'],
        'ultima_vez' => $ts ? date('d/m/Y', $ts) : '—',
    ];
}, Repository::unmetDemand($from, $to, 100));

view('opportunities', [
    'from' => $from,
    'to' => $to,
    'rows' => $rows,
    'totals' => [
        'terminos' => count($rows),
        'vacio' => $totalVacio,
        'aproximado' => $totalAprox,
    ],
]);
