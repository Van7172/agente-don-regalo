<?php

declare(strict_types=1);

/**
 * Fase 2 competencia: SQL → CRM → agente, sin inventar cifras.
 */

function compSource(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function compRequires(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

$sql = compSource('sql/016_competencia.sql');
compRequires($sql, 'crm_competencia_productos', 'Falta la tabla de productos ajenos');
compRequires($sql, 'url VARCHAR(1024) NOT NULL', 'Sin URL obligatoria se puede mostrar basura');
compRequires($sql, 'visto_por_ultima_vez', 'Sin visto_por_ultima_vez no se detecta lo que dejaron de vender');
compRequires($sql, 'rosatel', 'Seed Rosatel');
compRequires($sql, 'magia', 'Seed Magia');
compRequires($sql, 'sorprendelima', 'Seed Sorprende Lima');

$repo = compSource('src/Repository.php');
compRequires($repo, 'upsertCompetitionProducts', 'Falta el upsert desde el agente');
compRequires($repo, 'competitionGaps', 'Falta la lectura de huecos');
compRequires($repo, '016_competencia', 'schemaState debe conocer la migración 016');

$api = compSource('public/api/index.php');
compRequires($api, '/competition/products', 'Falta el endpoint de upsert');

$layout = compSource('views/layout.php');
compRequires($layout, 'competition.php', 'La nav debe enlazar Competencia');

$agent = compSource('../app/services/competition_adapters.py');
compRequires($agent, 'DonRegaloBot/1.0', 'User-Agent identificable');
compRequires($agent, 'allowed_by_robots', 'Hay que respetar robots.txt');
compRequires($agent, 'products.json', 'Shopify vía products.json');
compRequires(
    $agent,
    'vtexcommercestable',
    'Rosatel no debe pegarle a www.rosatel.pe/api (robots Disallow)'
);

$crawl = compSource('../app/services/competition_crawl.py');
compRequires($crawl, 'maybe_run_crawl', 'El watchdog necesita un entrypoint');
compRequires($crawl, 'upsert_competition_products', 'El crawl debe persistir en el CRM');

$watchdog = compSource('../app/services/watchdog.py');
compRequires($watchdog, 'check_competition', 'El tick debe llamar al crawl');

$client = compSource('../app/crm/http_client.py');
compRequires($client, 'upsert_competition_products', 'Falta el cliente HTTP');

echo "competencia_contract OK\n";
