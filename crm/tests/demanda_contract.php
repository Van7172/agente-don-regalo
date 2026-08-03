<?php

declare(strict_types=1);

/**
 * La demanda no cubierta llega desde el agente hasta la pantalla.
 *
 * La cadena cruza los tres despliegues (SQL → CRM → agente) y la señal NO es
 * recuperable hacia atrás: si un eslabón se cae, no se detecta como un error
 * sino como una tabla vacía, que se lee igual que "no falta nada en el
 * catálogo". Es el peor modo de fallo posible para este módulo.
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

// 1. El agente anota al agotar la escalera, no en cada peldaño.
$executor = source('../app/tools/executor.py');
requiresText($executor, 'def _record_demand', 'Falta el registro de demanda en el executor');
requiresText(
    $executor,
    'demand.VACIO',
    'Vacío y aproximado son dos carencias distintas y hay que separarlas'
);
requiresText(
    $executor,
    'str(args.get("q") or "").strip()',
    'Se anota la consulta ORIGINAL: si "unicornio gigante" solo funcionó al '
        . 'acortarse a "peluche", guardar "peluche" diría que falta lo que sí hay'
);

// 2. El módulo de demanda no puede costarle latencia ni errores al turno.
$demand = source('../app/services/demand.py');
requiresText(
    $demand,
    'create_task',
    'Es telemetría: se lanza y se vuelve. Esperar al CRM añadiría latencia al '
        . 'turno del cliente por un dato que no cambia la respuesta'
);
requiresText(
    $demand,
    '_pending',
    'Sin referencia fuerte, el GC puede llevarse la tarea antes de que envíe'
);
requiresText(
    $demand,
    'redact_personal_data',
    'El argumento lo compone el modelo: nada garantiza que no arrastre PII'
);
requiresText($demand, 'except Exception', 'Un fallo al anotar no puede ensuciar un turno correcto');

// 3. La conversación viaja por ContextVar desde el arranque del turno.
requiresText(
    source('../app/harness/master.py'),
    'demand.set_conversation(conversation_id)',
    'Sin esto las búsquedas se anotan huérfanas y no se puede separar un '
        . 'cliente insistiendo de un producto que falta de verdad'
);

// 4. El transporte.
requiresText(
    source('../app/crm/http_client.py'),
    'async def record_demand_miss',
    'Falta el cliente HTTP hacia el CRM'
);

// 5. El endpoint y el repositorio.
requiresText(source('public/api/index.php'), "\$path === '/demand'", 'Falta el endpoint /demand');
$repo = source('src/Repository.php');
requiresText($repo, 'function recordDemandMiss', 'Falta la escritura');
requiresText($repo, 'function unmetDemand', 'Falta la lectura agrupada');
requiresText(
    $repo,
    'FROM crm_conversations',
    'La conversación llega del agente: hay que validarla contra el tenant o una '
        . 'FK rota tumba el INSERT entero por un dato accesorio'
);
requiresText(
    $repo,
    'ORDER BY veces_vacio DESC',
    'Lo que no dejó NADA que ofrecer pesa más que lo que se resolvió con alternativas'
);

// 6. La pantalla.
requiresText(
    source('views/opportunities.php'),
    'Solo cuenta desde que se instaló esta medición',
    'Una tabla vacía se lee como "no falta nada": hay que decir desde cuándo mide'
);
requiresText(
    source('views/opportunities.php'),
    'Chats',
    'Sin separar chats de veces, un cliente insistiendo parece demanda real'
);
requiresText(source('views/layout.php'), "url_to('opportunities.php')", 'Falta el enlace en la navegación');

echo "demanda contract: OK\n";
