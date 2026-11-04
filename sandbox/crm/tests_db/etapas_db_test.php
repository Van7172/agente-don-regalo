<?php

declare(strict_types=1);

/**
 * Pruebas contra MySQL de etapas, etiquetas y respuestas rápidas.
 *
 *   php sandbox/crm/tests_db/etapas_db_test.php
 *
 * Crea datos y los borra al terminar, así que se niega a correr salvo en una base
 * cuyo nombre termine en `_sandbox`.
 */

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';

if (substr((string) $config['db']['name'], -8) !== '_sandbox') {
    fwrite(STDERR, "Se niega a correr: la base no termina en _sandbox.\n");
    exit(2);
}

$failures = 0;
$checks = 0;
function check(string $name, bool $ok, string $detail = ''): void
{
    global $failures, $checks;
    $checks++;
    if (!$ok) {
        $failures++;
    }
    echo ($ok ? '  ok   ' : '  FAIL ') . $name . ($ok || $detail === '' ? '' : " → {$detail}") . "\n";
}

/** @return string nombre de la clase de la excepción, o '' si no lanzó nada */
function thrown(callable $action): string
{
    try {
        $action();
        return '';
    } catch (Throwable $error) {
        return get_class($error);
    }
}

function stageOf(int $id): ?string
{
    return Database::fetchOne(
        'SELECT etapa_conversation AS e FROM crm_conversations WHERE id_conversation = :id',
        ['id' => $id]
    )['e'];
}

function conversation(string $waId): int
{
    return Repository::ensureInboundConversation($waId, 'Prueba ' . $waId)['conversationId'];
}

function sale(string $product, int $closedAt): array
{
    return ['producto' => $product, 'cerrada_en' => $closedAt, 'monto_sol' => 100, 'origen' => 'bot'];
}

function ageStage(int $id, int $days): void
{
    Database::exec(
        'UPDATE crm_conversations
         SET fecha_etapa_conversation = DATE_SUB(NOW(), INTERVAL ' . $days . ' DAY)
         WHERE id_conversation = :id',
        ['id' => $id]
    );
}

