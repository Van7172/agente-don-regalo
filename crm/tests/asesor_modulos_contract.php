<?php

declare(strict_types=1);

/**
 * Contrato de los módulos del asesor (Tier 1):
 * venta manual, asignación, ventana de 24 h, notas internas y seguimientos.
 *
 * Cada aserción de aquí protege una decisión que costó razonar, no la existencia
 * de un archivo. Si alguna se cae, léela: dice POR QUÉ estaba así.
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

function forbidsText(string $source, string $needle, string $message): void
{
    if (strpos($source, $needle) !== false) {
        throw new RuntimeException($message . " [{$needle}]");
    }
}

$repository = source('src/Repository.php');
$api = source('public/api/index.php');
$inbox = source('public/assets/inbox.js');
$view = source('views/inbox.php');
$css = source('public/assets/app.css');

// ── migraciones ─────────────────────────────────────────────────────────────
// El orden de despliegue es SQL → CRM PHP → agente: sin la migración aplicada,
// el PHP consulta columnas que no existen y tumba el inbox entero.
$asignacion = source('sql/009_asignacion_asesor.sql');
requiresText($asignacion, 'ADD COLUMN id_usuario_asignado', 'Falta la columna de asignación');
requiresText($asignacion, 'nombre_usuario_asignado', 'El nombre se copia, no se resuelve por JOIN');
requiresText($asignacion, 'idx_crm_conv_asignado', 'El filtro "Mis chats" necesita índice');

$notas = source('sql/010_notas_internas.sql');
requiresText($notas, 'CREATE TABLE IF NOT EXISTS crm_conversation_notes', 'Falta la tabla de notas');
requiresText($notas, 'ON DELETE CASCADE', 'Las notas mueren con su conversación');

$seguimientos = source('sql/011_seguimientos.sql');
requiresText($seguimientos, 'CREATE TABLE IF NOT EXISTS crm_seguimientos', 'Falta la tabla de seguimientos');
requiresText($seguimientos, 'fecha_programada DATETIME', 'El seguimiento necesita hora, no solo día');
requiresText($seguimientos, 'idx_crm_seg_pendiente', 'El rail consulta pendientes vencidos: hace falta índice');

$ventaManual = source('sql/012_venta_manual.sql');
requiresText($ventaManual, "origen_venta_historial ENUM('bot','asesor')", 'Falta el origen de la venta');
requiresText($ventaManual, "DEFAULT 'bot'", 'Las filas viejas son del bot');
requiresText($ventaManual, 'monto_venta_historial', 'Sin monto, una venta manual no se puede sumar');

// ── 1. venta registrada por el asesor ───────────────────────────────────────
requiresText($repository, 'function registerManualSale', 'Falta el registro de venta manual');
requiresText($repository, 'self::storeActiveSale($conversationId, $sale)', 'La venta manual va por el mismo camino que la del bot');
// El origen viaja DENTRO del snapshot: `markSaleDelivered` vuelve a archivar la
// ficha al confirmar la entrega y, si fuera un argumento con default 'bot', ese
// segundo archivado le borraría la autoría a toda venta de un asesor.
requiresText($repository, "\$origin = (\$sale['origen'] ?? 'bot')", 'El origen debe leerse del snapshot');
requiresText($repository, 'origen_venta_historial = VALUES(origen_venta_historial)', 'Re-archivar no puede perder el origen');
requiresText($repository, 'id_usuario_registro = VALUES(id_usuario_registro)', 'Re-archivar no puede perder al asesor');
requiresText($api, '#^/conversations/(\d+)/sale$#', 'Falta el endpoint de venta manual');
requiresText($inbox, '/sale`', 'El inbox no llama al endpoint de venta');
requiresText($view, 'sale-dialog', 'Falta el diálogo de registrar venta');
requiresText($inbox, 'sale.origen === "asesor"', 'La ficha debe distinguir quién cerró la venta');
$historial = source('views/sales-history.php');
requiresText($historial, "row['origin_label']", 'El historial debe decir quién cerró cada venta');

// ── 2. asignación de asesor ─────────────────────────────────────────────────
requiresText($repository, 'function claimConversation', 'Falta el claim de conversación');
// Un UPDATE condicional es atómico bajo el lock de fila de InnoDB. Comprobar con
// un SELECT y actualizar después deja la ventana abierta justo cuando importa:
// la cola se despacha en ráfagas y los dos clics caen en el mismo segundo.
requiresText($repository, 'id_usuario_asignado IS NULL OR id_usuario_asignado = :userIdGuard', 'El claim debe ser un UPDATE condicional');
// El veredicto NO puede salir de rowCount(): MySQL cuenta filas CAMBIADAS, no
// coincidentes, así que el dueño re-reclamando la suya —cosa que pasa en CADA
// mensaje, porque responder reclama— recibía 0 y el panel le ofrecía quitarle
// el chat a sí mismo. La pregunta real es de quién es la fila DESPUÉS.
requiresText($repository, "\$assigned !== null && \$assigned['id'] === \$userId", 'El claim se decide leyendo de quién es la fila');
requiresText($repository, 'function releaseConversation', 'Devolver el chat al bot debe soltarlo');
requiresText($api, 'Repository::releaseConversation($id)', 'Volver a AI tiene que liberar la asignación');
requiresText($api, '#^/conversations/(\d+)/claim$#', 'Falta el endpoint de claim');
// El orden importa: reclamar y SOLO entonces pasar a HUMAN. Al revés, el chat
// se quedaría en HUMAN aunque el claim lo hubiera ganado otro y el asesor vería
// el composer abierto sobre un cliente que no es suyo.
requiresText($inbox, 'async function takeConversation', 'Tomar la conversación debe pasar por el claim');
requiresText($inbox, 'if (!res.claimed)', 'Hay que avisar cuando la tiene otro');
requiresText($inbox, 'force: true', 'El supervisor necesita salida de emergencia');
requiresText($inbox, 'el.btnHuman.addEventListener("click", takeConversation)', 'El botón debe reclamar, no solo cambiar el modo');
requiresText($view, 'data-scope="mine"', 'Falta el filtro "Mis chats"');
// El handoff del bot hace `set_mode(HUMAN)` SIN asignar a nadie. Si el botón se
// ocultara con solo mirar el modo, el chat más urgente del panel —el de la cola
// de atención— sería el único que no se podría reclamar.
requiresText($inbox, 'el.btnHuman.hidden = mine', 'El botón solo se oculta si el chat ya es tuyo');
// Tomarla la saca de la franja: una alarma que sigue sonando por un chat que un
// compañero ya tiene abierto se ignora, y entonces deja de avisar de los de verdad.
requiresText($inbox, 'setMode("HUMAN", { human_support: false })', 'Tomar el chat debe sacarlo de la cola de atención');
// Escribir es tomar el chat: si además hay que pulsar un botón, la asignación se
// queda vacía y el módulo entero no sirve para nada.
requiresText($inbox, "if (!lastThread?.conv?.assigned && lastThread?.conv?.id === convId)", 'Responder debe reclamar el chat');

// ── 3. ventana de servicio de WhatsApp ──────────────────────────────────────
requiresText($repository, 'const SERVICE_WINDOW_HOURS = 24', 'La ventana debe estar en un solo sitio');
requiresText($repository, 'function serviceWindow', 'Falta el cálculo de la ventana');
requiresText($repository, "direction_message = 'inbound'", 'Solo los mensajes del cliente reabren la ventana');
// Sin ningún mensaje entrante no hay evidencia, y sin evidencia no se le quita
// al equipo la posibilidad de escribir.
requiresText($repository, "'known' => false", 'Sin datos no se bloquea');
requiresText($api, "\$window['known'] && !\$window['open']", 'El servidor debe frenar el envío fuera de ventana');
requiresText($api, 'Repository::SERVICE_WINDOW_HOURS', 'El mensaje de error debe citar la ventana real');
requiresText($inbox, 'function windowIsClosed', 'El panel debe cortar antes de enviar');
requiresText($inbox, 'if (windowIsClosed())', 'El envío debe cortarse con el texto aún en el composer');
requiresText($view, 'window-banner', 'Falta el aviso de ventana cerrada');

// ── 4. notas internas ───────────────────────────────────────────────────────
requiresText($repository, 'function addNote', 'Falta la escritura de notas');
requiresText($repository, 'function listNotes', 'Falta el listado de notas');
// Todo lo que entra en `crm_messages` es candidato a salir por la Cloud API: el
// outbox lee de ahí y el agente compone desde ahí. Una nota interna que comparta
// tabla con los mensajes está a un bug de acabar en el teléfono del cliente.
forbidsText(
    substr($repository, (int) strpos($repository, 'function addNote'), 900),
    'crm_messages',
    'Las notas NUNCA pueden tocar la tabla de mensajes'
);
requiresText($api, '#^/conversations/(\d+)/notes$#', 'Falta el endpoint de notas');
requiresText($view, 'solo las ve el equipo', 'La UI debe dejar claro que la nota no sale a WhatsApp');
requiresText($inbox, 'renderNotes([])', 'Al cambiar de chat hay que limpiar las notas del anterior');

// ── 5. seguimientos ─────────────────────────────────────────────────────────
requiresText($repository, 'function addFollowup', 'Falta programar seguimientos');
requiresText($repository, 'function listDueFollowups', 'Un recordatorio que nadie mira no sirve de nada');
requiresText($repository, 'const FOLLOWUP_STATUSES', 'Los estados válidos van en un solo sitio');
requiresText($repository, 'function setFollowupStatus', 'Falta cerrar/reabrir el seguimiento');
requiresText($repository, "'pendiente'", 'Reabrir debe ser posible: cerrar por error no puede ser definitivo');
requiresText($api, "\$path === '/followups' && \$method === 'GET'", 'Falta la lista global para el rail');
requiresText($api, 'Repository::FOLLOWUP_STATUSES', 'El endpoint debe validar el estado');
requiresText($view, 'followup-rail', 'Falta el rail de seguimientos vencidos');
requiresText($inbox, 'loadDueFollowups', 'El rail necesita su propio refresco');
requiresText($inbox, 'setInterval(loadDueFollowups', 'Los seguimientos vencen por tiempo, no por mensajes');
requiresText($css, '.followup-rail', 'Falta el estilo del rail');

// Aislamiento por tenant: cada consulta nueva tiene que filtrarlo, como el resto.
foreach (['listNotes', 'listFollowups', 'listDueFollowups'] as $metodo) {
    $inicio = (int) strpos($repository, "function {$metodo}");
    $cuerpo = substr($repository, $inicio, 800);
    requiresText($cuerpo, 'tenantId', "{$metodo} debe aislar por tenant");
}

// El agente se llama Don Regalo: basta con que el nombre viejo quede en un sitio
// para que el panel y el bot se contradigan delante del cliente.
forbidsText($view, 'Regalito', "Quedó 'Regalito' en la vista del inbox");

echo "asesor modulos contract: OK\n";
