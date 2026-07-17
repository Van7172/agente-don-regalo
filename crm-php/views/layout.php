<?php
/** @var string $appName */
/** @var array|null $user */
/** @var string $name */
/** @var string $base */
$contentFile = dirname(__DIR__) . '/views/' . $name . '.php';
$isLogin = $name === 'login';
$showChrome = $user && !$isLogin;
$userName = (string) ($user['name'] ?? '');
?><!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title><?= e($appName) ?></title>
  <link rel="stylesheet" href="<?= e(url_to('assets/app.css')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/app.css') ?>" />
</head>
<body class="page-<?= e(preg_replace('/[^a-z0-9_-]/i', '', $name) ?: 'app') ?>">
<?php if ($showChrome): ?>
<div class="app-shell">
  <header class="topbar">
    <div class="topbar-brand">
      <?= brand_mark('topbar') ?>
      <div>
        <?php if (brand_logo_src() === null): ?>
          <div class="brand-name">Don Regalo</div>
        <?php endif; ?>
        <div class="brand-kicker">CRM asesores</div>
      </div>
    </div>

    <nav class="topbar-nav">
      <a href="<?= e(url_to('/')) ?>"<?= $name === 'inbox' ? ' aria-current="page"' : '' ?>>Inbox</a>
      <a href="<?= e(url_to('sales-history.php')) ?>"<?= $name === 'sales-history' ? ' aria-current="page"' : '' ?>>Historial de ventas</a>
      <a href="<?= e(url_to('reports.php')) ?>"<?= $name === 'reports' ? ' aria-current="page"' : '' ?>>Reportes</a>
    </nav>

    <div class="topbar-user">
      <div class="avatar-sm"><?= e(initials($userName, 'DR')) ?></div>
      <span class="who"><?= e($userName) ?></span>
      <a class="icon-btn" href="<?= e(url_to('logout.php')) ?>" title="Cerrar sesión" aria-label="Cerrar sesión">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
          <polyline points="16 17 21 12 16 7"></polyline>
          <line x1="21" y1="12" x2="9" y2="12"></line>
        </svg>
      </a>
    </div>
  </header>
<?php require $contentFile; ?>
</div>
<?php else: ?>
<?php require $contentFile; ?>
<?php endif; ?>
</body>
</html>
