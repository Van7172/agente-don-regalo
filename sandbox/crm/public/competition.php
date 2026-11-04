<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

$stats = Repository::competitionStats();
$gaps = array_map(static function (array $row): array {
    $visto = (string) ($row['visto_por_ultima_vez'] ?? '');
    $ts = $visto !== '' ? strtotime($visto) : false;
    $precio = $row['precio_sol'];
    return [
        'competidor' => (string) $row['nombre_competidor'],
        'nombre' => (string) $row['nombre_producto'],
        'precio' => $precio !== null ? (float) $precio : null,
        'url' => (string) $row['url'],
        'match_score' => $row['match_score'] !== null ? (float) $row['match_score'] : null,
        'match_nombre' => (string) ($row['match_nombre'] ?? ''),
        'visto' => $ts ? date('d/m/Y H:i', $ts) : '—',
    ];
}, Repository::competitionGaps(100));

$totalHuecos = 0;
$totalActivos = 0;
foreach ($stats as $s) {
    $totalHuecos += (int) ($s['huecos'] ?? 0);
    $totalActivos += (int) ($s['activos'] ?? 0);
}

view('competition', [
    'stats' => $stats,
    'gaps' => $gaps,
    'totals' => [
        'activos' => $totalActivos,
        'huecos' => $totalHuecos,
        'competidores' => count($stats),
    ],
]);
