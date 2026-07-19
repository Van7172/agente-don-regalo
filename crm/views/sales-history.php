<?php
/** @var string $from */
/** @var string $to */
/** @var string $status */
/** @var string $query */
/** @var array<int, array<string, mixed>> $rows */
?>
<main class="sales-history">
  <header class="sales-history-head">
    <div>
      <h2>Historial de ventas</h2>
      <p class="lead">Ventas cerradas por Regalito y confirmaciones de entrega del CRM.</p>
    </div>
    <span class="history-count"><?= e((string) count($rows)) ?> registros</span>
  </header>

  <form class="history-filters" method="get" action="<?= e(url_to('sales-history.php')) ?>">
    <label>
      <span>Desde</span>
      <input class="input" type="date" name="from" value="<?= e($from) ?>" />
    </label>
    <label>
      <span>Hasta</span>
      <input class="input" type="date" name="to" value="<?= e($to) ?>" />
    </label>
    <label>
      <span>Estado</span>
      <select class="input" name="status">
        <option value=""<?= $status === '' ? ' selected' : '' ?>>Todos</option>
        <option value="pendiente"<?= $status === 'pendiente' ? ' selected' : '' ?>>Pendiente</option>
        <option value="entregado"<?= $status === 'entregado' ? ' selected' : '' ?>>Entregado</option>
      </select>
    </label>
    <label class="history-search">
      <span>Buscar</span>
      <input
        class="input"
        type="search"
        name="q"
        value="<?= e($query) ?>"
        placeholder="Cliente, WhatsApp, producto o pedido"
      />
    </label>
    <button class="btn btn-primary" type="submit">Filtrar</button>
  </form>

  <div class="history-table-wrap">
    <table class="history-table">
      <thead>
        <tr>
          <th>Estado</th>
          <th>Cliente</th>
          <th>Venta</th>
          <th>Entrega</th>
          <th>Pedido</th>
          <th>Cierre</th>
          <th>Confirmación</th>
        </tr>
      </thead>
      <tbody>
      <?php if (!$rows): ?>
        <tr>
          <td class="history-empty" colspan="7">No hay ventas para estos filtros.</td>
        </tr>
      <?php endif; ?>
      <?php foreach ($rows as $row): ?>
        <tr>
          <td>
            <span class="history-status is-<?= e($row['status']) ?>">
              <?= e($row['status_label']) ?>
            </span>
          </td>
          <td>
            <strong><?= e($row['contact_name'] ?: 'Sin nombre') ?></strong>
            <small><?= e($row['wa_id']) ?></small>
          </td>
          <td>
            <strong><?= e($row['product']) ?></strong>
            <small><?= e(trim($row['district'] . ' · ' . $row['shipping'], ' ·')) ?></small>
          </td>
          <td>
            <strong><?= e($row['delivery_date'] ?: '—') ?></strong>
            <small><?= e($row['schedule'] ?: '—') ?></small>
          </td>
          <td>
            <?= $row['temporary_order_id'] !== null
              ? '#' . e((string) $row['temporary_order_id'])
              : '—' ?>
          </td>
          <td><?= e($row['closed_at']) ?></td>
          <td>
            <strong><?= e($row['delivered_at']) ?></strong>
            <small><?= e($row['advisor'] ?: '—') ?></small>
          </td>
        </tr>
      <?php endforeach; ?>
      </tbody>
    </table>
  </div>
</main>
