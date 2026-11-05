<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

$validDate = static function ($value, string $fallback): string {
    $value = is_scalar($value) ? (string) $value : '';
    $parsed = DateTimeImmutable::createFromFormat('!Y-m-d', $value);
    return $parsed && $parsed->format('Y-m-d') === $value ? $value : $fallback;
};
$from = $validDate($_GET['from'] ?? '', date('Y-m-d', strtotime('-30 days')));
$to = $validDate($_GET['to'] ?? '', date('Y-m-d'));
if ($from > $to) {
    [$from, $to] = [$to, $from];
}
$overview = Repository::reportsOverview($from, $to);
$rows = Repository::reportsConversations($from, $to, 150);
$daily = Repository::reportsDaily($from, $to);
$funnel = Repository::stageFunnel($from, $to);

view('reports', [
    'from' => $from,
    'to' => $to,
    'overview' => $overview,
    'rows' => $rows,
    'daily' => $daily,
    'funnel' => $funnel,
]);
