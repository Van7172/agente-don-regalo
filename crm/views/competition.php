<?php
/** @var array<int, array<string, mixed>> $stats */
/** @var array<int, array<string, mixed>> $gaps */
/** @var array<string, mixed> $totals */
?>
<div class="reports">
  <h2>Competencia</h2>
  <p class="lead">
    Lo que ofrecen Rosatel, Magia.pe y Sorprende Lima y nosotros no tenemos cerca.
    Solo precios públicos de catálogo, con URL de origen.
  </p>

  <?php if ((int) $totals['activos'] === 0): ?>
  <div class="card elev-sm" style="margin-bottom:1rem;border-left:4px solid #c9a227;">
    <p style="margin:0;">
      Todavía no hay productos scrapeados. Eso <strong>no</strong> significa que
      el catálogo esté completo. La migración <code>016</code> solo crea las
      tablas; los productos los escribe el <em>agente</em> tras un crawl.
      Comprueba: redeploy con el código de competencia,
      <code>COMPETITION_CRAWL_ENABLED=1</code>, <code>WATCHDOG_ENABLED=1</code>,
      <code>CRM_MODE=external</code>, y dispara
      <code>POST /internal/competition/crawl</code> si no quieres esperar al tick.
    </p>
  </div>
  <?php endif; ?>

  <div class="kpi-grid">
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Competidores</div>
      <div class="kpi-value"><?= (int) $totals['competidores'] ?></div>
      <div class="card-meta">lista fija del negocio</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Productos activos</div>
      <div class="kpi-value"><?= (int) $totals['activos'] ?></div>
      <div class="card-meta">vistos en el último crawl</div>
    </div>
    <div class="card elev-sm kpi-card is-warn">
      <div class="card-kicker">Huecos candidatos</div>
      <div class="kpi-value"><?= (int) $totals['huecos'] ?></div>
      <div class="card-meta">sin equivalente cercano en nuestro catálogo</div>
    </div>
  </div>

  <div class="card elev-sm table-card" style="margin-bottom:1.25rem;">
    <h4>Por competidor</h4>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Competidor</th>
            <th>Activos</th>
            <th>Huecos</th>
            <th>Último crawl</th>
          </tr>
        </thead>
        <tbody>
          <?php if (!$stats): ?>
          <tr><td colspan="4">Sin competidores en base (corre la migración 016).</td></tr>
          <?php endif; ?>
          <?php foreach ($stats as $row): ?>
          <?php
            $visto = (string) ($row['ultima_vez'] ?? '');
            $ts = $visto !== '' ? strtotime($visto) : false;
          ?>
          <tr>
            <td><?= e((string) $row['nombre_competidor']) ?></td>
            <td><?= (int) ($row['activos'] ?? 0) ?></td>
            <td><?= (int) ($row['huecos'] ?? 0) ?></td>
            <td><?= e($ts ? date('d/m/Y H:i', $ts) : '—') ?></td>
          </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card elev-sm table-card">
    <h4>Huecos de catálogo</h4>
    <p class="card-meta" style="margin:0 0 .75rem;">
      Productos ajenos cuyo vecino en Qdrant quedó bajo el umbral. Que lo ofrezcan
      no prueba que se vendería aquí.
    </p>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Competidor</th>
            <th>Producto</th>
            <th>Precio</th>
            <th>Match propio</th>
            <th>Score</th>
            <th>Visto</th>
          </tr>
        </thead>
        <tbody>
          <?php if (!$gaps): ?>
          <tr><td colspan="6">Sin huecos todavía.</td></tr>
          <?php endif; ?>
          <?php foreach ($gaps as $row): ?>
          <tr>
            <td><?= e($row['competidor']) ?></td>
            <td>
              <a href="<?= e($row['url']) ?>" target="_blank" rel="noopener noreferrer">
                <?= e($row['nombre']) ?>
              </a>
            </td>
            <td>
              <?= $row['precio'] !== null
                ? 'S/ ' . e(number_format((float) $row['precio'], 2, '.', ','))
                : '—' ?>
            </td>
            <td><?= e($row['match_nombre'] !== '' ? $row['match_nombre'] : '—') ?></td>
            <td><?= $row['match_score'] !== null ? e(number_format((float) $row['match_score'], 3)) : '—' ?></td>
            <td><?= e($row['visto']) ?></td>
          </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  </div>
</div>
