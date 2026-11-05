<?php
declare(strict_types=1);

require dirname(__DIR__) . '/src/Auth.php';
require dirname(__DIR__) . '/src/Repository.php';

$_SESSION['crm_user'] = ['role' => 'Vendedor'];
if (Auth::canManage()) {
    throw new RuntimeException('Un vendedor no debe administrar ajustes ni exportaciones.');
}
$_SESSION['crm_user'] = ['role' => 'Administrador'];
if (!Auth::canManage()) {
    throw new RuntimeException('El administrador real debe conservar el acceso.');
}

$filters = Repository::parseConversationFilters([
    'q' => 'María', 'atencion' => 'antigua', 'etapa' => 'cotizado',
]);
if ($filters['q'] !== 'María' || $filters['atencion'] !== 'antigua') {
    throw new RuntimeException('La búsqueda y la cola antigua deben pasar al servidor.');
}
$method = new ReflectionMethod(Repository::class, 'conversationFilterSql');
[$sql, $params] = $method->invoke(null, $filters);
if (strpos($sql, 'ct.nombre_contact LIKE :searchName') === false
    || strpos($sql, "c.human_support = 1") === false
    || strpos($sql, 'INTERVAL 7 DAY') === false
    || ($params['searchName'] ?? '') !== '%María%') {
    throw new RuntimeException('Los filtros no encuentran chats antiguos en la base.');
}

echo "crm_improvements_contract OK\n";
