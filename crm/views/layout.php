<?php
/** @var string $appName */
/** @var array|null $user */
/** @var string $name */
/** @var string $base */
$contentFile = dirname(__DIR__) . '/views/' . $name . '.php';
$isLogin = $name === 'login';
$showChrome = $user && !$isLogin;
$userName = (string) ($user['name'] ?? '');

// Trazos de los iconos: texto fijo de este archivo, nunca datos de usuario.
$navItems = [
    ['inbox', url_to('/'), 'Inbox', '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path>'],
    ['sales-history', url_to('sales-history.php'), 'Historial de ventas', '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"></path><line x1="3" y1="6" x2="21" y2="6"></line><path d="M16 10a4 4 0 0 1-8 0"></path>'],
    ['reports', url_to('reports.php'), 'Reportes', '<line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line>'],
    ['campaigns', url_to('campaigns.php'), 'Campañas', '<path d="m3 11 18-5v12L3 14v-3z"></path><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"></path>'],
    ['opportunities', url_to('opportunities.php'), 'Oportunidades', '<circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle>'],
    ['competition', url_to('competition.php'), 'Competencia', '<circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline>'],
    ['content', url_to('content.php'), 'Crear contenido', '<path d="M12 20h9"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>'],
    ['operations', url_to('operaciones.php'), 'Operaciones', '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>'],
    ['settings', url_to('settings.php'), 'Ajustes', '<line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line>'],
];
if (!Auth::canManage()) {
    $navItems = array_values(array_filter($navItems, static function (array $item): bool {
        return $item[0] !== 'settings' && $item[0] !== 'operations';
    }));
}
$navGroups = [
    'inbox' => 'Atención',
    'reports' => 'Análisis',
    'content' => 'Herramientas',
    'operations' => 'Administración',
];
$currentLabel = 'Don Regalo';
foreach ($navItems as $item) {
    if ($item[0] === $name) {
        $currentLabel = $item[2];
    }
}
?><!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title><?= e($appName) ?></title>
  <link rel="stylesheet" href="<?= e(url_to('assets/app.css')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/app.css') ?>" />
<?php if ($showChrome): ?>
  <?php /* Va aquí y no en sidebar.js: el modo debe estar puesto antes de pintar, o
           el lateral se abriría y se cerraría de golpe en cada cambio de página.
           Por defecto es simple, para dejarle el ancho al trabajo; solo arranca
           ampliado si el asesor lo eligió, y nunca en pantallas medianas, donde
           ampliarlo lo pone encima del contenido. La clave se llama sidebarMode y
           no sidebar para no heredar el "ampliado" guardado cuando ese era el modo
           normal. */ ?>
  <script>
    (function () {
      var mode = "simple";
      if (window.innerWidth >= 1200) {
        try {
          if (localStorage.getItem("dr.sidebarMode") === "full") mode = "full";
        } catch (e) { /* sin localStorage: el modo por defecto */ }
      }
      document.documentElement.setAttribute("data-sidebar", mode);
    })();
  </script>
<?php endif; ?>
</head>
<body class="page-<?= e(preg_replace('/[^a-z0-9_-]/i', '', $name) ?: 'app') ?>">
<?php if ($showChrome): ?>
<div class="app-shell">
  <aside class="sidebar" id="app-sidebar" aria-label="Navegación principal">
    <div class="sidebar-head">
      <div class="sidebar-brand">
        <div class="sidebar-logo">
          <?= brand_mark('topbar') ?>
          <div class="brand-kicker">CRM asesores</div>
        </div>
        <div class="sidebar-gift" role="img" aria-label="Don Regalo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="20 12 20 22 4 22 4 12"></polyline>
            <rect x="2" y="7" width="20" height="5"></rect>
            <line x1="12" y1="22" x2="12" y2="7"></line>
            <path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"></path>
            <path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"></path>
          </svg>
        </div>
      </div>
      <button type="button" class="sidebar-toggle" id="sidebar-toggle" aria-controls="app-sidebar">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <polyline points="11 17 6 12 11 7"></polyline>
          <polyline points="18 17 13 12 18 7"></polyline>
        </svg>
      </button>
    </div>

    <nav class="sidebar-nav">
      <?php foreach ($navItems as [$itemName, $href, $label, $paths]): ?>
        <?php if (isset($navGroups[$itemName])): ?>
          <span class="sidebar-group"><?= e($navGroups[$itemName]) ?></span>
        <?php endif; ?>
        <a class="sidebar-link" href="<?= e($href) ?>" title="<?= e($label) ?>" aria-label="<?= e($label) ?>"<?= $name === $itemName ? ' aria-current="page"' : '' ?>>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><?= $paths ?></svg>
          <span class="sidebar-label"><?= e($label) ?></span>
        </a>
      <?php endforeach; ?>
    </nav>

    <div class="sidebar-foot">
      <div class="avatar-sm" title="<?= e($userName) ?>"><?= e(initials($userName, 'DR')) ?></div>
      <span class="who"><?= e($userName) ?></span>
      <a class="icon-btn" href="<?= e(url_to('logout.php')) ?>" title="Cerrar sesión" aria-label="Cerrar sesión">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
          <polyline points="16 17 21 12 16 7"></polyline>
          <line x1="21" y1="12" x2="9" y2="12"></line>
        </svg>
      </a>
    </div>
  </aside>
  <div class="sidebar-backdrop" id="sidebar-backdrop"></div>

  <div class="app-main">
    <?php /* Solo en móvil: el lateral es un panel que se abre desde aquí. Dentro de un
             chat esta barra desaparece y la cabecera del contacto ocupa su lugar. */ ?>
    <header class="topbar">
      <button type="button" class="icon-btn sidebar-open" id="sidebar-open" aria-label="Abrir menú" aria-controls="app-sidebar" aria-expanded="false">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true">
          <line x1="4" y1="6" x2="20" y2="6"></line>
          <line x1="4" y1="12" x2="20" y2="12"></line>
          <line x1="4" y1="18" x2="20" y2="18"></line>
        </svg>
      </button>
      <span class="topbar-title"><?= e($currentLabel) ?></span>
      <?= brand_mark('topbar') ?>
    </header>
<?php require $contentFile; ?>
  </div>
</div>
<script src="<?= e(url_to('assets/sidebar.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/sidebar.js') ?>"></script>
<?php else: ?>
<?php require $contentFile; ?>
<?php endif; ?>
</body>
</html>
