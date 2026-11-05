<?php

declare(strict_types=1);

$config = require dirname(__DIR__) . '/bootstrap.php';
require_once dirname(__DIR__) . '/src/helpers.php';
Auth::requireLogin();

$validDate = static function ($value, string $fallback): string {
    $value = is_scalar($value) ? (string) $value : '';
    $parsed = DateTimeImmutable::createFromFormat('!Y-m-d', $value);
    return $parsed && $parsed->format('Y-m-d') === $value ? $value : $fallback;
};
$from = $validDate($_GET['from'] ?? '', date('Y-m-d', strtotime('-89 days')));
$to = $validDate($_GET['to'] ?? '', date('Y-m-d'));
if ($from > $to) {
    [$from, $to] = [$to, $from];
}
$accountId = (string) ($config['meta_ads_account_id'] ?? '1149788199387012');
$sync = Repository::metaAdsSync($accountId);
$metaMap = [];
$campaigns = [];
foreach (Repository::metaAdMap($accountId) as $ad) {
    $metaMap[(string) $ad['ad_id']] = $ad;
    if ($ad['campaign_id'] !== '') {
        $campaigns[(string) $ad['campaign_id']] = (string) $ad['campaign_name'];
    }
}
asort($campaigns, SORT_NATURAL | SORT_FLAG_CASE);
$campaignParam = $_GET['campaign'] ?? '';
$campaignId = is_scalar($campaignParam) ? (string) $campaignParam : '';
if ($campaignId !== '' && $campaignId !== 'unlinked' && !isset($campaigns[$campaignId])) {
    $campaignId = '';
}
$metaStats = [];
foreach (Repository::metaAdPerformance($accountId, $from, $to) as $item) {
    $metaStats[(string) $item['ad_id']] = $item;
}
$includeSource = static function ($sourceId) use ($campaignId, $metaMap): bool {
    if ($campaignId === '') {
        return true;
    }
    $mapped = (string) ($metaMap[(string) $sourceId]['campaign_id'] ?? '');
    return $mapped === ($campaignId === 'unlinked' ? '' : $campaignId);
};

$rows = [];
$seenSourceIds = [];
$totals = ['leads' => 0, 'buyers' => 0, 'ventas' => 0, 'monto' => 0.0,
    'valued_sales' => 0, 'spend' => 0.0, 'clicks' => 0, 'impressions' => 0];
$allCrmPerformance = Repository::campaignPerformance($from, $to);
$attribution = ['total' => 0, 'identified' => 0];
foreach ($allCrmPerformance as $item) {
    $attribution['total'] += (int) $item['leads'];
    if ((string) ($item['ad_source_id'] ?? '') !== '') {
        $attribution['identified'] += (int) $item['leads'];
    }
}
foreach ($allCrmPerformance as $crm) {
    $sourceId = (string) ($crm['ad_source_id'] ?? '');
    if (!$includeSource($sourceId)) {
        continue;
    }
    $meta = $metaMap[$sourceId] ?? null;
    $stats = $metaStats[$sourceId] ?? null;
    $headline = trim((string) ($crm['ad_headline'] ?? ''));
    $label = $sourceId === '' ? 'Sin anuncio identificado'
        : (string) ($meta['ad_name'] ?? ($headline !== '' ? $headline : 'Anuncio ' . $sourceId));
    $rawUrl = (string) ($crm['ad_source_url'] ?? '');
    $safeUrl = filter_var($rawUrl, FILTER_VALIDATE_URL) && preg_match('#^https?://#i', $rawUrl) ? $rawUrl : '';
    $leads = (int) $crm['leads'];
    $buyers = (int) $crm['buyers'];
    $row = [
        'source_id' => $sourceId, 'is_unknown' => $sourceId === '', 'label' => $label,
        'meta_linked' => $meta !== null,
        'campaign' => (string) ($meta['campaign_name'] ?? 'Sin campaña vinculada'),
        'url' => $safeUrl,
        'leads' => $leads, 'buyers' => $buyers, 'ventas' => (int) $crm['ventas'],
        'valued_sales' => (int) $crm['valued_sales'], 'monto' => (float) $crm['monto'],
        'spend' => (float) ($stats['spend'] ?? 0), 'clicks' => (int) ($stats['clicks'] ?? 0),
        'impressions' => (int) ($stats['impressions'] ?? 0),
        'conversion' => $leads > 0 ? round($buyers / $leads * 100, 1) : null,
    ];
    foreach ($totals as $field => &$total) {
        if (isset($row[$field])) {
            $total += $row[$field];
        }
    }
    unset($total);
    $rows[] = $row;
    $seenSourceIds[$sourceId] = true;
}
foreach ($metaStats as $sourceId => $stats) {
    // PHP convierte claves de array numéricas a int aunque el ID de Meta sea texto.
    $sourceId = (string) $sourceId;
    if (isset($seenSourceIds[$sourceId]) || !isset($metaMap[$sourceId]) || !$includeSource($sourceId)) {
        continue;
    }
    $spend = (float) $stats['spend'];
    $clicks = (int) $stats['clicks'];
    $impressions = (int) $stats['impressions'];
    if ($spend === 0.0 && $clicks === 0 && $impressions === 0) {
        continue;
    }
    $meta = $metaMap[$sourceId];
    $row = [
        'source_id' => $sourceId, 'is_unknown' => false, 'label' => (string) $meta['ad_name'],
        'meta_linked' => true,
        'campaign' => (string) $meta['campaign_name'], 'url' => '',
        'leads' => 0, 'buyers' => 0, 'ventas' => 0, 'valued_sales' => 0, 'monto' => 0.0,
        'spend' => $spend, 'clicks' => $clicks, 'impressions' => $impressions, 'conversion' => null,
    ];
    $totals['spend'] += $spend;
    $totals['clicks'] += $clicks;
    $totals['impressions'] += $impressions;
    $rows[] = $row;
}
usort($rows, static function (array $a, array $b): int {
    return ($b['spend'] <=> $a['spend']) ?: (($b['leads'] <=> $a['leads']) ?: strcmp($a['label'], $b['label']));
});

$trend = [];
foreach (Repository::campaignCrmDaily($from, $to) as $daily) {
    if (!$includeSource($daily['ad_source_id'])) {
        continue;
    }
    $date = (string) $daily['date'];
    $trend[$date] = $trend[$date] ?? ['date' => $date, 'chats' => 0, 'buyers' => 0, 'spend' => 0];
    $trend[$date]['chats'] += (int) $daily['chats'];
    $trend[$date]['buyers'] += (int) $daily['buyers'];
}
foreach (Repository::metaDailyPerformance($accountId, $from, $to) as $daily) {
    if ($campaignId !== '' && (string) $daily['campaign_id'] !== ($campaignId === 'unlinked' ? '' : $campaignId)) {
        continue;
    }
    $date = (string) $daily['date'];
    $trend[$date] = $trend[$date] ?? ['date' => $date, 'chats' => 0, 'buyers' => 0, 'spend' => 0];
    $trend[$date]['spend'] += (float) $daily['spend'];
}
ksort($trend);

view('campaigns', compact('from', 'to', 'rows', 'totals', 'sync', 'campaigns', 'campaignId', 'trend', 'attribution'));
