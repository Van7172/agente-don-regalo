<?php

declare(strict_types=1);

function opsSource(string $relative): string
{
    $path = dirname(__DIR__) . '/' . $relative;
    if (!is_file($path)) {
        throw new RuntimeException("Falta {$relative}");
    }
    return (string) file_get_contents($path);
}

function opsRequires(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) === false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

$client = opsSource('src/OperationsClient.php');
if (strncmp($client, '<?php', 5) !== 0) {
    throw new RuntimeException(
        'OperationsClient.php debe comenzar exactamente en <?php, sin BOM ni espacios'
    );
}
opsRequires($client, '/internal/operations', 'El CRM no consulta el snapshot del agente');
opsRequires($client, 'X-Agent-Token:', 'La consulta debe autenticarse servidor a servidor');
opsRequires($client, 'CURLOPT_TIMEOUT', 'La caída del agente no debe colgar el CRM');

$repository = opsSource('src/Repository.php');
opsRequires($repository, 'function operationalOverview', 'Falta agregación operacional del CRM');
opsRequires($repository, 'pending_handoffs', 'Falta conteo de handoffs');
opsRequires($repository, "status_outbox = \\'failed\\'", 'Falta seguimiento del outbox fallido');
opsRequires($repository, 'id_tenant = :tenantId', 'Los handoffs deben aislar tenant');

$api = opsSource('public/api/index.php');
opsRequires($api, "\$path === '/operations'", 'Falta endpoint del panel');
opsRequires($api, 'Auth::user()', 'El endpoint debe exigir sesión o token');

$view = opsSource('views/operations.php');
$javascript = opsSource('public/assets/operations.js');
$layout = opsSource('views/layout.php');
opsRequires($view, 'Panel operacional', 'Falta vista operacional');
opsRequires($view, 'ops-circuits', 'Falta tabla de circuit breakers');
opsRequires($view, 'ops-latencies', 'Falta tabla de latencias');
// El equipo mira este panel, no Grafana: dejar los percentiles solo en
// /metrics sería medir la latencia para nadie. Y la media sola miente — la
// aplana el caso bueno, y el máximo es UN pico.
opsRequires($view, '<th>p95</th>', 'La tabla de latencias necesita el p95');
$opsJs = opsSource('public/assets/operations.js');
opsRequires($opsJs, 'duration_ms_p95', 'El panel no lee el percentil del agente');
// Repartir la suma entre llamadas que nunca se cronometraron daba una media
// más baja que la real.
opsRequires($opsJs, 'duration_count', 'La media debe dividirse entre lo cronometrado');
opsRequires($view, 'ops-handoffs', 'Falta tabla de handoffs');
opsRequires($javascript, 'setInterval(refresh, 15000)', 'Falta actualización automática');
opsRequires($javascript, 'textContent', 'Los datos dinámicos deben renderizarse como texto');
opsRequires($layout, 'operations.php', 'Falta acceso al panel en la navegación');

if (strpos($view, 'agent_internal_token') !== false || strpos($javascript, 'agent_internal_token') !== false) {
    throw new RuntimeException('El token interno no puede llegar al navegador');
}

echo "operations panel contract: OK\n";
