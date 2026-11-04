<?php
/** @var string $from */
/** @var string $to */
/** @var array<int, array<string, mixed>> $rows */
/** @var array<string, mixed> $totals */

// Escala relativa al mejor de la tabla. Contra el 100% teórico casi todas las
// barras salen pegadas al suelo y no se distingue nada; lo que se compara aquí
// es un anuncio contra otro, no contra un ideal.
$maxConversion = 0.0;
foreach ($rows as $row) {
    if ($row['conversion'] !== null) {
        $maxConversion = max($maxConversion, (float) $row['conversion']);
    }
}
?>
<div class="reports">
  <h2>Campañas</h2>
  <p class="lead">Qué anuncio trae compradores y cuál trae curiosos.</p>

  <form class="reports-filters" method="get" action="<?= e(url_to('campaigns.php')) ?>">
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
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Chats</div>
      <div class="kpi-value"><?= (int) $totals['leads'] ?></div>
      <div class="card-meta">abiertos en el rango</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Ventas</div>
      <div class="kpi-value"><?= (int) $totals['ventas'] ?></div>
      <div class="card-meta">cerradas por esos chats</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Conversión</div>
      <div class="kpi-value"><?= $totals['conversion'] !== null ? e((string) $totals['conversion']) . '%' : '—' ?></div>
      <div class="card-meta">del total de chats</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Monto</div>
      <div class="kpi-value"><?= e((string) $totals['monto_label']) ?></div>
      <div class="card-meta">sin envío</div>
    </div>
  </div>

  <div class="card elev-sm table-card">
    <h4>Rendimiento por anuncio</h4>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Anuncio</th>
            <th>Chats</th>
            <th>Ventas</th>
            <th>Conversión</th>
            <th>Monto</th>
            <th>Ticket prom.</th>
            <th>Necesitó asesor</th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($rows as $row): ?>
            <?php
            $pctHuman = $row['leads'] > 0
                ? round(($row['leads_human'] / $row['leads']) * 100)
                : 0;
            $barWidth = ($row['conversion'] !== null && $maxConversion > 0)
                ? round(((float) $row['conversion'] / $maxConversion) * 100)
                : 0;
            ?>
            <tr<?= $row['is_unknown'] ? ' class="is-muted"' : '' ?>>
              <td>
                <?php if ($row['url'] !== ''): ?>
                  <a href="<?= e($row['url']) ?>" target="_blank" rel="noopener noreferrer"
                     style="font-weight:600;"><?= e($row['label']) ?></a>
                <?php else: ?>
                  <strong><?= e($row['label']) ?></strong>
                <?php endif; ?>
                <?php if ($row['source_id'] !== ''): ?>
                  <small><?= e($row['type'] ?: 'ad') ?> · <?= e($row['source_id']) ?></small>
                <?php endif; ?>
              </td>
              <td><?= (int) $row['leads'] ?></td>
              <td><?= (int) $row['ventas'] ?></td>
              <td>
                <?php if ($row['conversion'] === null): ?>
                  —
                <?php else: ?>
                  <div class="conv-cell">
                    <span class="conv-pct"><?= e((string) $row['conversion']) ?>%</span>
                    <span class="conv-bar" aria-hidden="true">
                      <span class="conv-bar-fill" style="width: <?= (int) $barWidth ?>%"></span>
                    </span>
                  </div>
                <?php endif; ?>
              </td>
              <td><?= e((string) $row['monto_label']) ?></td>
              <td><?= e((string) $row['ticket']) ?></td>
              <td><?= (int) $pctHuman ?>%</td>
            </tr>
          <?php endforeach; ?>
          <?php if (!$rows): ?>
            <tr><td colspan="7">Sin chats en el rango.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card elev-sm table-card reports-caveats">
    <h4>Cómo leer esta tabla</h4>
    <ul>
      <li>
        <strong>El rango filtra por llegada del chat, no por cierre de la venta.</strong>
        De los chats que entraron en estas fechas, cuántos compraron — cuando sea
        que compraran. Por eso los días más recientes salen bajos: esos chats
        todavía no han tenido tiempo de cerrar.
      </li>
      <li>
        <strong>Meta no manda el nombre del anuncio.</strong> El referral trae el
        titular y el id, no "PORTADA FAMILIA". Si un anuncio aparece solo con su
        id es que llegó sin titular.
      </li>
      <li>
        <strong>«Sin anuncio identificado» no es tráfico orgánico.</strong> Mezcla
        a quien escribió por su cuenta con todos los chats anteriores a que se
        empezara a capturar el anuncio, que no es un dato recuperable.
      </li>
      <li>
        <strong>Falta el gasto.</strong> Sin lo invertido en cada anuncio esto es
        conversión, no costo por venta: dice cuál convierte mejor, no cuál sale
        más barato.
      </li>
    </ul>
  </div>

  <p class="reports-source">
    Fuente: <code>crm_conversations.ad_*</code> × <code>crm_ventas_historiales</code>.
  </p>
</div>
