<?php

declare(strict_types=1);

/**
 * Llamadas servidor-a-servidor del CRM al agente (`/internal/*`).
 *
 * Existe aparte de `OperationsClient` porque aquel devuelve un sobre pensado
 * para pintar un dashboard —"reachable", "fetched_at"— y aquí hace falta lo
 * contrario: o hay datos, o hay una excepción con un motivo que el asesor pueda
 * leer. Un buscador que devuelve una lista vacía cuando en realidad el agente
 * está caído es la peor de las respuestas: parece que el catálogo no tiene ese
 * producto.
 *
 * El token viaja en `X-Agent-Token` y NUNCA sale al navegador: el panel habla
 * con el CRM por sesión y es el CRM quien habla con el agente.
 */
final class AgentClient
{
    /** El LLM tarda; el buscador no debería. Cada llamada dice cuánto espera. */
    public static function get(string $path, array $query = [], int $timeout = 15): array
    {
        $url = self::baseUrl() . $path;
        if ($query) {
            $url .= '?' . http_build_query($query);
        }
        return self::request('GET', $url, null, $timeout);
    }

    public static function post(string $path, array $body, int $timeout = 45): array
    {
        return self::request('POST', self::baseUrl() . $path, $body, $timeout);
    }

    private static function baseUrl(): string
    {
        $config = Auth::config();
        $base = rtrim((string) ($config['agent_base_url'] ?? ''), '/');
        if ($base === '' || strpos($base, 'XXXX.easypanel.host') !== false) {
            throw new RuntimeException(
                'El agente no está configurado en config.php (agent_base_url).'
            );
        }
        return $base;
    }

    private static function request(string $method, string $url, ?array $body, int $timeout): array
    {
        $config = Auth::config();
        $token = trim((string) ($config['agent_internal_token'] ?? ''));
        if ($token === '' || $token === 'cambia-este-token-agente') {
            throw new RuntimeException(
                'Falta agent_internal_token en config.php: el CRM no puede hablar con el agente.'
            );
        }
        if (!function_exists('curl_init')) {
            throw new RuntimeException('La extensión cURL no está disponible en el hosting.');
        }

        $headers = ['Accept: application/json', 'X-Agent-Token: ' . $token];
        $options = [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_CONNECTTIMEOUT => 5,
            CURLOPT_TIMEOUT => $timeout,
            CURLOPT_CUSTOMREQUEST => $method,
        ];
        if ($body !== null) {
            $options[CURLOPT_POSTFIELDS] = json_encode($body, JSON_UNESCAPED_UNICODE);
            $headers[] = 'Content-Type: application/json';
        }
        $options[CURLOPT_HTTPHEADER] = $headers;

        $ch = curl_init($url);
        curl_setopt_array($ch, $options);
        $raw = curl_exec($ch);
        $curlError = curl_error($ch);
        $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($raw === false) {
            throw new RuntimeException('No se pudo contactar al agente: ' . $curlError);
        }
        $decoded = json_decode((string) $raw, true);
        if ($status < 200 || $status >= 300) {
            // El `detail` de FastAPI trae el motivo real (p. ej. que OpenAI está
            // caído). Enseñarlo evita que el asesor reintente diez veces contra
            // un error que no depende de él.
            $detalle = is_array($decoded) ? (string) ($decoded['detail'] ?? '') : '';
            throw new RuntimeException(
                'El agente respondió HTTP ' . $status . ($detalle !== '' ? ': ' . $detalle : '')
            );
        }
        if (!is_array($decoded)) {
            throw new RuntimeException('El agente no devolvió JSON válido.');
        }
        return $decoded;
    }
}
