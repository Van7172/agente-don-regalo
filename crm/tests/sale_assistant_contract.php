<?php

declare(strict_types=1);

function saleAiSource(string $relative): string
{
    $path = dirname(__DIR__, 2) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function saleAiRequires(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

$api = saleAiSource('crm/public/api/index.php');
$repository = saleAiSource('crm/src/Repository.php');
$view = saleAiSource('crm/views/inbox.php');
$javascript = saleAiSource('crm/public/assets/inbox.js');
$service = saleAiSource('app/services/sale_assistant.py');
$internal = saleAiSource('app/api_internal.py');

saleAiRequires($api, '/sale-suggestion', 'Falta el endpoint de sugerencia');
saleAiRequires($api, "class_exists('AgentClient', false)", 'El endpoint debe tolerar bootstrap antiguo');
saleAiRequires($api, "require_once \$agentClientFile", 'Falta carga de respaldo de AgentClient');
saleAiRequires($api, "new DateTimeImmutable('today'", 'La extracción debe limitarse a hoy');
saleAiRequires($api, "'/internal/sales/extract'", 'El CRM no consulta al agente');
saleAiRequires($repository, 'function getMessagesBetween', 'Falta consulta acotada por fecha');
saleAiRequires($internal, '@router.post("/sales/extract")', 'Falta endpoint interno del agente');
saleAiRequires($service, 'if value is None or not citations:', 'Un valor sin evidencia debe descartarse');
saleAiRequires($service, 'nunca registra nada', 'La herramienta debe documentar que no persiste');
saleAiRequires($view, 'id="sale-ai-status"', 'Falta estado del análisis en el formulario');
saleAiRequires($view, 'Confirmar y registrar', 'La venta necesita confirmación explícita');
saleAiRequires($javascript, 'suggestSale(convId)', 'Abrir el formulario debe iniciar el análisis');
saleAiRequires($javascript, '!String(input.value || "").trim()', 'La IA no debe pisar correcciones manuales');

echo "sale assistant contract: OK\n";
