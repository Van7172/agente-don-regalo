<?php
/** @var string $from */
/** @var string $to */
/** @var array $overview */
/** @var array $rows */
/** @var array $daily */

$kpis = [
    ['kicker' => 'Conversaciones', 'value' => (int) $overview['conversations'], 'meta' => 'creadas en el rango'],
    ['kicker' => 'Mensajes', 'value' => (int) $overview['messages'], 'meta' => 'intercambiados'],
    ['kicker' => 'Inbound', 'value' => (int) $overview['inbound_messages'], 'meta' => 'escritos por clientes'],
    ['kicker' => 'Msgs bot', 'value' => (int) $overview['bot_messages'], 'meta' => 'enviados por Don Regalo'],
    ['kicker' => 'Msgs asesor', 'value' => (int) $overview['agent_messages'], 'meta' => 'enviados por humanos'],
    ['kicker' => 'Modo AI', 'value' => (int) $overview['mode_ai'], 'meta' => 'chats con el bot al mando'],
    ['kicker' => 'Modo HUMAN', 'value' => (int) $overview['mode_human'], 'meta' => 'chats tomados'],
    ['kicker' => '% en modo HUMAN', 'value' => $overview['pct_human'] . '%', 'meta' => 'del total de chats'],
    ['kicker' => 'Leads', 'value' => (int) $overview['leads'], 'meta' => 'con interés de compra'],
    ['kicker' => 'Piden ayuda', 'value' => (int) $overview['open_needs_help'], 'meta' => 'abiertas ahora mismo', 'warn' => true],
];

// Geometría del gráfico (mismo viewBox que el diseño).
$chartW = 560;
$chartH = 180;
$padL = 10;
$padR = 10;
$padTop = 16;
$padBottom = 26;

$points = [];
$values = [];
foreach ($daily as $d) {
    $values[] = (int) $d['value'];
}
$maxValue = $values ? max($values) : 0;
$plotMax = $maxValue > 0 ? $maxValue : 1;

if (count($daily) > 1) {
    $step = ($chartW - $padL - $padR) / (count($daily) - 1);
    foreach ($daily as $i => $d) {
        $points[] = [
            'x' => round($padL + $i * $step, 1),
            'y' => round($padTop + (1 - $d['value'] / $plotMax) * ($chartH - $padTop - $padBottom), 1),
            'label' => $d['label'],
            'date' => $d['date'],
            'value' => $d['value'],
        ];
    }
}

$lineCoords = [];
foreach ($points as $p) {
    $lineCoords[] = $p['x'] . ',' . $p['y'];
}
$linePoints = implode(' ', $lineCoords);
$baseY = $chartH - $padBottom;
$areaPoints = $points
    ? $padL . ',' . $baseY . ' ' . $linePoints . ' ' . ($chartW - $padR) . ',' . $baseY
    : '';

// Con muchos días, etiquetar todos amontona el eje.
$labelEvery = max(1, (int) ceil(count($points) / 10));
?>
<div class="reports">
  <h2>Reportes</h2>
  <p class="lead">Resumen de la actividad de Don Regalo y tus asesores.</p>

  <form class="reports-filters" method="get" action="<?= e(url_to('reports.php')) ?>">
    <div class="field">
      <label for="from">Desde</label>
      <input class="input" id="from" type="date" name="from" value="<?= e($from) ?>" />
    </div>
    <div class="field">
      <label for="to">Hasta</label>
      <input class="input" id="to" type="date" name="to" value="<?= e($to) ?>" />
    </div>
    <button type="submit" class="btn btn-primary">Filtrar</button>
  </form>

  <div class="kpi-grid">
    <?php foreach ($kpis as $k): ?>
      <div class="card elev-sm kpi-card<?= !empty($k['warn']) ? ' is-warn' : '' ?>">
        <div class="card-kicker"><?= e($k['kicker']) ?></div>
        <div class="kpi-value"><?= e((string) $k['value']) ?></div>
        <div class="card-meta"><?= e($k['meta']) ?></div>
      </div>
    <?php endforeach; ?>
  </div>

  <div class="card elev-sm chart-card">
    <h4>Conversaciones por día</h4>
    <?php if (!$points || $maxValue === 0): ?>
      <p class="chart-empty">Sin conversaciones en el rango seleccionado.</p>
    <?php else: ?>
      <svg viewBox="0 0 <?= $chartW ?> <?= $chartH ?>" role="img"
           aria-label="Conversaciones por día entre <?= e($from) ?> y <?= e($to) ?>">
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--color-accent-400)" stop-opacity="0.45"></stop>
            <stop offset="100%" stop-color="var(--color-accent-400)" stop-opacity="0"></stop>
          </linearGradient>
        </defs>
        <polyline points="<?= e($areaPoints) ?>" fill="url(#areaGrad)" stroke="none"></polyline>
        <polyline points="<?= e($linePoints) ?>" fill="none" stroke="var(--color-accent-600)"
                  stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
        <?php foreach ($points as $i => $p): ?>
          <circle cx="<?= e((string) $p['x']) ?>" cy="<?= e((string) $p['y']) ?>" r="4.5"
                  fill="var(--color-bg)" stroke="var(--color-accent-600)" stroke-width="2.5">
            <title><?= e($p['date']) ?>: <?= (int) $p['value'] ?></title>
          </circle>
          <?php if ($i % $labelEvery === 0): ?>
            <text x="<?= e((string) $p['x']) ?>" y="<?= $chartH - 8 ?>" text-anchor="middle"
                  font-size="11" fill="var(--color-text)" opacity="0.55"><?= e($p['label']) ?></text>
          <?php endif; ?>
        <?php endforeach; ?>
      </svg>
    <?php endif; ?>
  </div>

  <div class="card elev-sm table-card">
    <h4>Detalle de conversaciones</h4>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Cliente</th>
            <th>Teléfono</th>
            <th>Estado</th>
            <th>Mensajes</th>
            <th>Última actividad</th>
            <th>Creada</th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($rows as $r): ?>
            <?php
            if (!empty($r['human_support'])) {
                $badgeLabel = 'AYUDA';
                $badgeClass = 'tag-accent';
            } elseif ($r['mode_conversation'] === 'HUMAN') {
                $badgeLabel = 'HUMAN';
                $badgeClass = 'tag-neutral';
            } else {
                $badgeLabel = 'AI';
                $badgeClass = 'tag-accent-2';
            }
            ?>
            <tr>
              <td><?= (int) $r['id_conversation'] ?></td>
              <td style="font-weight:600;"><?= e($r['nombre_contact'] ?: '—') ?></td>
              <td><?= e($r['wa_id']) ?></td>
              <td><span class="tag <?= e($badgeClass) ?>"><?= e($badgeLabel) ?></span></td>
              <td><?= (int) $r['msg_count'] ?></td>
              <td><?= e((string) ($r['last_message_at'] ?? '—')) ?></td>
              <td><?= e((string) $r['fecha_creacion']) ?></td>
            </tr>
          <?php endforeach; ?>
          <?php if (!$rows): ?>
            <tr><td colspan="7">Sin conversaciones en el rango.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>

  <p class="reports-source">
    Fuente: tablas <code>crm_*</code> en MySQL.
    <?php if (!empty($overview['catalog_api_base'])): ?>
      Catálogo/pedidos (corroboración): <code><?= e($overview['catalog_api_base']) ?></code>
    <?php endif; ?>
  </p>
</div>
