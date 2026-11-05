<?php

declare(strict_types=1);

require dirname(__DIR__) . '/src/MetaAdsSnapshot.php';

$row = [
    'id' => '12345', 'name' => 'Anuncio de prueba', 'campaign_id' => '678',
    'campaign_name' => 'Campaña', 'adset_id' => '789', 'adset_name' => 'Grupo',
    'date_start' => '2026-11-01', 'date_stop' => '2026-11-01',
    'amount_spent' => ['value' => '12.34', 'unit' => 'PEN'],
    'impressions' => '100', 'clicks' => '4',
];
$base = ['account_id' => '1149788199387012', 'from' => '2026-11-01',
    'to' => '2026-11-02', 'complete' => true, 'rows' => [$row]];
$normalized = MetaAdsSnapshot::normalize($base, '1149788199387012');
if ($normalized['rows'][0]['spend'] !== '12.34' || $normalized['rows'][0]['clicks'] !== 4) {
    throw new RuntimeException('La lectura válida cambió sus métricas.');
}
$cases = [
    ['account_id' => '999'],
    ['complete' => false],
    ['rows' => [$row, $row]],
    ['rows' => [array_replace($row, ['amount_spent' => ['value' => '12', 'unit' => 'USD']])]],
    ['rows' => [array_replace($row, ['date_start' => '2026-11-99', 'date_stop' => '2026-11-99'])]],
];
foreach ($cases as $change) {
    try {
        MetaAdsSnapshot::normalize(array_replace($base, $change), '1149788199387012');
    } catch (InvalidArgumentException $e) {
        continue;
    }
    throw new RuntimeException('La importación aceptó una lectura insegura.');
}
echo "meta ads snapshot contract: OK\n";
