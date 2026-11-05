<?php

declare(strict_types=1);

if (PHP_SAPI !== 'cli' || ($argc ?? 0) !== 2) {
    fwrite(STDERR, "Uso: php crm/scripts/import_meta_ads.php ruta/lectura.json\n");
    exit(2);
}
$path = $argv[1];
if (!is_file($path) || !is_readable($path)) {
    fwrite(STDERR, "No se puede leer la lectura JSON.\n");
    exit(2);
}
$raw = file_get_contents($path);
$payload = json_decode((string) $raw, true);
if (!is_array($payload)) {
    fwrite(STDERR, "JSON inválido.\n");
    exit(2);
}
$config = require dirname(__DIR__) . '/bootstrap.php';
try {
    $count = MetaAdsSnapshot::import($payload, (string) ($config['meta_ads_account_id'] ?? '1149788199387012'));
    fwrite(STDOUT, "Lectura de Meta importada: {$count} filas.\n");
} catch (Throwable $e) {
    fwrite(STDERR, "No se importó: " . $e->getMessage() . "\n");
    exit(1);
}
