<?php

declare(strict_types=1);

/** Cliente servidor-a-servidor para la telemetría segura del agente. */
final class OperationsClient
{
    public static function fetch(array $config): array
    {
        $base = rtrim((string) ($config['agent_base_url'] ?? ''), '/');
        $token = trim((string) ($config['agent_internal_token'] ?? ''));
        $fetchedAt = gmdate('c');

        if (
            $base === ''
            || strpos($base, 'XXXX.easypanel.host') !== false
            || $token === ''
            || $token === 'cambia-este-token-agente'
        ) {
            return [
                'reachable' => false,
                'status' => 'not_configured',
                'error' => 'Configura agent_base_url y agent_internal_token.',
                'fetched_at' => $fetchedAt,
                'data' => null,
            ];
        }
        if (!function_exists('curl_init')) {
            return [
                'reachable' => false,
                'status' => 'curl_unavailable',
                'error' => 'La extensión cURL no está disponible.',
                'fetched_at' => $fetchedAt,
                'data' => null,
            ];
        }

        $ch = curl_init($base . '/internal/operations');
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 2,
            CURLOPT_TIMEOUT => 5,
            CURLOPT_HTTPHEADER => [
                'Accept: application/json',
                'X-Agent-Token: ' . $token,
            ],
        ]);
        $body = curl_exec($ch);
        $curlError = curl_error($ch);
        $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($body === false || $status < 200 || $status >= 300) {
            return [
                'reachable' => false,
                'status' => 'unavailable',
                'http_status' => $status,
                'error' => $curlError !== ''
                    ? $curlError
                    : 'El agente respondió HTTP ' . $status . '.',
                'fetched_at' => $fetchedAt,
                'data' => null,
            ];
        }

        $decoded = json_decode((string) $body, true);
        if (!is_array($decoded)) {
            return [
                'reachable' => false,
                'status' => 'invalid_response',
                'http_status' => $status,
                'error' => 'El agente no devolvió JSON válido.',
                'fetched_at' => $fetchedAt,
                'data' => null,
            ];
        }

        return [
            'reachable' => true,
            'status' => 'ok',
            'http_status' => $status,
            'error' => null,
            'fetched_at' => $fetchedAt,
            'data' => $decoded,
        ];
    }
}
