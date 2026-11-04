<?php

declare(strict_types=1);

/**
 * Panel operacional (telemetría del agente + CRM).
 *
 * El entrypoint se llama `operaciones.php` a propósito: en hosting,
 * `operations.php` y `opportunities.php` son tan parecidos que un upload
 * equivocado o un corrector de nombres del servidor acababa sirviendo
 * Oportunidades bajo la URL de Operaciones.
 */

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

view('operations', [
    'crmOperations' => Repository::operationalOverview(),
    'agentOperations' => OperationsClient::fetch($config),
]);
