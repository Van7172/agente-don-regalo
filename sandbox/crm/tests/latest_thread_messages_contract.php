<?php

declare(strict_types=1);

$repository = (string) file_get_contents(dirname(__DIR__) . '/src/Repository.php');

$start = strpos($repository, 'public static function getMessages');
$end = strpos($repository, '/**', $start ?: 0);
if ($start === false || $end === false) {
    throw new RuntimeException('No se pudo aislar getMessages');
}
$query = substr($repository, $start, $end - $start);

// La bandeja lateral muestra el último mensaje. El hilo debe tomar también los
// últimos N, no los primeros N históricos de una conversación larga.
if (strpos($query, 'ORDER BY newest.id_message ASC') === false) {
    throw new RuntimeException(
        'getMessages no devuelve los mensajes recientes en orden cronológico'
    );
}
if (strpos($query, 'ORDER BY id_message DESC') === false) {
    throw new RuntimeException(
        'getMessages debe seleccionar primero los mensajes más recientes'
    );
}

echo "latest thread messages contract: OK\n";
