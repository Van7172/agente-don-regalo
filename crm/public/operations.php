<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

view('operations', [
    'crmOperations' => Repository::operationalOverview(),
    'agentOperations' => OperationsClient::fetch($config),
]);
