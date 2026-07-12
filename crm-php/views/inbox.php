<div class="inbox-shell" id="inbox-app"
  data-base="<?= e(url_to('')) ?>"
  data-poll-list="4000"
  data-poll-thread="4000"
  data-mobile-chat="false">

  <!-- Cola de atención: conversaciones marcadas human_support -->
  <section class="help-rail" id="help-rail" hidden>
    <div class="help-rail-head">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="12" y1="8" x2="12" y2="12"></line>
        <line x1="12" y1="16" x2="12.01" y2="16"></line>
      </svg>
      <span>Cola de atención — necesitan ayuda ahora</span>
    </div>
    <div class="help-rail-chips" id="help-rail-chips"></div>
  </section>

  <!-- Bandeja vacía: no hay ninguna conversación todavía -->
  <div class="empty-state" id="inbox-empty" hidden>
    <div class="icon">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M22 12h-6l-2 3h-4l-2-3H2"></path>
        <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path>
      </svg>
    </div>
    <h3>Aún no hay conversaciones</h3>
    <p>Cuando tus clientes escriban por WhatsApp, sus chats con Regalito aparecerán aquí en tiempo real.</p>
  </div>

  <div class="inbox-panes" id="inbox-panes">

    <!-- Lista de conversaciones -->
    <div class="list-pane">
      <div class="list-pane-head">
        <div class="search-wrap">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7"></circle>
            <line x1="21" y1="21" x2="16.5" y2="16.5"></line>
          </svg>
          <input class="input" id="conv-search" type="search" placeholder="Buscar conversación…" aria-label="Buscar conversación" />
        </div>
        <div class="list-count" id="conv-count">—</div>
      </div>
      <div class="list-scroll" id="conv-list"></div>
    </div>

    <!-- Hilo -->
    <div class="chat-pane">
      <div class="empty-state" id="chat-placeholder">
        <div class="icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>
          </svg>
        </div>
        <h3>Selecciona una conversación</h3>
        <p>Elige un chat de la lista para ver el hilo completo y tomar el control cuando haga falta.</p>
      </div>

      <div class="chat-body" id="chat-body" hidden>
        <div class="chat-head">
          <button type="button" class="icon-btn mobile-back" id="btn-back" aria-label="Volver a la lista">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <line x1="19" y1="12" x2="5" y2="12"></line>
              <polyline points="12 19 5 12 12 5"></polyline>
            </svg>
          </button>
          <div class="avatar" id="chat-avatar"></div>
          <div class="who">
            <div class="chat-name" id="chat-name">—</div>
            <div class="chat-state">
              <span class="dot-sm" id="chat-dot"></span>
              <span id="chat-state-label"></span>
            </div>
          </div>
          <button type="button" class="btn btn-primary" id="btn-human" hidden>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            Tomar conversación
          </button>
          <button type="button" class="btn btn-secondary" id="btn-ai" hidden>Modo AI</button>
          <button type="button" class="icon-btn icon-btn-outline" id="btn-lead" title="Resumen del lead" aria-label="Resumen del lead">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
              <circle cx="12" cy="7" r="4"></circle>
            </svg>
          </button>
        </div>

        <div class="thread" id="thread"></div>

        <!-- Bot al mando: sin composer, hay que tomar la conversación -->
        <div class="ai-banner" id="ai-banner" hidden>
          <span>Regalito está a cargo de este chat. Toma la conversación para responder tú.</span>
          <button type="button" class="btn btn-primary" id="btn-take">Tomar</button>
        </div>

        <!-- Modo HUMAN: el asesor responde -->
        <form class="composer" id="composer" hidden>
          <textarea class="input" id="draft" rows="1" placeholder="Escribe como asesor…"></textarea>
          <button type="submit" class="btn btn-primary btn-round" aria-label="Enviar">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </form>
      </div>
    </div>

    <!-- Resumen del lead -->
    <aside class="lead-panel" id="lead-panel">
      <div class="lead-panel-inner">
        <div class="lead-head">
          <h4>Resumen del lead</h4>
          <button type="button" class="icon-btn" id="btn-lead-close" aria-label="Cerrar panel">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
        <div class="avatar lead-avatar" id="lead-avatar"></div>
        <div class="lead-name" id="lead-name">—</div>
        <div class="lead-sub" id="lead-sub"></div>
        <div class="lead-fields" id="lead-fields"></div>
      </div>
    </aside>
  </div>
</div>

<div class="alert error-box" id="error-box" role="alert" hidden></div>

<script src="<?= e(url_to('assets/inbox.js')) ?>"></script>
