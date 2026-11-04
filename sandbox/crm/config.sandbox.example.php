<?php
/**
 * Configuración del borrador. Cópiala a config.php (ignorado por git).
 *
 * `agent_base_url` apunta a un puerto sin uso a propósito: lo que escriba un
 * asesor se queda en `crm_outbox` y nunca llega a WhatsApp. No pongas aquí la URL
 * del agente ni los tokens de producción: este panel le escribiría a clientes reales.
 */
return [
    'db' => [
        'host' => '127.0.0.1',
        'port' => 3307,
        'name' => 'donregalo_crm_sandbox',
        'user' => 'root',
        'pass' => '',
        'charset' => 'utf8mb4',
    ],
    // Con el Apache de XAMPP. Debe coincidir con RewriteBase en public/.htaccess.
    'base_path' => '/agente-don-regalo/sandbox/crm/public',
    'tenant_slug' => 'don-regalo',
    'crm_internal_token' => 'sandbox-crm-token',
    'agent_base_url' => 'http://127.0.0.1:9',
    'agent_internal_token' => 'sandbox-agent-token',
    'catalog_api_base' => '',
    'session_name' => 'dr_crm_sandbox',
    'app_name' => 'Don Regalo CRM (sandbox)',
    'timezone' => 'America/Lima',
];
