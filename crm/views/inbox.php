<div class="inbox-shell" id="inbox-app"
  data-base="<?= e(url_to('')) ?>"
  data-poll-list="4000"
  data-poll-thread="4000"
  data-mobile-chat="false"
  data-user-id="<?= e((string) ($user['id'] ?? '')) ?>"
  data-user-name="<?= e((string) ($user['name'] ?? '')) ?>">

  <!-- Seguimientos vencidos: leads a los que tocaba volver y nadie ha vuelto.
       Va sobre la cola de ayuda porque son los únicos chats que NO se van a
       manifestar solos: el cliente que dijo "lo consulto y te aviso" no vuelve
       a escribir, así que nunca sube en la bandeja. -->
  <section class="followup-rail" id="followup-rail" hidden>
    <div class="followup-rail-head">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10"></circle>
        <polyline points="12 6 12 12 16 14"></polyline>
      </svg>
      <span>Seguimientos vencidos — tocaba volver a escribirles</span>
    </div>
    <div class="followup-rail-chips" id="followup-rail-chips"></div>
  </section>

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
    <p>Cuando tus clientes escriban por WhatsApp, sus chats con Don Regalo aparecerán aquí en tiempo real.</p>
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
        <!-- Sin esto, "quién atiende qué" no se puede consultar: la bandeja
             ordena por recencia y los chats de uno quedan repartidos entre los
             de todos. -->
        <div class="scope-tabs" role="tablist" aria-label="Filtrar conversaciones">
          <button type="button" class="scope-tab is-active" data-scope="all" role="tab" aria-selected="true">Todas</button>
          <button type="button" class="scope-tab" data-scope="mine" role="tab" aria-selected="false">Mías</button>
          <button type="button" class="scope-tab" data-scope="free" role="tab" aria-selected="false">Sin asignar</button>
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
            <div class="chat-chips">
              <!-- Quién lo tiene. Antes "Tomar conversación" pasaba el chat a
                   HUMAN sin decir a qué humano, y dos asesores podían estar
                   escribiéndole cosas distintas al mismo cliente. -->
              <span class="chip-assign" id="chip-assign" hidden></span>
              <!-- Cuánto queda de la ventana de 24h de WhatsApp. -->
              <span class="chip-window" id="chip-window" hidden></span>
            </div>
          </div>
          <!-- La mayoría de las ventas del CRM las cierra un asesor, no el bot,
               y hasta ahora no quedaban registradas en ninguna parte. -->
          <button type="button" class="btn btn-secondary" id="btn-sale" title="Registrar una venta cerrada por ti">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <line x1="12" y1="1" x2="12" y2="23"></line>
              <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path>
            </svg>
            Registrar venta
          </button>
          <button type="button" class="btn btn-primary" id="btn-human" hidden>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <!-- El texto lo pone el JS: "Tomar conversación" si está libre,
                 "Tomar de todas formas" si la tiene otro asesor. -->
            <span class="btn-label">Tomar conversación</span>
          </button>
          <!-- Aquí hubo un "Quitar de la cola" que sacaba el chat de la franja
               SIN devolvérselo al bot. No era un estado: dejaba el chat en modo
               HUMAN y mudo, fuera de la vista y asignado a un asesor que ya
               había terminado, hasta que el releaser lo pasaba a AI a los 20 min
               por su cuenta. Quien de verdad quiere quedarse el chat tiene
               «Mantener humano», que sí frena al releaser. -->
          <button type="button" class="btn btn-secondary" id="btn-ai" hidden>Devolver a Don Regalo</button>
          <button type="button" class="icon-btn icon-btn-outline" id="btn-lead" title="Resumen del lead" aria-label="Resumen del lead">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
              <circle cx="12" cy="7" r="4"></circle>
            </svg>
          </button>
        </div>

        <!-- Venta cerrada por el agente: el pedido, ya listo, para que el
             vendedor no lo reconstruya leyendo el hilo. -->
        <div class="sale-card-wrap" id="sale-card" hidden></div>

        <!-- El anuncio que trajo el lead. "¡Hola! Quiero más información." no lo
             escribió el cliente: es el mensaje predefinido de un anuncio, y
             varios anuncios comparten el mismo texto. Aquí se dice cuál fue. -->
        <div class="ad-card-wrap" id="ad-card" hidden></div>
        <div class="thread" id="thread"></div>

        <!-- Bot al mando: sin composer, hay que tomar la conversación -->
        <div class="ai-banner" id="ai-banner" hidden>
          <span>Don Regalo está a cargo de este chat. Toma la conversación para responder tú.</span>
          <button type="button" class="btn btn-primary" id="btn-take">Tomar</button>
        </div>

        <!-- Modo HUMAN: el asesor responde -->
        <div class="composer-wrap" id="composer-wrap" hidden>

          <!-- Ventana de 24h agotada. Antes esto no se sabía: el mensaje se
               encolaba, Meta lo rechazaba y el panel decía "No se envió" sin
               ningún motivo, así que el asesor reintentaba y volvía a fallar. -->
          <div class="window-banner" id="window-banner" hidden>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
              <line x1="12" y1="9" x2="12" y2="13"></line>
              <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
            <span id="window-banner-text"></span>
          </div>

          <div class="human-return-banner" id="human-return-banner">
            <span>Cuando termines → <strong>Devolver a Don Regalo</strong> para que el bot siga el chat.</span>
            <label class="keep-human-label" title="Evita el auto-retorno del bot">
              <input type="checkbox" id="keep-human" />
              Mantener humano
            </label>
            <button type="button" class="btn btn-secondary" id="btn-ai-banner">Devolver a Don Regalo</button>
          </div>

          <!-- Mensaje al que se está respondiendo (clic derecho → Responder) -->
          <div class="reply-bar" id="reply-bar" hidden>
            <div class="reply-bar-body">
              <div class="reply-bar-title">Respondiendo a</div>
              <div class="reply-bar-text" id="reply-bar-text"></div>
            </div>
            <button type="button" class="icon-btn" id="reply-cancel" aria-label="Cancelar respuesta" title="Cancelar respuesta">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>

          <!-- Adjuntos elegidos, aún sin enviar (varios permitidos) -->
          <div class="attach-preview" id="attach-preview" hidden>
            <div class="attach-list" id="attach-list"></div>
            <button type="button" class="icon-btn" id="attach-clear" aria-label="Quitar todos los adjuntos" title="Quitar todos">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>

          <!-- Grabando nota de voz -->
          <div class="recording-bar" id="recording-bar" hidden>
            <span class="rec-dot"></span>
            <span class="rec-label">Grabando… <strong id="rec-time">0:00</strong></span>
            <button type="button" class="btn btn-secondary" id="rec-cancel">Cancelar</button>
            <button type="button" class="btn btn-primary" id="rec-stop">Listo</button>
          </div>

          <!-- Mensajes rápidos: escribir / en el draft -->
          <div class="slash-menu" id="slash-menu" hidden role="listbox" aria-label="Mensajes rápidos"></div>

          <form class="composer" id="composer">
            <input type="file" id="file-input" hidden multiple
                   accept="image/*,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip" />

            <button type="button" class="icon-btn icon-btn-outline" id="btn-attach" title="Adjuntar archivo" aria-label="Adjuntar archivo">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
              </svg>
            </button>

            <button type="button" class="icon-btn icon-btn-outline" id="btn-record" title="Grabar nota de voz" aria-label="Grabar nota de voz">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                <line x1="12" y1="19" x2="12" y2="23"></line>
              </svg>
            </button>

            <textarea class="input" id="draft" rows="1" placeholder="Escribe como asesor… ( / mensajes rápidos )"></textarea>

            <button type="submit" class="btn btn-primary btn-round" id="btn-send" aria-label="Enviar">
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <line x1="22" y1="2" x2="11" y2="13"></line>
                <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
              </svg>
            </button>
          </form>
        </div>
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

        <!-- Seguimientos: lo único que impide que el lead que dijo "lo consulto
             y te aviso" se hunda en la bandeja y no vuelva nunca. -->
        <section class="lead-block">
          <h5>Seguimientos</h5>
          <form class="followup-form" id="followup-form">
            <input class="input" id="followup-reason" type="text" maxlength="255"
                   placeholder="¿Para qué? Ej: confirmar si toma el desayuno grande"
                   aria-label="Motivo del seguimiento" />
            <div class="followup-when">
              <input class="input" id="followup-when" type="datetime-local"
                     aria-label="Cuándo retomar" />
              <button type="submit" class="btn btn-secondary btn-sm">Programar</button>
            </div>
            <!-- Presets: el 90% de los seguimientos son "mañana" o "en 2 días",
                 y obligar a rellenar un datetime a mano para eso hace que no se
                 programe ninguno. -->
            <div class="followup-presets">
              <button type="button" class="preset-chip" data-hours="3">En 3 h</button>
              <button type="button" class="preset-chip" data-preset="tomorrow">Mañana 9:00</button>
              <button type="button" class="preset-chip" data-days="2">En 2 días</button>
            </div>
          </form>
          <div class="followup-list" id="followup-list"></div>
        </section>

        <!-- Notas internas: lo que sabe el equipo y el cliente NO ve. -->
        <section class="lead-block">
          <h5>Notas internas <span class="lead-block-hint">solo las ve el equipo</span></h5>
          <form class="note-form" id="note-form">
            <textarea class="input" id="note-text" rows="2" maxlength="4000"
                      placeholder="Ej: pide factura, falta el RUC"
                      aria-label="Nueva nota interna"></textarea>
            <button type="submit" class="btn btn-secondary btn-sm">Guardar nota</button>
          </form>
          <div class="note-list" id="note-list"></div>
        </section>
      </div>
    </aside>
  </div>
