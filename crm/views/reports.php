<?php
/** @var string $from */
/** @var string $to */
/** @var array $overview */
/** @var array $rows */
/** @var array $daily */
/** @var array $funnel */

$kpis = [
    ['kicker' => 'Conversaciones', 'value' => (int) $overview['conversations'], 'meta' => 'creadas en el rango'],
    ['kicker' => 'Mensajes', 'value' => (int) $overview['messages'], 'meta' => 'intercambiados'],
    ['kicker' => 'Inbound', 'value' => (int) $overview['inbound_messages'], 'meta' => 'escritos por clientes'],
    ['kicker' => 'Msgs bot', 'value' => (int) $overview['bot_messages'], 'meta' => 'enviados por Don Regalo'],
    ['kicker' => 'Msgs asesor', 'value' => (int) $overview['agent_messages'], 'meta' => 'enviados por humanos'],
    ['kicker' => 'Modo AI actual', 'value' => (int) $overview['mode_ai'], 'meta' => 'de los chats que llegaron en el rango'],
    ['kicker' => 'Modo HUMAN actual', 'value' => (int) $overview['mode_human'], 'meta' => 'de los chats que llegaron en el rango'],
    ['kicker' => '% en modo HUMAN', 'value' => $overview['pct_human'] . '%', 'meta' => 'estado actual de la cohorte'],
    ['kicker' => 'Contactos captados', 'value' => (int) $overview['leads'], 'meta' => 'primer registro en el rango'],
    ['kicker' => 'Piden ayuda ahora', 'value' => (int) $overview['open_needs_help'], 'meta' => 'cola actual, sin filtro de fechas', 'warn' => true],
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
    <?php if (Auth::canManage()): ?><a class="btn btn-secondary" href="<?= e(url_to('export.php') . '?' . http_build_query(['tipo' => 'conversaciones', 'from' => $from, 'to' => $to])) ?>">Exportar CSV</a><?php endif; ?>
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

  <div class="card elev-sm funnel-card">
    <h4>Embudo de ventas</h4>
    <p class="funnel-note">
      De los chats que llegaron en estas fechas, en qué etapa están hoy. Los recientes
      todavía pueden avanzar. Pulsa un número para ver esos chats.
    </p>
    <?php
    $funnelLink = static function (string $stage) use ($from, $to): string {
        return url_to('/') . '?' . http_build_query(['etapa' => $stage, 'from' => $from, 'to' => $to]);
    };
    $funnelTotal = (int) $funnel['total'];
    ?>
    <div class="funnel-stages">
      <?php foreach ($funnel['stages'] as $stage): ?>
        <a class="funnel-stage is-<?= e($stage['code']) ?>" href="<?= e($funnelLink($stage['code'])) ?>">
          <span class="funnel-count"><?= (int) $stage['count'] ?></span>
          <span class="funnel-label"><?= e($stage['label']) ?></span>
          <span class="funnel-pct"><?= $funnelTotal > 0 ? e((string) round($stage['count'] / $funnelTotal * 100, 1)) . '%' : '—' ?></span>
        </a>
      <?php endforeach; ?>
      <?php if ((int) $funnel['unclassified'] > 0): ?>
        <a class="funnel-stage is-sin_etapa" href="<?= e($funnelLink(Repository::STAGE_NONE)) ?>"
           title="Chats anteriores a las etapas, sin venta registrada">
          <span class="funnel-count"><?= (int) $funnel['unclassified'] ?></span>
          <span class="funnel-label">Sin etapa</span>
          <span class="funnel-pct"><?= e((string) round($funnel['unclassified'] / $funnelTotal * 100, 1)) ?>%</span>
        </a>
      <?php endif; ?>
    </div>
    <?php if ($funnel['lost_reasons']): ?>
      <h5 class="funnel-subtitle">Por qué se perdieron</h5>
      <ul class="funnel-reasons">
        <?php foreach ($funnel['lost_reasons'] as $reason): ?>
          <li><span><?= e($reason['label']) ?></span><strong><?= (int) $reason['count'] ?></strong></li>
        <?php endforeach; ?>
      </ul>
    <?php endif; ?>
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
    <?php if (count($rows) >= 150): ?><p class="card-meta">Se muestran las 150 conversaciones más recientes del período.</p><?php endif; ?>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Cliente</th>
            <th>Teléfono</th>
            <th>Estado</th>
            <th>Etapa</th>
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
              <td><?= e(Repository::STAGES[$r['etapa_conversation'] ?? ''] ?? 'Sin etapa') ?></td>
              <td><?= (int) $r['msg_count'] ?></td>
              <td><?= e((string) ($r['last_message_at'] ?? '—')) ?></td>
              <td><?= e((string) $r['fecha_creacion']) ?></td>
            </tr>
          <?php endforeach; ?>
          <?php if (!$rows): ?>
            <tr><td colspan="8">Sin conversaciones en el rango.</td></tr>
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
