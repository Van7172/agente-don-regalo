<?php
/** @var string $appName */
/** @var array|null $user */
/** @var string $name */
/** @var string $base */
$contentFile = dirname(__DIR__) . '/views/' . $name . '.php';
?><!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title><?= e($appName) ?></title>
  <link rel="stylesheet" href="<?= e(url_to('assets/app.css')) ?>" />
</head>
<body class="page-<?= e(preg_replace('/[^a-z0-9_-]/i', '', $name) ?: 'app') ?>">
<?php if ($user && $name !== 'login'): ?>
<header class="topbar">
  <div class="brand-inline">
    <strong><?= e($appName) ?></strong>
    <span>WhatsApp · asesores</span>
  </div>
  <nav>
    <a href="<?= e(url_to('/')) ?>">Inbox</a>
    <a href="<?= e(url_to('reports.php')) ?>">Reportes</a>
  </nav>
  <div class="userbar">
    <span><?= e($user['name'] ?? '') ?> · <?= e($user['role'] ?? '') ?></span>
    <a class="btn" href="<?= e(url_to('logout.php')) ?>">Salir</a>
  </div>
</header>
<?php endif; ?>
<?php require $contentFile; ?>
</body>
</html>