</div>

<!-- Menú de clic derecho sobre un mensaje del hilo -->
<div class="msg-menu" id="msg-menu" role="menu" hidden>
  <button type="button" class="msg-menu-item" data-action="reply" role="menuitem">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="9 17 4 12 9 7"></polyline><path d="M20 18v-2a4 4 0 0 0-4-4H4"></path>
    </svg>
    <span>Responder</span>
  </button>
  <button type="button" class="msg-menu-item" data-action="copy" role="menuitem">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2"></rect>
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
    </svg>
    <span>Copiar</span>
  </button>
</div>

<!-- Registrar una venta que cerró el asesor.
     `crm_ventas_historiales` solo recogía los cierres del bot, y el chat que
     llega a un humano es justo el que el bot NO pudo cerrar: la mayor parte de
     lo que se vende por el CRM no existía en ningún registro. -->
<dialog class="sale-dialog" id="sale-dialog" aria-labelledby="sale-dialog-title">
  <form method="dialog" class="sale-dialog-form" id="sale-form">
    <div class="sale-dialog-head">
      <h4 id="sale-dialog-title">Registrar venta</h4>
      <button type="button" class="icon-btn" id="sale-dialog-close" aria-label="Cerrar">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>
    <p class="sale-dialog-lead" id="sale-dialog-lead"></p>

    <div class="field">
      <label for="sale-producto">Producto <span aria-hidden="true">*</span></label>
      <input class="input" id="sale-producto" name="producto" type="text" required maxlength="255"
             placeholder="Desayuno Brunch Feliz Cumpleaños" />
    </div>

    <div class="sale-dialog-grid">
      <div class="field">
        <label for="sale-monto">Monto (S/)</label>
        <input class="input" id="sale-monto" name="monto_sol" type="number" min="0" step="0.10" inputmode="decimal" />
      </div>
      <div class="field">
        <label for="sale-envio">Envío (S/)</label>
        <input class="input" id="sale-envio" name="envio_sol" type="number" min="0" step="0.10" inputmode="decimal" />
      </div>
      <div class="field">
        <label for="sale-distrito">Distrito</label>
        <input class="input" id="sale-distrito" name="distrito" type="text" maxlength="120" />
      </div>
      <div class="field">
        <label for="sale-pedido">Nº pedido temporal</label>
        <input class="input" id="sale-pedido" name="pedido_temporal_id" type="number" min="1" step="1" />
      </div>
      <div class="field">
        <label for="sale-fecha">Fecha de entrega</label>
        <input class="input" id="sale-fecha" name="fecha" type="date" />
      </div>
      <div class="field">
        <label for="sale-horario">Horario</label>
        <input class="input" id="sale-horario" name="horario" type="text" maxlength="80"
               placeholder="9:00 a 13:00" />
      </div>
    </div>

    <div class="field">
      <label for="sale-motivo">Nota</label>
      <input class="input" id="sale-motivo" name="motivo" type="text" maxlength="255"
             placeholder="Pagó por Yape, comprobante en el chat" />
    </div>

    <div class="sale-dialog-error" id="sale-dialog-error" role="alert" hidden></div>

    <div class="sale-dialog-actions">
      <button type="button" class="btn btn-secondary" id="sale-cancel">Cancelar</button>
      <button type="submit" class="btn btn-primary" id="sale-submit">Registrar venta</button>
    </div>
  </form>
</dialog>

<div class="alert error-box" id="error-box" role="alert" hidden></div>

<script src="<?= e(url_to('assets/inbox-time.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/inbox-time.js') ?>"></script>
<script src="<?= e(url_to('assets/inbox.js')) ?>?v=<?= (int) @filemtime(dirname(__DIR__) . '/public/assets/inbox.js') ?>"></script>
