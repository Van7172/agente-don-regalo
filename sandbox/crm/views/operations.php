<?php
/** @var array $crmOperations */
/** @var array $agentOperations */
$initial = [
    'ok' => true,
    'crm' => $crmOperations,
    'agent' => $agentOperations,
];
$jsonFlags = JSON_UNESCAPED_UNICODE
    | JSON_HEX_TAG
    | JSON_HEX_AMP
    | JSON_HEX_APOS
    | JSON_HEX_QUOT;
?>
<main class="operations" aria-labelledby="operations-title">
  <div class="operations-head">
    <div>
      <h2 id="operations-title">Panel operacional</h2>
      <p class="lead">Estado del agente, Redis y atención humana en un solo lugar.</p>
    </div>
    <div class="operations-refresh">
      <span id="ops-updated" aria-live="polite">Cargando…</span>
      <button class="btn btn-primary" id="ops-refresh" type="button">Actualizar</button>
    </div>
  </div>

  <div class="ops-alert" id="ops-alert" role="alert" hidden></div>
  <section class="ops-kpis" id="ops-kpis" aria-label="Indicadores operacionales"></section>

  <div class="ops-grid">
    <section class="card elev-sm ops-panel">
      <div class="ops-panel-head">
        <div>
          <span class="card-kicker">Dependencias</span>
          <h3>Circuit breakers</h3>
        </div>
        <span class="ops-status" id="ops-circuit-status">—</span>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead><tr><th>Circuito</th><th>Estado</th><th>Fallos</th><th>Reintento</th></tr></thead>
          <tbody id="ops-circuits"></tbody>
        </table>
      </div>
    </section>

    <section class="card elev-sm ops-panel">
      <div class="ops-panel-head">
        <div>
          <span class="card-kicker">Rendimiento</span>
          <h3>Latencias</h3>
        </div>
        <span class="ops-status is-neutral">Desde el último reinicio</span>
      </div>
      <div class="table-scroll">
        <table class="table">
          <!-- p95 antes que la máxima a propósito: la máxima es UN caso y suele
               ser un pico raro; el p95 es lo que está viviendo el cliente lento
               de verdad, que es la pregunta cuando alguien dice "va lento". -->
          <thead><tr><th>Operación</th><th>Ejecuciones</th><th>Promedio</th><th>p95</th><th>Máxima</th></tr></thead>
          <tbody id="ops-latencies"></tbody>
        </table>
      </div>
    </section>

    <section class="card elev-sm ops-panel">
      <div class="ops-panel-head">
        <div>
          <span class="card-kicker">Atención humana</span>
          <h3>Handoffs activos</h3>
        </div>
        <a class="btn btn-secondary btn-sm" href="<?= e(url_to('/')) ?>">Abrir inbox</a>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead><tr><th>Conversación</th><th>Contacto</th><th>Estado</th><th>Espera</th></tr></thead>
          <tbody id="ops-handoffs"></tbody>
        </table>
      </div>
    </section>

    <section class="card elev-sm ops-panel">
      <div class="ops-panel-head">
        <div>
          <span class="card-kicker">Incidentes</span>
          <h3>Errores y reintentos</h3>
        </div>
        <span class="ops-status is-neutral">Sin contenido de mensajes</span>
      </div>
      <div class="table-scroll">
        <table class="table">
          <thead><tr><th>Origen</th><th>Resultado</th><th>Cantidad</th><th>Detalle</th></tr></thead>
          <tbody id="ops-errors"></tbody>
        </table>
      </div>
    </section>
  </div>

  <p class="reports-source">
    Actualización automática cada 15 segundos. Los contadores del agente se
    reinician cuando se despliega una nueva réplica; Redis y MySQL conservan su estado.
  </p>
</main>

<script>
window.__OPS_INITIAL__ = <?= json_encode($initial, $jsonFlags) ?: '{}' ?>;
window.__OPS_API__ = <?= json_encode(url_to('api/operations'), $jsonFlags) ?>;
</script>
<script src="<?= e(url_to('assets/operations.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/operations.js') ?>"></script>
