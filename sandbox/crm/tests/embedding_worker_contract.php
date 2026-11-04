<?php

declare(strict_types=1);

function embeddingSource(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    $source = file_get_contents($path);
    if ($source === false) {
        throw new RuntimeException("No se pudo leer {$relative}");
    }
    return $source;
}

function embeddingRequires(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message);
    }
}

$api = embeddingSource('public/api/index.php');
$repository = embeddingSource('src/Repository.php');

embeddingRequires($api, '/embedding-jobs/claim', 'Falta endpoint claim');
embeddingRequires($api, "['done', 'deleted', 'retry']", 'Falta contrato de estados');
embeddingRequires($api, 'base64_decode', 'El endpoint no valida el vector');
embeddingRequires($repository, 'FOR UPDATE', 'El claim no bloquea las filas');
embeddingRequires($repository, "status = 'processing'", 'El claim no cambia estado');
embeddingRequires($repository, 'ON DUPLICATE KEY UPDATE', 'No persiste embedding idempotente');
embeddingRequires($repository, 'PDO::PARAM_LOB', 'El embedding no se guarda como binario');

echo "OK embedding worker contract\n";
