<?php

declare(strict_types=1);

/**
 * Compatibilidad: la URL antigua `operations.php` redirige al entrypoint real.
 * No renderiza vistas aquí — evita el choque con `opportunities.php`.
 */

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';

$target = url_to('operaciones.php');
if ($target === '' || $target === '/') {
    $target = '/operaciones.php';
}

$query = $_SERVER['QUERY_STRING'] ?? '';
if (is_string($query) && $query !== '') {
    $target .= (strpos($target, '?') === false ? '?' : '&') . $query;
}

header('Location: ' . $target, true, 302);
exit;
