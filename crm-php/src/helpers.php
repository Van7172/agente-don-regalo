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

/** Iniciales para los avatares (máx. 2 letras). */
function initials(?string $name, string $fallback = '?'): string
{
    $parts = preg_split('/\s+/u', trim((string) $name)) ?: [];
    $parts = array_values(array_filter($parts, static function ($p) {
        return $p !== '';
    }));
    if (!$parts) {
        return $fallback;
    }
    $letters = '';
    foreach (array_slice($parts, 0, 2) as $p) {
        $letters .= mb_substr($p, 0, 1, 'UTF-8');
    }
    return mb_strtoupper($letters, 'UTF-8');
}

/** Ruta del logo si el archivo existe, con cache-buster. Null si no está. */
function brand_logo_src(): ?string
{
    $file = dirname(__DIR__) . '/public/assets/logo-don-regalo.png';
    if (!is_file($file)) {
        return null;
    }
    return url_to('assets/logo-don-regalo.png') . '?v=' . filemtime($file);
}

/**
 * Marca Don Regalo. Usa public/assets/logo-don-regalo.png si está presente;
 * si no, cae a una marca SVG con los tokens del design system.
 *
 * El logo es un wordmark con mucho margen: en 'topbar' se recorta por CSS a la
 * banda del lettering (recortarlo a un cuadrado dejaría un trozo ilegible).
 */
function brand_mark(string $variant = 'topbar'): string
{
    $src = brand_logo_src();

    if ($src !== null) {
        if ($variant === 'login') {
            return '<img class="brand-mark-lg" src="' . e($src) . '" alt="Don Regalo" />';
        }
        return '<span class="brand-logo"><img src="' . e($src) . '" alt="Don Regalo" /></span>';
    }

    $class = $variant === 'login' ? 'brand-mark-lg' : 'brand-mark';
    $gift = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        . ' stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        . '<polyline points="20 12 20 22 4 22 4 12"></polyline>'
        . '<rect x="2" y="7" width="20" height="5"></rect>'
        . '<line x1="12" y1="22" x2="12" y2="7"></line>'
        . '<path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"></path>'
        . '<path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"></path>'
        . '</svg>';

    return '<div class="' . e($class) . ' is-fallback" role="img" aria-label="Don Regalo">'
        . $gift
        . '<span class="brand-mark-label">Don Regalo</span>'
        . '</div>';
}
