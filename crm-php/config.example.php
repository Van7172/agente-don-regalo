<?php
/**
 * Copia este archivo a config.php y ajusta valores de producción.
 *
 * Opciones de publicación:
 * A) Subdominio crm.donregalo.pe → document root = public/ → base_path = ''
 * B) Carpeta: https://donregalo.pe/crm/public/ → base_path = '/crm/public'
 */
return [
    'db' => [
        'host' => '127.0.0.1',
        'port' => 3306,
        'name' => 'donregal_donregalo2019',
        'user' => 'root',
        'pass' => '',
        'charset' => 'utf8mb4',
    ],
    // Ruta pública hasta /public (sin barra final). Vacío si el docroot es public/
    'base_path' => '/crm/public',
    'tenant_slug' => 'don-regalo',
    // Mismo valor que CRM_INTERNAL_TOKEN del agente (sandbox VPS)
    'crm_internal_token' => 'cambia-este-token-seguro',
    // URL pública del agente en EasyPanel (outbox push)
    'agent_base_url' => 'https://don-regalo-rags-app-agente-sandbox.XXXX.easypanel.host',
    'agent_internal_token' => 'cambia-este-token-agente',
    // Opcional: base catálogo para corroborar reportes
    'catalog_api_base' => 'https://donregalo.pe/clienteApiApp/api',
    'session_name' => 'dr_crm_php',
    'app_name' => 'Don Regalo CRM',
    // Zona del negocio. PHP y MySQL se alinean a esta: si no, las horas del
    // inbox no cuadran con las que el cliente ve en WhatsApp.
    'timezone' => 'America/Lima',
];
