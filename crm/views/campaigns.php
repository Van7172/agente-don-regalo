<?php
/** @var string $from */
/** @var string $to */
/** @var array $rows */
/** @var array $totals */
/** @var array|null $sync */
/** @var array $campaigns */
/** @var string $campaignId */
/** @var array $trend */
/** @var array $attribution */
$money = static function (float $value): string {
    return 'S/' . number_format($value, 2, '.', ',');
};
$coverageComplete = $sync && $from >= $sync['date_from'] && $to <= $sync['date_to'];
$chartData = json_encode(
    ['rows' => $rows, 'trend' => array_values($trend), 'hasMeta' => (bool) $sync,
        'from' => $from, 'to' => $to],
    JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_INVALID_UTF8_SUBSTITUTE
);
?>
<div class="reports campaigns-page">
  <div class="campaigns-header">
    <div>
      <h2>Campañas</h2>
      <p class="lead">Lo invertido en Meta y los compradores registrados en el CRM, en un solo lugar.</p>
    </div>
    <span class="campaigns-sync<?= $sync ? ' is-ready' : '' ?>">
      <?= $sync ? 'Última lectura de Meta: ' . e((string) $sync['synced_at']) : 'Meta aún sin importar' ?>
    </span>
  </div>

  <form class="reports-filters campaigns-filters" method="get" action="<?= e(url_to('campaigns.php')) ?>">
    <div class="field">
      <label for="from">Desde</label>
      <input class="input" id="from" type="date" name="from" value="<?= e($from) ?>" />
    </div>
    <div class="field">
      <label for="to">Hasta</label>
      <input class="input" id="to" type="date" name="to" value="<?= e($to) ?>" />
    </div>
    <div class="field campaigns-campaign-field">
      <label for="campaign">Campaña</label>
      <select class="input" id="campaign" name="campaign">
        <option value="">Todas las campañas</option>
        <?php foreach ($campaigns as $id => $name): ?>
          <option value="<?= e((string) $id) ?>"<?= $campaignId === (string) $id ? ' selected' : '' ?>><?= e($name) ?></option>
        <?php endforeach; ?>
        <option value="unlinked"<?= $campaignId === 'unlinked' ? ' selected' : '' ?>>Sin campaña vinculada</option>
      </select>
    </div>
    <button type="submit" class="btn btn-primary">Aplicar filtros</button>
  </form>

  <?php if ($sync && !$coverageComplete): ?>
    <p class="campaigns-notice">La última lectura de Meta cubre del <?= e((string) $sync['date_from']) ?> al <?= e((string) $sync['date_to']) ?>. El gasto fuera de esas fechas aún no está disponible.</p>
  <?php endif; ?>
  <?php if ($sync && strtotime((string) $sync['synced_at']) < strtotime('-1 day')): ?>
    <p class="campaigns-notice">La lectura de Meta tiene más de 24 horas. Los importes recientes pueden estar incompletos.</p>
  <?php endif; ?>
  <?php if ($attribution['total'] > 0 && $attribution['identified'] === 0 && $totals['spend'] > 0): ?>
    <p class="campaigns-notice">Ninguno de los <?= (int) $attribution['total'] ?> chats del período llegó con ID de anuncio. El gasto de Meta está visible, pero todavía no se puede atribuir chats ni compras a cada anuncio.</p>
  <?php elseif ($attribution['total'] > 0 && $totals['spend'] > 0): ?>
    <p class="campaigns-notice"><?= (int) $attribution['identified'] ?> de <?= (int) $attribution['total'] ?> chats tienen ID de anuncio. La comparación por anuncio solo cubre esos chats.</p>
  <?php elseif ($sync && $attribution['total'] > 0): ?>
    <p class="campaigns-notice">Meta no registra gasto en el período seleccionado. Los chats del CRM se muestran como referencia, sin atribuirlos a anuncios.</p>
  <?php endif; ?>

  <div class="kpi-grid campaigns-kpis">
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Invertido en Meta</div>
      <div class="kpi-value"><?= $sync ? e($money((float) $totals['spend'])) : '—' ?></div>
      <div class="card-meta">fecha en que se mostró el anuncio</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Chats en el CRM</div>
      <div class="kpi-value"><?= number_format((int) $totals['leads']) ?></div>
      <div class="card-meta">iniciados en el rango</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Chats que compraron</div>
      <div class="kpi-value"><?= number_format((int) $totals['buyers']) ?></div>
      <div class="card-meta"><?= $totals['leads'] > 0 ? e((string) round($totals['buyers'] / $totals['leads'] * 100, 1)) . '% de los chats' : 'sin chats en el rango' ?></div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Monto de ventas</div>
      <div class="kpi-value"><?= e($money((float) $totals['monto'])) ?></div>
      <div class="card-meta">monto conocido, sin envío</div>
    </div>
  </div>

  <div class="campaigns-charts">
    <section class="card elev-sm chart-card campaigns-chart">
      <div class="campaigns-chart-head">
        <div><h4>Evolución</h4><p>Total, pico y cambio durante el periodo.</p></div>
        <label>Ver <select class="input" id="campaign-trend-metric">
          <option value="chats">Chats</option>
          <option value="buyers">Chats que compraron</option>
          <?php if ($sync): ?><option value="spend">Gasto en Meta</option><?php endif; ?>
        </select></label>
      </div>
      <div id="campaign-trend-chart" class="campaigns-bars" aria-live="polite"></div>
    </section>
    <section class="card elev-sm chart-card campaigns-chart">
      <div class="campaigns-chart-head">
        <div><h4>Anuncios destacados</h4><p>Los 8 anuncios con más actividad según la medida elegida.</p></div>
        <label>Ordenar por <select class="input" id="campaign-ranking-metric">
          <?php if ($sync): ?><option value="spend"<?= $totals['spend'] > 0 ? ' selected' : '' ?>>Gasto en Meta</option><?php endif; ?>
          <option value="leads"<?= !$sync || $totals['spend'] <= 0 ? ' selected' : '' ?>>Chats</option>
          <option value="buyers">Chats que compraron</option>
        </select></label>
      </div>
      <div id="campaign-ranking-chart" class="campaigns-ranking" aria-live="polite"></div>
    </section>
  </div>

  <section class="card elev-sm table-card campaigns-detail">
    <h4>Detalle por anuncio</h4>
    <p class="campaigns-table-intro">El nombre y el gasto vienen de Meta; chats y compras vienen del CRM.</p>
    <div class="table-scroll">
      <table class="table">
        <thead><tr>
          <th>Anuncio / campaña</th><th>Gasto Meta</th><th>Clics Meta</th><th>Chats CRM</th>
          <th>Compradores CRM</th><th>Conversión CRM</th><th>Ventas registradas</th>
        </tr></thead>
        <tbody>
        <?php foreach ($rows as $row): ?>
          <tr<?= $row['is_unknown'] ? ' class="is-muted"' : '' ?>>
            <td>
              <?php if ($row['url'] !== ''): ?>
                <a href="<?= e($row['url']) ?>" target="_blank" rel="noopener noreferrer"><strong><?= e($row['label']) ?></strong></a>
              <?php else: ?><strong><?= e($row['label']) ?></strong><?php endif; ?>
              <small><?= e($row['campaign']) ?></small>
            </td>
            <td><?= $sync && $row['meta_linked'] ? e($money((float) $row['spend'])) : '—' ?></td>
            <td><?= $sync && $row['meta_linked'] ? number_format((int) $row['clicks']) : '—' ?></td>
            <td><?= (int) $row['leads'] ?></td>
            <td><?= (int) $row['buyers'] ?></td>
            <td>
              <?php if ($row['conversion'] === null): ?>—<?php else: ?>
                <div class="conv-cell"><span class="conv-pct"><?= e((string) $row['conversion']) ?>%</span><span class="conv-bar" aria-hidden="true"><span class="conv-bar-fill" style="width: <?= max(0, min(100, (int) round($row['conversion']))) ?>%"></span></span></div>
              <?php endif; ?>
            </td>
            <td><strong><?= e($money((float) $row['monto'])) ?></strong><small><?= (int) $row['ventas'] ?> ventas</small></td>
          </tr>
        <?php endforeach; ?>
        <?php if (!$rows): ?><tr><td colspan="7">No hay actividad para estos filtros.</td></tr><?php endif; ?>
        </tbody>
      </table>
    </div>
  </section>

  <details class="card elev-sm reports-caveats campaigns-help">
    <summary>Cómo leer estas cifras</summary>
    <ul>
      <li><strong>Las fechas tienen dos significados.</strong> Meta cuenta el gasto cuando mostró el anuncio; el CRM cuenta el chat cuando llegó, no por cierre de la venta. Una compra puede ocurrir días después.</li>
      <li><strong>Conversión CRM</strong> es la proporción de chats que registraron al menos una venta. Una persona puede tener varias ventas; por eso se muestran aparte.</li>
      <li><strong>Monto conocido.</strong> Algunas ventas no tienen importe registrado. El monto mostrado no incluye esas ventas ni el envío.</li>
      <li><strong>Sin anuncio identificado</strong> incluye chats sin referencia y chats antiguos de antes de capturar el anuncio; no significa tráfico orgánico.</li>
      <?php if (!$sync): ?><li><strong>Falta el gasto.</strong> Todavía no se importó la lectura de Meta al CRM.</li><?php endif; ?>
    </ul>
  </details>
  <p class="reports-source">Fuentes: Meta Ads (lectura guardada) y <code>crm_conversations.ad_*</code> × <code>crm_ventas_historiales</code>.</p>
</div>
<script id="campaign-chart-data" type="application/json"><?= $chartData ?></script>
<script defer src="<?= e(url_to('assets/campaigns.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/campaigns.js') ?>"></script>
