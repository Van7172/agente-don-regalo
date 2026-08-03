<?php

declare(strict_types=1);

/**
 * El despliegue se puede comprobar desde fuera.
 *
 * El orden es SQL → CRM PHP → agente y los dos primeros pasos se hacen a mano
 * contra el hosting. Saltarse uno no da un error: da una pantalla vacía, y una
 * tabla de Oportunidades vacía se lee igual que "no falta nada en el catálogo".
 * `scripts/check_deploy.py` convierte ese silencio en un fallo; esto comprueba
 * que las piezas de las que depende siguen existiendo.
 */

function source(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function requiresText(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

// 1. El endpoint existe y NO es público. El esquema le dice a cualquiera qué
//    versión corre el CRM y qué columnas tiene.
$api = source('public/api/index.php');
requiresText($api, "\$path === '/schema'", 'Falta GET /schema');
$desdeSchema = strstr($api, "\$path === '/schema'");
requiresText(
    substr((string) $desdeSchema, 0, 200),
    'Auth::assertInternalToken()',
    'El esquema va con token: /health es público y esto no puede serlo'
);

// 2. El repositorio comprueba COLUMNAS, no tablas.
$repo = source('src/Repository.php');
requiresText($repo, 'function schemaState', 'Falta schemaState');
requiresText(
    $repo,
    'information_schema.COLUMNS',
    'Las migraciones que añaden columnas (007, 012, 013) corren sobre tablas que '
        . 'YA existen: preguntar por la tabla las daría por aplicadas siempre'
);
requiresText($repo, 'TABLE_SCHEMA = DATABASE()', 'Hay que mirar la base actual, no todas');
foreach (
    [
        '013_venta_producto_id',
        '014_demanda_no_cubierta',
    ] as $migracion
) {
    requiresText($repo, $migracion, "schemaState no vigila la migración {$migracion}");
}

// 3. El script no escribe en la tabla de análisis para probar la ruta.
$script = source('../scripts/check_deploy.py');
requiresText(
    $script,
    'json={}',
    'La ruta de demanda se prueba con un cuerpo inválido: meter una fila de '
        . 'mentira ensucia una tabla que luego se lee como señal de negocio'
);
requiresText(
    $script,
    'def _json',
    'PHP responde 200 con un fatal en HTML si ya mandó las cabeceras; sin esto '
        . 'el propio check muere con un JSONDecodeError y no informa de nada'
);
requiresText(
    $script,
    '_FATAL',
    'Una página con un fatal de PHP devuelve 200: por el status daría por buena '
        . 'una pantalla rota'
);

echo "deploy check contract: OK\n";
