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
  <div class="card elev-sm reports-caveats" style="margin-bottom:1rem;border-left:4px solid #c9a227;">
    <p style="margin:0 0 .75rem;">
      Todavía no hay productos scrapeados. Eso <strong>no</strong> significa que
      el catálogo esté completo: la migración <code>016</code> solo crea las
      tablas, y los productos los escribe el <em>agente</em> tras un crawl.
    </p>
    <p style="margin:0 0 .75rem;">
      <strong>Lo primero, porque no se ve desde aquí:</strong> un crawl que falla
      deja esta pantalla exactamente igual que uno que nunca corrió. El crawl solo
      marca su cooldown si hubo avance, así que un fallo de red o de robots.txt no
      deja rastro en el panel. Dispáralo a mano y lee lo que devuelve — el resumen
      trae los errores:
    </p>
    <pre style="margin:0 0 .75rem;overflow-x:auto;"><code>curl -X POST https://TU-AGENTE/internal/competition/crawl \
     -H "X-Agent-Token: AGENT_INTERNAL_TOKEN"</code></pre>
    <ul>
      <li>
        <strong>Devuelve productos</strong> → el crawl funciona y lo que falla es
        la programación: revisa <code>WATCHDOG_ENABLED=1</code>. El crawl no tiene
        bucle propio, cuelga del tick del watchdog, así que con el watchdog apagado
        <code>COMPETITION_CRAWL_ENABLED=1</code> no sirve de nada.
      </li>
      <li>
        <strong>Devuelve <code>errores</code></strong> → el crawl sí corre y falla.
        Ahí está la causa.
      </li>
      <li>
        <strong><code>500</code></strong> → el crawl revienta antes de poder
        contarlo. Ojo: <code>run_crawl</code> ya captura los fallos <em>por
        competidor</em>, así que un 500 significa que se rompió algo
        <em>fuera</em> de esa red — mira los logs del agente. Pasó el 03-08 y
        costó tres días: la métrica del bucle se llamaba con un argumento que no
        existe, y la copia dentro del <code>except</code> tumbaba el manejador.
      </li>
      <li>
        <strong><code>404</code></strong> → el agente no se ha redesplegado con el
        código de competencia. Poner la variable en el panel no despliega el código.
      </li>
      <li>
        <strong><code>skipped: crm_disabled</code></strong> → falta
        <code>CRM_MODE=external</code> o <code>CRM_BASE_URL</code> en el agente.
      </li>
    </ul>
    <p style="margin:0;">
      Y si acabas de redesplegar, espera: el primer crawl tarda hasta un tick
      (<code>WATCHDOG_TICK_SECONDS</code>, 5 min por defecto) y entre crawls pasan
      12 h (<code>COMPETITION_CRAWL_INTERVAL_SECONDS</code>).
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
