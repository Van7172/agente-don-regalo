<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

$from = (string) ($_GET['from'] ?? date('Y-m-d', strtotime('-30 days')));
$to = (string) ($_GET['to'] ?? date('Y-m-d'));
$overview = Repository::reportsOverview($from, $to);
$rows = Repository::reportsConversations($from, $to, 150);
$daily = Repository::reportsDaily($from, $to);

view('reports', [
    'from' => $from,
    'to' => $to,
    'overview' => $overview,
    'rows' => $rows,
    'daily' => $daily,
]);
