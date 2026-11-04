<?php
/** @var string $from */
/** @var string $to */
/** @var array<int, array<string, mixed>> $rows */
/** @var array<string, mixed> $totals */
?>
<div class="reports">
  <h2>Oportunidades</h2>
  <p class="lead">Lo que los clientes pidieron y el catálogo no tenía.</p>

  <form class="reports-filters" method="get" action="<?= e(url_to('opportunities.php')) ?>">
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
    <div class="card elev-sm kpi-card is-warn">
      <div class="card-kicker">Sin nada que ofrecer</div>
      <div class="kpi-value"><?= (int) $totals['vacio'] ?></div>
      <div class="card-meta">búsquedas que no devolvieron ni una alternativa</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Solo alternativas</div>
      <div class="kpi-value"><?= (int) $totals['aproximado'] ?></div>
      <div class="card-meta">no era lo pedido, pero se ofreció algo</div>
    </div>
    <div class="card elev-sm kpi-card">
      <div class="card-kicker">Términos distintos</div>
      <div class="kpi-value"><?= (int) $totals['terminos'] ?></div>
      <div class="card-meta">agrupados en el rango</div>
    </div>
  </div>

  <div class="card elev-sm table-card">
    <h4>Lo más pedido que no tenemos</h4>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th>Búsqueda</th>
            <th>Categoría</th>
            <th>Veces</th>
            <th>Sin nada</th>
            <th>Con alternativa</th>
            <th>Chats</th>
            <th>Última vez</th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($rows as $row): ?>
            <tr>
              <td><strong><?= e($row['query']) ?></strong></td>
              <td><?= e($row['categoria'] ?: '—') ?></td>
              <td><?= (int) $row['veces'] ?></td>
              <td>
                <?php if ($row['vacio'] > 0): ?>
                  <span class="tag tag-accent"><?= (int) $row['vacio'] ?></span>
                <?php else: ?>
                  —
                <?php endif; ?>
              </td>
              <td><?= $row['aproximado'] > 0 ? (int) $row['aproximado'] : '—' ?></td>
              <td><?= (int) $row['chats'] ?></td>
              <td><?= e($row['ultima_vez']) ?></td>
            </tr>
          <?php endforeach; ?>
          <?php if (!$rows): ?>
            <tr><td colspan="7">Sin registros en el rango.</td></tr>
          <?php endif; ?>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card elev-sm table-card reports-caveats">
    <h4>Cómo leer esta tabla</h4>
    <ul>
      <li>
        <strong>«Sin nada» pesa más que «con alternativa».</strong> En el primer
        caso el cliente se fue con las manos vacías; en el segundo se llevó algo
        que sí podía comprar. Por eso el orden es por la primera columna.
      </li>
      <li>
        <strong>Es lo que buscó el bot, no lo que escribió el cliente.</strong>
        Se guardan los términos de búsqueda, ya destilados y sin datos
        personales, no el mensaje entero.
      </li>
      <li>
        <strong>Solo cuenta desde que se instaló esta medición.</strong> Lo
        anterior no existe: nunca se guardó qué devolvía cada búsqueda, y no hay
        forma de reconstruirlo.
      </li>
      <li>
        <strong>Un término repetido puede ser una sola persona insistiendo.</strong>
        La columna «Chats» lo separa: veinte veces en un chat es un cliente
        atascado; veinte veces en quince chats es un producto que falta.
      </li>
    </ul>
  </div>

  <p class="reports-source">
    Fuente: <code>crm_demanda_no_cubierta</code>, que llena el agente al agotar
    la búsqueda.
  </p>
</div>
