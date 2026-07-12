<div class="app" id="inbox-app"
  data-base="<?= e(url_to('')) ?>"
  data-poll-list="4000"
  data-poll-thread="4000">
  <aside class="sidebar">
    <div class="list-head">
      <h2>Conversaciones</h2>
      <span id="needs-help-count" class="pill warn" hidden>0 ayuda</span>
    </div>
    <div class="list" id="conv-list"></div>
  </aside>
  <main class="main">
    <div class="empty" id="empty-state">Selecciona una conversación</div>
    <div id="thread-wrap" hidden>
      <div class="toolbar">
        <div>
          <h2 id="thread-title">—</h2>
          <div class="meta" id="thread-meta"></div>
        </div>
        <div class="actions">
          <button type="button" id="btn-ai">Modo AI</button>
          <button type="button" class="primary" id="btn-human">Tomar (humano)</button>
        </div>
      </div>
      <div class="thread" id="thread"></div>
      <div class="composer">
        <textarea id="draft" placeholder="Escribe como asesor… (pasa a HUMAN y envía)"></textarea>
        <button type="button" id="btn-send">Enviar</button>
      </div>
    </div>
    <div class="alert" id="error-box" hidden></div>
  </main>
</div>
<script src="<?= e(url_to('assets/inbox.js')) ?>"></script>