$prefix = '5190' . random_int(1000000, 9999999);
$tenantId = Repository::ensureTenantId();
$cleanup = static function () use ($prefix, $tenantId): void {
    $like = $prefix . '%';
    Database::exec(
        'DELETE v FROM crm_ventas_historiales v WHERE v.wa_id_venta_historial LIKE :w',
        ['w' => $like]
    );
    Database::exec(
        'DELETE s FROM crm_settings s
         JOIN crm_conversations c ON s.llave_setting = CONCAT(\'sale_\', c.id_conversation)
         JOIN crm_contacts ct ON ct.id_contact = c.id_contact
         WHERE ct.wa_id LIKE :w',
        ['w' => $like]
    );
    Database::exec(
        'DELETE c FROM crm_conversations c
         JOIN crm_contacts ct ON ct.id_contact = c.id_contact
         WHERE ct.wa_id LIKE :w',
        ['w' => $like]
    );
    Database::exec('DELETE FROM crm_contacts WHERE wa_id LIKE :w', ['w' => $like]);
    Database::exec(
        'DELETE FROM crm_etiquetas WHERE id_tenant = :t AND nombre_etiqueta LIKE :n',
        ['t' => $tenantId, 'n' => 'prueba-%']
    );
    Database::exec(
        'DELETE FROM crm_respuestas_rapidas WHERE id_tenant = :t AND comando_respuesta LIKE :c',
        ['t' => $tenantId, 'c' => 'prueba-%']
    );
};

$exitCode = 1;
try {
    echo "Etapas\n";
    $a = conversation($prefix . '1');
    check('una conversación nueva nace en Nuevo', stageOf($a) === 'nuevo');
    check(
        'y deja fila de historial',
        (int) Database::fetchOne(
            'SELECT COUNT(*) n FROM crm_etapas_historial WHERE id_conversation = :id AND etapa_nueva = \'nuevo\'',
            ['id' => $a]
        )['n'] === 1
    );

    check('Vendido a mano se rechaza', thrown(static function () use ($a) {
        Repository::setConversationStage($a, 'vendido', null, 1);
    }) === 'InvalidArgumentException');
    check('Entregado a mano se rechaza', thrown(static function () use ($a) {
        Repository::setConversationStage($a, 'entregado', null, 1);
    }) === 'InvalidArgumentException');
    check('Perdido sin motivo se rechaza', thrown(static function () use ($a) {
        Repository::setConversationStage($a, 'perdido', null, 1);
    }) === 'InvalidArgumentException');
    check('Perdido con motivo inventado se rechaza', thrown(static function () use ($a) {
        Repository::setConversationStage($a, 'perdido', 'nose', 1);
    }) === 'InvalidArgumentException');

    Repository::setConversationStage($a, 'cotizado', null, 1, 'Ana Prueba');
    check('Cotizado a mano funciona', stageOf($a) === 'cotizado');
    $hist = Database::fetchOne(
        'SELECT * FROM crm_etapas_historial WHERE id_conversation = :id ORDER BY id_etapa_historial DESC LIMIT 1',
        ['id' => $a]
    );
    check(
        'el historial dice de dónde venía y quién fue',
        $hist['etapa_anterior'] === 'nuevo' && $hist['nombre_usuario'] === 'Ana Prueba'
    );

    $closedAt = time() - 100;
    Repository::storeActiveSale($a, sale('Desayuno', $closedAt));
    check('registrar una venta mueve a Vendido', stageOf($a) === 'vendido');
    check('con venta pendiente no se puede cambiar a mano', thrown(static function () use ($a) {
        Repository::setConversationStage($a, 'perdido', 'precio', 1);
    }) === 'DomainException');

    Repository::markSaleDelivered($a, 1);
    check('confirmar la entrega mueve a Entregado', stageOf($a) === 'entregado');

    $saleId = (int) Database::fetchOne(
        'SELECT id_venta_historial i FROM crm_ventas_historiales WHERE id_conversation = :id',
        ['id' => $a]
    )['i'];
    Repository::setSaleStatus($saleId, 'pendiente', 1);
    check('revertir la entrega vuelve a Vendido', stageOf($a) === 'vendido');
    Repository::setSaleStatus($saleId, 'entregado', 1);
    check('y confirmarla otra vez, a Entregado', stageOf($a) === 'entregado');

    Repository::storeActiveSale($a, sale('Ramo', $closedAt + 50));
    check('una segunda compra manda sobre la anterior', stageOf($a) === 'vendido');
    Repository::setSaleStatus($saleId, 'pendiente', 1);
    check('tocar la venta vieja no mueve la etapa de la nueva', stageOf($a) === 'vendido');

    // Si falla la venta, la etapa no cambia.
    $b = conversation($prefix . '2');
    $falla = thrown(static function () use ($b) {
        Repository::storeActiveSale($b, ['producto' => "bytes rotos \xB1", 'cerrada_en' => time()]);
    });
    check('una venta inválida no cambia la etapa', $falla !== '' && stageOf($b) === 'nuevo', $falla);

    // Y al revés: si falla la etapa, la venta tampoco queda a medias. Se rompe justo
    // después de guardar la venta quitando la tabla que escribe la etapa.
    $countSales = static function (int $id): int {
        return (int) Database::fetchOne(
            'SELECT COUNT(*) n FROM crm_ventas_historiales WHERE id_conversation = :id',
            ['id' => $id]
        )['n'];
    };
    Database::pdo()->exec('RENAME TABLE crm_etapas_historial TO crm_etapas_historial_oculta');
    try {
        $falla = thrown(static function () use ($b) {
            Repository::storeActiveSale($b, sale('Caja', time()));
        });
    } finally {
        Database::pdo()->exec('RENAME TABLE crm_etapas_historial_oculta TO crm_etapas_historial');
    }
    check('si falla la etapa, el error se ve', $falla !== '');
    check('la venta se revierte', $countSales($b) === 0);
    check('la ficha verde también', Repository::getSetting('sale_' . $b) === null);
    check('la etapa sigue igual', stageOf($b) === 'nuevo');
    check('y no deja la transacción abierta', !Database::pdo()->inTransaction());

    echo "Reapertura\n";
    Repository::setConversationStage($b, 'perdido', 'precio', 1);
    ageStage($b, 2);
    check('Perdido de hace 2 días NO se reabre', !Repository::reopenStageOnInbound($b) && stageOf($b) === 'perdido');
    ageStage($b, 8);
    check('Perdido de hace 8 días SÍ se reabre', Repository::reopenStageOnInbound($b) && stageOf($b) === 'nuevo');
    check(
        'y se le borra el motivo',
        Database::fetchOne(
            'SELECT motivo_perdida_conversation m FROM crm_conversations WHERE id_conversation = :id',
            ['id' => $b]
        )['m'] === null
    );
    check('Nuevo no se reabre', !Repository::reopenStageOnInbound($b));

    $c = conversation($prefix . '3');
    Repository::storeActiveSale($c, sale('Peluche', time() - 10));
    ageStage($c, 30);
    check('Vendido no se reabre aunque sea viejo', !Repository::reopenStageOnInbound($c) && stageOf($c) === 'vendido');

    echo "Filtros\n";
    // 85 chats nuevos dejan fuera de los 80 recientes al chat cotizado.
    $old = conversation($prefix . '4');
    Repository::setConversationStage($old, 'cotizado', null, 1);
    Repository::addMessage([
        'conversationId' => $old, 'direction' => 'inbound', 'senderType' => 'contact',
        'role' => 'user', 'content' => 'viejo',
    ]);
    Database::exec(
        'UPDATE crm_messages SET fecha_creacion = DATE_SUB(NOW(), INTERVAL 400 DAY) WHERE id_conversation = :id',
        ['id' => $old]
    );
    Database::exec(
        'UPDATE crm_conversations SET last_message_at = DATE_SUB(NOW(), INTERVAL 400 DAY) WHERE id_conversation = :id',
        ['id' => $old]
    );
    for ($i = 0; $i < 85; $i++) {
        conversation($prefix . '5' . str_pad((string) $i, 2, '0', STR_PAD_LEFT));
    }
    $ids = static function (array $rows): array {
        return array_map(static function (array $row): int {
            return (int) $row['id_conversation'];
        }, $rows);
    };
    check('sin filtro, el chat viejo queda fuera de los 80', !in_array($old, $ids(Repository::listConversations(80)), true));
    check(
        'con filtro de etapa, aparece',
        in_array($old, $ids(Repository::listConversations(200, Repository::parseConversationFilters(['etapa' => 'cotizado']))), true)
    );
    check('un filtro inventado se descarta', Repository::parseConversationFilters(['etapa' => 'x-inventada']) === []);

    echo "Etiquetas\n";
    $tag = Repository::createTag('prueba-Cumpleaños', 'oliva', 1, 'Ana Prueba');
    check('crear etiqueta', $tag > 0);
    check('duplicada con otras mayúsculas se rechaza', thrown(static function () {
        Repository::createTag('PRUEBA-CUMPLEAÑOS', 'azul', 1);
    }) === 'DomainException');
    check('color fuera de la paleta se rechaza', thrown(static function () {
        Repository::createTag('prueba-color', 'rojo-inventado', 1);
    }) === 'InvalidArgumentException');
    $tagAcento = Repository::createTag('prueba-Café', 'azul', 1);
    check('duplicada sin acento se rechaza', thrown(static function () {
        Repository::createTag('prueba-cafe', 'azul', 1);
    }) === 'DomainException');
    check('la etiqueta con acento se creó', $tagAcento > 0);

    $tagged = Repository::addConversationTag($a, $tag, 1);
    check('poner etiqueta', count($tagged) === 1 && $tagged[0]['id'] === $tag);
    check('ponerla dos veces no duplica', count(Repository::addConversationTag($a, $tag, 2)) === 1);
    check(
        'filtrar por etiqueta encuentra el chat',
        $ids(Repository::listConversations(200, ['etiqueta' => $tag])) === [$a]
    );
    $listed = Repository::listConversations(200, ['etiqueta' => $tag]);
    check('la lista trae las etiquetas', ($listed[0]['tags'][0]['name'] ?? '') === 'prueba-Cumpleaños');

    Repository::updateTag($tag, ['active' => false], 1);
    check('una archivada no se ofrece', !in_array($tag, array_column(Repository::listTags(), 'id'), true));
    check('pero el chat la conserva', count(Repository::conversationTags($a)) === 1);
    check('y no se puede poner en otro chat', thrown(static function () use ($b, $tag) {
        Repository::addConversationTag($b, $tag, 1);
    }) === 'InvalidArgumentException');
    check('quitarla', Repository::removeConversationTag($a, $tag) === []);

    echo "Respuestas rápidas\n";
    $reply = Repository::createQuickReply(
        ['cmd' => 'prueba-hora', 'label' => 'Horario', 'body' => "Línea 1\r\nLínea 2 🙏"],
        1,
        'Ana Prueba'
    );
    $row = array_values(array_filter(Repository::listQuickReplies(), static function (array $r) use ($reply) {
        return $r['id'] === $reply;
    }))[0] ?? [];
    check('crear y listar', ($row['cmd'] ?? '') === 'prueba-hora');
    check('el retorno de carro no llega al cliente', ($row['body'] ?? '') === "Línea 1\nLínea 2 🙏");
    check('queda quién la escribió', ($row['updated_by'] ?? '') === 'Ana Prueba');
    foreach (['Con Espacio', 'MAYUS', 'a', 'con/barra'] as $badCmd) {
        check("comando «{$badCmd}» se rechaza", thrown(static function () use ($badCmd) {
            Repository::createQuickReply(['cmd' => $badCmd, 'label' => 'x', 'body' => 'x'], 1);
        }) === 'InvalidArgumentException');
    }
    check('comando repetido se rechaza', thrown(static function () {
        Repository::createQuickReply(['cmd' => 'prueba-hora', 'label' => 'x', 'body' => 'x'], 1);
    }) === 'DomainException');
    check('texto de más de 4096 se rechaza', thrown(static function () {
        Repository::createQuickReply(['cmd' => 'prueba-largo', 'label' => 'x', 'body' => str_repeat('a', 4097)], 1);
    }) === 'InvalidArgumentException');
    Repository::updateQuickReply($reply, ['active' => false], 1);
    check('desactivada no se ofrece', !in_array($reply, array_column(Repository::listQuickReplies(), 'id'), true));
    check('pero sigue en Ajustes', in_array($reply, array_column(Repository::listQuickReplies(true), 'id'), true));

    echo "Embudo\n";
    $today = date('Y-m-d');
    $funnel = Repository::stageFunnel($today, $today);
    $sum = array_sum(array_column($funnel['stages'], 'count')) + $funnel['unclassified'];
    check('las etapas suman el total', $sum === $funnel['total'], "{$sum} vs {$funnel['total']}");
    $stageCounts = array_column($funnel['stages'], 'count', 'code');
    check(
        'el número abre exactamente esos chats',
        Repository::countConversations(['etapa' => 'cotizado', 'from' => $today, 'to' => $today]) === $stageCounts['cotizado']
    );

    $exitCode = $failures === 0 ? 0 : 1;
} catch (Throwable $error) {
    echo "ERROR: {$error->getMessage()} ({$error->getFile()}:{$error->getLine()})\n";
} finally {
    if (Database::pdo()->inTransaction()) {
        Database::pdo()->rollBack();
    }
    $cleanup();
}

echo "\n{$checks} comprobaciones, {$failures} fallos\n";
exit($exitCode);
