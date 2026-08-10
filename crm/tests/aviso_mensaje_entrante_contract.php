<?php

declare(strict_types=1);

/**
 * El panel suena con CADA mensaje del cliente, lleve el chat la IA o un asesor.
 *
 * Antes solo pitaba en dos transiciones (handoff y lead nuevo), así que un
 * equipo que revisa las conversaciones mientras el agente las atiende no se
 * enteraba de nada hasta que el bot se rendía.
 */

$repository = (string) file_get_contents(dirname(__DIR__) . '/src/Repository.php');
$javascript = (string) file_get_contents(dirname(__DIR__) . '/public/assets/inbox.js');

// El dato: la hora del último mensaje ENTRANTE. `last_message_at` no sirve —
// también lo mueven el agente y el asesor, y eso no es "llegó un mensaje".
$start = strpos($repository, 'public static function listConversations');
$end = strpos($repository, 'public static function getConversation', $start ?: 0);
if ($start === false || $end === false) {
    throw new RuntimeException('No se pudo aislar listConversations');
}
$listQuery = substr($repository, $start, $end - $start);
if (strpos($listQuery, "AND entrante.direction_message = 'inbound'") === false) {
    throw new RuntimeException('La lista ya no calcula el último mensaje entrante');
}
if (strpos($repository, "'window' => self::serviceWindow(") === false) {
    throw new RuntimeException('La lista ya no publica la ventana con last_inbound_at');
}

// El aviso corre en cada refresco de la lista, junto a los otros dos.
if (strpos($javascript, 'alertOnInbound(next)') === false) {
    throw new RuntimeException('El refresco de la lista no comprueba mensajes entrantes');
}
if (strpos($javascript, 'c.window?.last_inbound_at') === false) {
    throw new RuntimeException('El aviso no se ancla al último mensaje del cliente');
}

// Marca de agua: sin ella el mismo mensaje pitaría en cada tick (4 s) y el
// equipo silenciaría la pestaña el primer día.
if (strpos($javascript, 'entrantesAvisados') === false) {
    throw new RuntimeException('Falta la marca de agua por conversación');
}
if (strpos($javascript, 'entrantesSembrados') === false) {
    throw new RuntimeException('La primera carga debe sembrar sin avisar');
}

// Un solo pitido por refresco: el primer mensaje de un lead es "entrante",
// "lead nuevo" y a veces "handoff" — tres pitidos pegados suenan a avería.
if (strpos($javascript, 'beepGastado = false;') === false) {
    throw new RuntimeException('El pitido único por refresco no se reinicia');
}
foreach (['notifyHandoff', 'notifyNewLead', 'notifyInbound'] as $aviso) {
    $pos = strpos($javascript, "function {$aviso}(conv)");
    if ($pos === false) {
        throw new RuntimeException("Falta el aviso {$aviso}");
    }
    if (strpos(substr($javascript, $pos, 400), 'beepUnaVez(') === false) {
        throw new RuntimeException("{$aviso} no pasa por el pitido único");
    }
}

echo "aviso mensaje entrante contract: OK\n";
