<?php

declare(strict_types=1);

/** Importa exclusivamente datos de lectura del conector de Meta Ads. */
final class MetaAdsSnapshot
{
    public static function normalize(array $payload, string $expectedAccount): array
    {
        $account = (string) ($payload['account_id'] ?? '');
        $from = (string) ($payload['from'] ?? '');
        $to = (string) ($payload['to'] ?? '');
        $rows = $payload['rows'] ?? null;
        if ($account === '' || $account !== $expectedAccount || !ctype_digit($account)) {
            throw new InvalidArgumentException('Cuenta de Meta no autorizada.');
        }
        foreach ([$from, $to] as $date) {
            $parsed = DateTimeImmutable::createFromFormat('!Y-m-d', $date);
            if (!$parsed || $parsed->format('Y-m-d') !== $date) {
                throw new InvalidArgumentException('Rango de fechas inválido.');
            }
        }
        if ($from > $to || !is_array($rows) || count($rows) > 20000 || ($payload['complete'] ?? false) !== true) {
            throw new InvalidArgumentException('La lectura debe estar completa y tener hasta 20000 filas.');
        }
        $normalized = [];
        $seen = [];
        foreach ($rows as $row) {
            if (!is_array($row)) {
                throw new InvalidArgumentException('Fila de Meta inválida.');
            }
            $adId = (string) ($row['id'] ?? '');
            $date = (string) ($row['date_start'] ?? '');
            $parsedDate = DateTimeImmutable::createFromFormat('!Y-m-d', $date);
            if (!ctype_digit($adId) || !$parsedDate || $parsedDate->format('Y-m-d') !== $date
                || $date < $from || $date > $to || $date !== (string) ($row['date_stop'] ?? '')) {
                throw new InvalidArgumentException('Anuncio o fecha de Meta inválidos.');
            }
            $key = $adId . ':' . $date;
            if (isset($seen[$key])) {
                throw new InvalidArgumentException('La lectura contiene un día duplicado para un anuncio.');
            }
            $seen[$key] = true;
            $money = $row['amount_spent'] ?? null;
            if (!is_array($money) || ($money['unit'] ?? '') !== 'PEN') {
                throw new InvalidArgumentException('El gasto debe venir en PEN.');
            }
            $spend = self::nonNegativeNumber($money['value'] ?? null);
            $impressions = self::nonNegativeInteger($row['impressions'] ?? null);
            $clicks = self::nonNegativeInteger($row['clicks'] ?? null);
            $normalized[] = [
                'ad_id' => $adId,
                'date' => $date,
                'ad_name' => self::shortText($row['name'] ?? '', 255),
                'adset_id' => self::numericId($row['adset_id'] ?? ''),
                'adset_name' => self::shortText($row['adset_name'] ?? '', 255),
                'campaign_id' => self::numericId($row['campaign_id'] ?? ''),
                'campaign_name' => self::shortText($row['campaign_name'] ?? '', 255),
                'spend' => $spend,
                'impressions' => $impressions,
                'clicks' => $clicks,
            ];
        }
        return ['account_id' => $account, 'from' => $from, 'to' => $to, 'rows' => $normalized];
    }

    private static function numericId($value): string
    {
        $id = (string) $value;
        if ($id !== '' && !ctype_digit($id)) {
            throw new InvalidArgumentException('ID de Meta inválido.');
        }
        return $id;
    }

    private static function shortText($value, int $limit): string
    {
        if (!is_string($value)) {
            throw new InvalidArgumentException('Nombre de Meta inválido.');
        }
        return mb_substr($value, 0, $limit);
    }

    private static function nonNegativeNumber($value): string
    {
        if (!is_scalar($value) || !is_numeric((string) $value) || (float) $value < 0 || !is_finite((float) $value)) {
            throw new InvalidArgumentException('Gasto de Meta inválido.');
        }
        return number_format((float) $value, 2, '.', '');
    }

    private static function nonNegativeInteger($value): int
    {
        if (!is_scalar($value) || !preg_match('/^\d+$/', (string) $value)) {
            throw new InvalidArgumentException('Métrica de Meta inválida.');
        }
        return (int) $value;
    }

    public static function import(array $payload, string $expectedAccount): int
    {
        $data = self::normalize($payload, $expectedAccount);
        $tenant = Repository::ensureTenantId();
        $pdo = Database::pdo();
        $pdo->beginTransaction();
        try {
            Database::exec(
                'DELETE FROM crm_meta_ads_daily WHERE id_tenant = :tenant AND account_id = :account',
                ['tenant' => $tenant, 'account' => $data['account_id']]
            );
            $stmt = $pdo->prepare(
                'INSERT INTO crm_meta_ads_daily
                 (id_tenant, account_id, ad_id, report_date, ad_name, adset_id, adset_name, campaign_id, campaign_name, spend, impressions, clicks)
                 VALUES (:tenant, :account, :ad_id, :date, :ad_name, :adset_id, :adset_name, :campaign_id, :campaign_name, :spend, :impressions, :clicks)'
            );
            foreach ($data['rows'] as $row) {
                $stmt->execute(['tenant' => $tenant, 'account' => $data['account_id']] + $row);
            }
            Database::exec(
                'INSERT INTO crm_meta_ads_sync (id_tenant, account_id, date_from, date_to, synced_at, row_count)
                 VALUES (:tenant, :account, :from, :to, NOW(), :count)
                 ON DUPLICATE KEY UPDATE date_from = VALUES(date_from), date_to = VALUES(date_to), synced_at = VALUES(synced_at), row_count = VALUES(row_count)',
                ['tenant' => $tenant, 'account' => $data['account_id'], 'from' => $data['from'], 'to' => $data['to'], 'count' => count($data['rows'])]
            );
            $pdo->commit();
        } catch (Throwable $e) {
            $pdo->rollBack();
            throw $e;
        }
        return count($data['rows']);
    }
}
