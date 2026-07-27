<?php

declare(strict_types=1);

$repository = (string) file_get_contents(dirname(__DIR__) . '/src/Repository.php');
$javascript = (string) file_get_contents(dirname(__DIR__) . '/public/assets/inbox.js');
$view = (string) file_get_contents(dirname(__DIR__) . '/views/inbox.php');

$start = strpos($repository, 'public static function listConversations');
$end = strpos($repository, 'public static function getConversation', $start ?: 0);
if ($start === false || $end === false) {
    throw new RuntimeException('No se pudo aislar listConversations');
}
$listQuery = substr($repository, $start, $end - $start);

if (strpos($listQuery, 'ORDER BY last_message_at DESC, c.id_conversation DESC') === false) {
    throw new RuntimeException('La bandeja no está ordenada por la última actividad');
}

foreach ([
    'ORDER BY es_nuevo DESC',
    '(s.valor_setting IS NOT NULL) DESC',
    'c.human_support DESC',
] as $oldPriority) {
    if (strpos($listQuery, $oldPriority) !== false) {
        throw new RuntimeException("La prioridad antigua sigue alterando la recencia: {$oldPriority}");
    }
}

if (strpos($listQuery, 'SELECT MAX(activity.fecha_creacion)') === false) {
    throw new RuntimeException('La actividad debe derivarse de los mensajes reales');
}
if (strpos($javascript, 'conversationTimeLabel(c.last_message_at)') === false) {
    throw new RuntimeException('La lista no usa la fecha contextual');
}
if (strpos($view, 'assets/inbox-time.js') === false) {
    throw new RuntimeException('Falta cargar el formateador temporal');
}

echo "conversation recency contract: OK\n";
