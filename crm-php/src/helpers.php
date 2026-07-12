<?php

declare(strict_types=1);

function view(string $name, array $vars = []): void
{
    extract($vars, EXTR_SKIP);
    $user = Auth::user();
    $appName = Auth::config()['app_name'] ?? 'Don Regalo CRM';
    $base = base_path();
    require dirname(__DIR__) . '/views/layout.php';
}

function e(?string $s): string
{
    return htmlspecialchars((string) $s, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

/** Prefijo URL (ej. /crm/public). Sin barra final, o '' si docroot = public/. */
function base_path(string $suffix = ''): string
{
    $base = rtrim((string) (Auth::config()['base_path'] ?? ''), '/');
    if ($suffix === '' || $suffix === '/') {
        return $base === '' ? '' : $base;
    }
    return $base . '/' . ltrim($suffix, '/');
}

function url_to(string $path = ''): string
{
    $full = base_path($path);
    return $full === '' ? '/' : $full;
}
