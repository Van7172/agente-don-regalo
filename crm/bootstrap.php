<?php

declare(strict_types=1);

$configPath = __DIR__ . '/config.php';
if (!is_file($configPath)) {
    $configPath = __DIR__ . '/config.example.php';
}

/** @var array $config */
$config = require $configPath;

// Antes de tocar la BD: MySQL se alinea a esta zona al conectar.
date_default_timezone_set($config['timezone'] ?? 'America/Lima');

require_once __DIR__ . '/src/Database.php';
require_once __DIR__ . '/src/Auth.php';
require_once __DIR__ . '/src/Http.php';
require_once __DIR__ . '/src/Media.php';
require_once __DIR__ . '/src/Repository.php';

Database::init($config['db']);
Auth::init($config);

return $config;
