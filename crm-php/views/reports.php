<div class="reports">
  <form class="filters" method="get" action="<?= e(url_to('reports.php')) ?>">
    <label>Desde
      <input type="date" name="from" value="<?= e($from) ?>" />
    </label>
    <label>Hasta
      <input type="date" name="to" value="<?= e($to) ?>" />
    </label>
    <button type="submit" class="primary">Filtrar</button>
  </form>

  <section class="kpis">
    <article><span>Conversaciones</span><strong><?= (int) $overview['conversations'] ?></strong></article>
    <article><span>Mensajes</span><strong><?= (int) $overview['messages'] ?></strong></article>
    <article><span>Inbound</span><strong><?= (int) $overview['inbound_messages'] ?></strong></article>
    <article><span>Msgs bot</span><strong><?= (int) $overview['bot_messages'] ?></strong></article>
    <article><span>Msgs asesor</span><strong><?= (int) $overview['agent_messages'] ?></strong></article>
    <article><span>Modo AI</span><strong><?= (int) $overview['mode_ai'] ?></strong></article>
    <article><span>Modo HUMAN</span><strong><?= (int) $overview['mode_human'] ?></strong></article>
    <article><span>% HUMAN</span><strong><?= e((string) $overview['pct_human']) ?>%</strong></article>
    <article class="warn"><span>Abiertas piden ayuda</span><strong><?= (int) $overview['open_needs_help'] ?></strong></article>
    <article><span>Leads</span><strong><?= (int) $overview['leads'] ?></strong></article>
  </section>

  <p class="muted">
    Fuente: tablas <code>crm_*</code> en MySQL local.
    <?php if (!empty($overview['catalog_api_base'])): ?>
      Catálogo/pedidos (corroboración): <code><?= e($overview['catalog_api_base']) ?></code>
    <?php endif; ?>
  </p>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Contacto</th>
          <th>Teléfono</th>
          <th>Modo</th>
          <th>Ayuda</th>
          <th>Msgs</th>
          <th>Último</th>
          <th>Creada</th>
        </tr>
      </thead>
      <tbody>
      <?php foreach ($rows as $r): ?>
        <tr class="<?= !empty($r['human_support']) ? 'needs-help' : '' ?>">
          <td><?= (int) $r['id_conversation'] ?></td>
          <td><?= e($r['nombre_contact'] ?: '—') ?></td>
          <td><?= e($r['wa_id']) ?></td>
          <td><span class="badge <?= $r['mode_conversation'] === 'HUMAN' ? 'human' : 'ai' ?>"><?= e($r['mode_conversation']) ?></span></td>
          <td><?= !empty($r['human_support']) ? 'Sí' : '—' ?></td>
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
