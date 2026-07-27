(() => {
  const root = document.getElementById("inbox-app");
  if (!root) return;

  /**
   * Base pública del CRM. Si config.base_path está vacío pero la app vive en
   * /crm/public/, el fetch iría a /api/... (404 HTML) y el inbox quedaría vacío
   * sin error claro. Inferimos la carpeta desde la URL actual.
   */
  function detectBase() {
    const path = window.location.pathname || "";
    const folder = path.match(/^(.*\/crm\/public)(?:\/|$)/i);
    if (folder) return folder[1].replace(/\/$/, "");
    const configured = (root.dataset.base || "").replace(/\/$/, "");
    if (configured) return configured;
    if (/\/[^/]+\.php$/i.test(path)) {
      return path.replace(/\/[^/]+\.php$/i, "") || "";
    }
    return path.replace(/\/$/, "") || "";
  }

  const base = detectBase();
  const apiBase = `${base}/api`;
  const pollList = Number(root.dataset.pollList || 4000);
  const pollThread = Number(root.dataset.pollThread || 4000);
  const {
    parseTimestamp: parseTs,
    clockLabel: timeLabel,
    conversationTimeLabel,
    localDayKey,
  } = window.CrmInboxTime;

  const el = {
    rail: document.getElementById("help-rail"),
    railChips: document.getElementById("help-rail-chips"),
    followupRail: document.getElementById("followup-rail"),
    followupRailChips: document.getElementById("followup-rail-chips"),
    inboxEmpty: document.getElementById("inbox-empty"),
    panes: document.getElementById("inbox-panes"),
    search: document.getElementById("conv-search"),
    count: document.getElementById("conv-count"),
    list: document.getElementById("conv-list"),
    chatPlaceholder: document.getElementById("chat-placeholder"),
    chatBody: document.getElementById("chat-body"),
    chatAvatar: document.getElementById("chat-avatar"),
    chatName: document.getElementById("chat-name"),
    chatDot: document.getElementById("chat-dot"),
    chatState: document.getElementById("chat-state-label"),
    btnBack: document.getElementById("btn-back"),
    btnHuman: document.getElementById("btn-human"),
    btnAi: document.getElementById("btn-ai"),
    btnAiBanner: document.getElementById("btn-ai-banner"),
    keepHuman: document.getElementById("keep-human"),
    humanReturnBanner: document.getElementById("human-return-banner"),
    btnTake: document.getElementById("btn-take"),
    btnLead: document.getElementById("btn-lead"),
    btnLeadClose: document.getElementById("btn-lead-close"),
    thread: document.getElementById("thread"),
    aiBanner: document.getElementById("ai-banner"),
    composerWrap: document.getElementById("composer-wrap"),
    composer: document.getElementById("composer"),
    draft: document.getElementById("draft"),
    slashMenu: document.getElementById("slash-menu"),
    btnSend: document.getElementById("btn-send"),
    fileInput: document.getElementById("file-input"),
    btnAttach: document.getElementById("btn-attach"),
    saleCard: document.getElementById("sale-card"),
    adCard: document.getElementById("ad-card"),
    btnRecord: document.getElementById("btn-record"),
    msgMenu: document.getElementById("msg-menu"),
    replyBar: document.getElementById("reply-bar"),
    replyBarText: document.getElementById("reply-bar-text"),
    replyCancel: document.getElementById("reply-cancel"),
    attachPreview: document.getElementById("attach-preview"),
    attachList: document.getElementById("attach-list"),
    attachClear: document.getElementById("attach-clear"),
    recBar: document.getElementById("recording-bar"),
    recTime: document.getElementById("rec-time"),
    recStop: document.getElementById("rec-stop"),
    recCancel: document.getElementById("rec-cancel"),
    leadPanel: document.getElementById("lead-panel"),
    leadAvatar: document.getElementById("lead-avatar"),
    leadName: document.getElementById("lead-name"),
    leadSub: document.getElementById("lead-sub"),
    leadFields: document.getElementById("lead-fields"),
    error: document.getElementById("error-box"),

    // Asignación de asesor y ventana de servicio de WhatsApp.
    chipAssign: document.getElementById("chip-assign"),
    chipWindow: document.getElementById("chip-window"),
    windowBanner: document.getElementById("window-banner"),
    windowBannerText: document.getElementById("window-banner-text"),
    scopeTabs: document.querySelectorAll(".scope-tab"),

    // Venta registrada por el asesor.
    btnSale: document.getElementById("btn-sale"),
    saleDialog: document.getElementById("sale-dialog"),
    saleForm: document.getElementById("sale-form"),
    saleDialogLead: document.getElementById("sale-dialog-lead"),
    saleDialogError: document.getElementById("sale-dialog-error"),
    saleDialogClose: document.getElementById("sale-dialog-close"),
    saleCancel: document.getElementById("sale-cancel"),
    saleSubmit: document.getElementById("sale-submit"),

    // Notas internas y seguimientos.
    noteForm: document.getElementById("note-form"),
    noteText: document.getElementById("note-text"),
    noteList: document.getElementById("note-list"),
    followupForm: document.getElementById("followup-form"),
    followupReason: document.getElementById("followup-reason"),
    followupWhen: document.getElementById("followup-when"),
    followupList: document.getElementById("followup-list"),
  };

  // Quién está usando el panel. Sin esto no se puede distinguir "lo tengo yo"
  // de "lo tiene otro", que es justo para lo que sirve la asignación.
  const currentUser = {
    id: Number(root.dataset.userId || 0) || null,
    name: root.dataset.userName || "",
  };

  const MAX_BYTES = 16 * 1024 * 1024;
  const MAX_ATTACH = 10;

  let conversations = [];
  let selectedId = null;
  let query = "";
  // Filtro de la bandeja: todas / mías / sin asignar.
  let scope = "all";
  let dueFollowups = []; // seguimientos vencidos, para el rail
  let railSig = "";
  let listSig = "";
  let threadSig = "";
  let pendingFiles = []; // [{ blob, name, kind }]
  let replyTo = null; // { waId, text } del mensaje que el asesor está citando
  let menuTarget = null; // fila del hilo sobre la que se abrió el menú

  // Envío optimista, como WhatsApp: al pulsar Enter el mensaje aparece YA en el
  // hilo con el relojito y el input queda libre. El envío real ocurre detrás.
  // ¿La ficha de la venta va plegada? Preferencia del asesor, no del chat: si la
  // plegó, es que prefiere el hilo. Plegada sigue mostrando el producto.
  let saleCollapsed = false;

  let pendingSends = []; // [{ id, convId, text, kind, failed }]
  let pendingSeq = 0;
  const sendQueues = new Map(); // convId → promesa encadenada (conserva el orden)
  let lastThread = null; // último {conv, messages, lead} pintado, para repintar

  /** Encadena por conversación: dos Enter seguidos llegan en orden, no a la vez. */
  function enqueueSend(convId, task) {
    const prev = sendQueues.get(convId) || Promise.resolve();
    const next = prev.then(task, task); // si el anterior falló, el siguiente sigue
    sendQueues.set(
      convId,
      next.catch(() => {})
    );
    return next;
  }

  // Mensajes rápidos fijos (editar aquí / redeploy CRM). Slash: /
  const QUICK_REPLIES = [
    {
      cmd: "formulario",
      label: "Pedir formulario de pedido",
      body:
        "Llene los siguientes datos en este formulario para registrar su pedido porfavor. Es importante nos avise una vez lo termine. No coloque comillas simples ' ni emojis.",
    },
    {
      cmd: "origen",
      label: "Encuesta: ¿dónde nos encontraste?",
      body:
        "Buenas tardes, Queríamos hacerle una consulta rápida 🙏 ¿Dónde nos encontraste? 👀\n1️⃣ Google\n2️⃣ Instagram\n3️⃣ TikTok\n4️⃣ Facebook\n5️⃣ RAPPI",
    },
    {
      cmd: "ubicacion",
      label: "Pedir pin de Google Maps (MZ/Lte)",
      body:
        "Dado que la ubicación es por MZ y Lte le pido compartir la ubicación exacta del lugar de entrega por Google maps, ya que puede tomar mucho tiempo llegar a la ubicación y la idea es llegar a tiempo.",
    },
  ];

  let slashOpen = false;
  let slashMatches = [];
  let slashIndex = 0;
  let slashRange = null; // { start, end } del token /cmd en el draft


  // ── utilidades ──────────────────────────────────────────────

  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  // URLs http/https dentro del texto de un mensaje. El bot manda enlaces
  // (formulario de pedido, ficha de producto, rastreo…) y en el CRM llegaban
  // como texto plano: el asesor tenía que copiarlos a mano.
  const URL_RE = /\bhttps?:\/\/[^\s<]+[^\s<.,;:!?)\]}'"]/gi;

  /**
   * Escapa el texto (anti-XSS) y CONVIERTE las URLs en enlaces clickeables.
   * Primero escapa —nunca insertamos HTML del cliente sin escapar— y recién
   * sobre el texto ya seguro reemplaza las URLs por <a>.
   */
  const linkify = (s) =>
    esc(s).replace(URL_RE, (url) => {
      const safe = url.replace(/"/g, "&quot;");
      return `<a class="msg-link" href="${safe}" target="_blank" rel="noopener noreferrer">${url}</a>`;
    });

  function initials(name, fallback = "?") {
    const parts = String(name ?? "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return fallback;
    return parts.slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  }

  function avatarClass(seed) {
    const key = String(seed ?? "");
    let sum = 0;
    for (let i = 0; i < key.length; i++) sum += key.charCodeAt(i);
    return `avatar-p${Math.abs(sum) % 3}`;
  }

  function minutesSince(value) {
    const d = parseTs(value);
    if (!d) return null;
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  }

  function waitLabel(value) {
    const mins = minutesSince(value);
    if (mins === null) return "";
    if (mins < 1) return "Recién";
    if (mins < 60) return `Esperando ${mins} min`;
    return `Esperando ${Math.floor(mins / 60)} h`;
  }

  /** Etiqueta del separador de día, como en WhatsApp: Hoy / Ayer / la fecha. */
  function dayKey(value) {
    const d = parseTs(value);
    if (!d) return null;
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function dayLabel(value) {
    const d = parseTs(value);
    if (!d) return null;

    const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
    const dias = Math.round((dayStart(new Date()) - dayStart(d)) / 86400000);

    if (dias === 0) return "Hoy";
    if (dias === 1) return "Ayer";

    const opciones = { day: "numeric", month: "long" };
    // Más de un año atrás: sin el año, la fecha engaña.
    if (dias >= 365) opciones.year = "numeric";
    return d.toLocaleDateString("es-PE", opciones);
  }

  function humanSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }

  /**
   * Estado visual. Ojo: 'help' es human_support (el bot pidió refuerzo) pero
   * el modo sigue siendo AI — por eso el composer solo se abre en HUMAN.
   */
  function statusOf(c) {
    if (c.human_support) return "help";
    return c.mode === "HUMAN" ? "human" : "ai";
  }

  const STATUS_META = {
    help: { badge: "AYUDA", tag: "tag-accent", label: "Necesita ayuda humana" },
    human: { badge: "HUMAN", tag: "tag-neutral", label: "Tú tienes el control" },
    ai: { badge: "AI", tag: "tag-accent-2", label: "Don Regalo escuchando" },
  };

  const displayName = (c) => c.contact?.name || c.contact?.wa_id || "Sin nombre";

  function showError(msg) {
    if (!msg) {
      el.error.hidden = true;
      el.error.textContent = "";
      return;
    }
    el.error.hidden = false;
    el.error.textContent = msg;
  }

  async function api(path, options = {}) {
    const res = await fetch(`${apiBase}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      credentials: "same-origin",
      ...options,
    });
    const json = await res.json().catch(() => ({}));
    if (res.status === 401) {
      window.location.href = `${base}/login.php`;
      throw new Error("Sesión expirada");
    }
    if (!res.ok) throw new Error(json.error || "Error de API");
    return json;
  }

  /** Sube un archivo y devuelve su clave de almacenamiento. */
  async function uploadMedia(fileOrBlob, filename) {
    const form = new FormData();
    form.append("file", fileOrBlob, filename);
    const res = await fetch(`${apiBase}/media`, {
      method: "POST",
      body: form, // sin Content-Type: el navegador pone el boundary
      credentials: "same-origin",
    });
    const json = await res.json().catch(() => ({}));
    if (res.status === 401) {
      window.location.href = `${base}/login.php`;
      throw new Error("Sesión expirada");
    }
    if (!res.ok) throw new Error(json.error || "No se pudo subir el archivo");
    return json;
  }

  // ── render: lista + rail ────────────────────────────────────

  const isMine = (c) => !!currentUser.id && c.assigned?.id === currentUser.id;

  function matchesScope(c) {
    if (scope === "mine") return isMine(c);
    if (scope === "free") return !c.assigned;
    return true;
  }

  function visibleConversations() {
    const q = query.toLowerCase();
    return conversations.filter((c) => {
      if (!matchesScope(c)) return false;
      if (!q) return true;
      const hay = [displayName(c), c.contact?.wa_id, c.last_message].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }

  /**
   * Etiqueta de la ventana de servicio de WhatsApp.
   *
   * `known: false` (la conversación no tiene ni un mensaje entrante) NO se
   * pinta: decir "quedan 24 h" sobre un dato que no tenemos es peor que no
   * decir nada, porque el asesor lo creería.
   */
  function windowChip(win) {
    if (!win || !win.known) return null;
    if (!win.open) return { text: "Ventana cerrada", state: "closed" };
    const mins = Number(win.minutes_left || 0);
    if (mins < 60) return { text: `Quedan ${mins} min`, state: "warn" };
    const horas = Math.floor(mins / 60);
    return {
      text: `Quedan ${horas} h`,
      // Por debajo de 2 h el asesor tiene que decidir YA si escribe.
      state: horas < 2 ? "warn" : "ok",
    };
  }

  /** Seguimiento pendiente que ya venció, para marcar la fila en la lista. */
  function followupOverdue(c) {
    const due = parseTs(c.next_followup_at);
    return !!due && due.getTime() <= Date.now();
  }

  function conversationItem(c) {
    const status = statusOf(c);
    const meta = STATUS_META[status];
    const btn = document.createElement("button");
    btn.type = "button";
    // Venta cerrada por el agente: el chat va en verde. El vendedor solo tiene que
    // entrar a cobrar; el pedido ya está cerrado y se muestra en la cabecera.
    const sold = !!c.sale;
    btn.className = `list-item${c.id === selectedId ? " active" : ""}${sold ? " is-sold" : ""}`;

    const wait = status === "help" ? waitLabel(c.last_message_at) : "";
    // Quién lo tiene. "Tú" y otro asesor se distinguen a simple vista: lo que
    // hay que poder leer de un vistazo es "esto no es mío, no lo toco".
    const assignTag = c.assigned
      ? `<span class="tag ${isMine(c) ? "tag-mine" : "tag-taken"}" title="${esc(
          c.assigned.name || "Asesor"
        )}">${isMine(c) ? "TÚ" : esc(initials(c.assigned.name, "AS"))}</span>`
      : "";
    const followTag = followupOverdue(c)
      ? `<span class="tag tag-followup">⏰ SEGUIMIENTO</span>`
      : "";
    btn.innerHTML = `
      <div class="avatar-wrap">
        <div class="avatar ${avatarClass(c.contact?.wa_id || c.id)}">${esc(initials(displayName(c)))}</div>
        <span class="status-dot is-${status}"></span>
      </div>
      <div class="item-body">
        <div class="item-top">
          <span class="item-name">${esc(displayName(c))}</span>
          <span class="item-time">${esc(conversationTimeLabel(c.last_message_at))}</span>
        </div>
        <div class="item-phone">${esc(c.contact?.wa_id || "")}</div>
        <div class="item-preview">${esc(c.last_message || "Sin mensajes")}</div>
        <div class="item-foot">
          ${sold ? `<span class="tag tag-sold">💚 VENTA CERRADA</span>` : ""}
          ${c.is_new ? `<span class="tag tag-new">✨ NUEVO</span>` : ""}
          ${followTag}
          <span class="tag ${meta.tag}">${esc(meta.badge)}</span>
          ${assignTag}
          ${wait ? `<span class="item-wait">${esc(wait)}</span>` : ""}
        </div>
      </div>`;
    btn.addEventListener("click", () => select(c.id));
    return btn;
  }

  function railChip(c) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rail-chip";
    btn.innerHTML = `
      <span class="live-dot"></span>
      <span class="chip-name">${esc(displayName(c))}</span>
      <span class="chip-meta">${esc(waitLabel(c.last_message_at))}</span>
      <span class="chip-dismiss" role="button" tabindex="0" title="Devolver a Don Regalo" aria-label="Devolver a Don Regalo">×</span>`;
    btn.addEventListener("click", (e) => {
      if (e.target.closest(".chip-dismiss")) {
        e.preventDefault();
        e.stopPropagation();
        returnToBot(c.id);
        return;
      }
      select(c.id);
    });
    return btn;
  }

  /**
   * Chip de un seguimiento vencido. El ✓ lo cierra sin abrir el chat: despachar
   * la lista de la mañana no debería costar un clic de entrada y otro de salida
   * por cada lead.
   */
  function followupRailChip(f) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "rail-chip is-followup";
    const quien = f.contact?.name || f.contact?.wa_id || `Chat #${f.conversation_id}`;
    btn.innerHTML = `
      <span class="chip-name">${esc(quien)}</span>
      <span class="chip-meta">${esc(f.reason)}</span>
      <span class="chip-dismiss" role="button" tabindex="0" title="Marcar como hecho" aria-label="Marcar como hecho">✓</span>`;
    btn.addEventListener("click", (e) => {
      if (e.target.closest(".chip-dismiss")) {
        e.preventDefault();
        e.stopPropagation();
        closeFollowup(f.id, "hecho");
        return;
      }
      select(f.conversation_id);
    });
    return btn;
  }

  function renderFollowupRail() {
    if (!el.followupRail) return;
    const sig = JSON.stringify(dueFollowups.map((f) => [f.id, f.status]));
    if (sig === railSig) return;
    railSig = sig;
    el.followupRail.hidden = dueFollowups.length === 0;
    el.followupRailChips.replaceChildren(...dueFollowups.map(followupRailChip));
  }

  function renderList() {
    const hasAny = conversations.length > 0;
    el.inboxEmpty.hidden = hasAny;
    el.panes.hidden = !hasAny;

    const helpQueue = conversations.filter((c) => statusOf(c) === "help");
    el.rail.hidden = helpQueue.length === 0;
    el.railChips.replaceChildren(...helpQueue.map(railChip));

    const rows = visibleConversations();
    el.count.textContent =
      rows.length === conversations.length
        ? `${conversations.length} conversaciones`
        : `${rows.length} de ${conversations.length} conversaciones`;

    if (!rows.length) {
      // El motivo importa: "no tienes chats asignados" y "la búsqueda no
      // encontró nada" mandan al asesor a sitios distintos.
      const vacio =
        scope === "mine"
          ? "No tienes ninguna conversación asignada. Toma una de la lista «Todas»."
          : scope === "free"
            ? "No hay conversaciones sin asignar."
            : "Ningún chat coincide con la búsqueda.";
      el.list.innerHTML = `<div class="list-note">${esc(vacio)}</div>`;
      return;
    }
    el.list.replaceChildren(...rows.map(conversationItem));
  }

  // ── render: hilo ────────────────────────────────────────────

  /** El medio es una clave de storage (se sirve por media.php) o una URL absoluta. */
  function mediaSrc(m) {
    if (!m.media_url) return null;
    if (m.media_external) return m.media_url;
    return `${base}/media.php?f=${encodeURIComponent(m.media_url)}`;
  }

  /** "[audio]" / "[image]" son marcadores del agente, no texto real del cliente. */
  const isPlaceholder = (text) => /^\[[^\]]*\]$/.test(String(text || "").trim());

  const DOC_ICON =
    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>' +
    '<polyline points="14 2 14 8 20 8"></polyline></svg>';

  function mediaMarkup(m) {
    const src = mediaSrc(m);
    if (!src) return "";
    const kind = m.media_kind || "document";

    if (kind === "image") {
      return `<a class="media-link" href="${esc(src)}" target="_blank" rel="noopener">
        <img class="media-img" src="${esc(src)}" alt="Imagen enviada" loading="lazy" />
      </a>`;
    }
    if (kind === "audio") {
      return `<audio class="media-audio" controls preload="none" src="${esc(src)}"></audio>`;
    }

    const label = !isPlaceholder(m.content) && m.content ? m.content : "Documento";
    return `<a class="media-doc" href="${esc(src)}" target="_blank" rel="noopener" download>
      ${DOC_ICON}<span>${esc(label)}</span>
    </a>`;
  }

  /**
   * El mensaje al que el cliente respondió, como la cita de WhatsApp. Sin esto el
   * asesor ve un "quiero este" suelto y no sabe a qué producto se refería.
   * Se recorta: la cita es contexto, no el mensaje.
   */
  function quotedMarkup(m) {
    const quoted = String(m.quoted_text || "").trim();
    // La foto citada. El asesor manda un ramo, el cliente responde a ESA foto
    // ("podría optar por esta opción?") y antes aquí salía el literal "[image]":
    // justo cuando el lead elige, el vendedor no veía qué había elegido. Si el
    // asesor mandó varias fotos seguidas, era imposible de reconstruir.
    const thumbSrc = mediaSrc({
      media_url: m.quoted_media_url,
      media_external: m.quoted_media_external,
    });
    const thumb = thumbSrc
      ? `<a class="quoted-thumb" href="${esc(thumbSrc)}" target="_blank" rel="noopener">
           <img src="${esc(thumbSrc)}" alt="Imagen citada" loading="lazy" />
         </a>`
      : "";

    // La cita de un producto trae la URL de la foto delante: no aporta al asesor.
    const clean = quoted
      .split("\n")
      .filter((line) => !/^\s*https?:\/\/\S+\s*$/.test(line))
      .join(" ")
      .trim();
    // Con miniatura, "[image]" sobra: la imagen ya dice lo que era.
    const texto = clean && !isPlaceholder(clean) ? clean : thumb ? "Foto" : "";
    if (!texto && !thumb) return "";

    const short = texto.length > 140 ? `${texto.slice(0, 137)}…` : texto;
    return `<div class="quoted${thumb ? " has-thumb" : ""}">
      ${thumb}${short ? `<span class="quoted-txt">${esc(short)}</span>` : ""}
    </div>`;
  }

  function bubble(m) {
    const inbound = m.direction === "inbound";
    const sender = inbound ? "contact" : m.sender_type === "agent" ? "agent" : "bot";
    const label =
      sender === "contact" ? "CONTACTO" : sender === "agent" ? "ASESOR" : "REGALITO · BOT";

    const row = document.createElement("div");
    row.className = `msg-row ${inbound ? "is-in" : "is-out"}`;
    // Para el menú de clic derecho: citar exige el id que le dio WhatsApp.
    if (m.wa_message_id) row.dataset.waId = m.wa_message_id;
    row.dataset.text = m.content || "";

    const media = mediaMarkup(m);
    // En un documento el texto ES el nombre del archivo: ya va dentro del propio enlace.
    const showText =
      m.content && !isPlaceholder(m.content) && !(media && m.media_kind === "document");

    row.innerHTML = `
      <div class="bubble from-${sender}${media ? " has-media" : ""}">
        <div class="who">${esc(label)}</div>
        ${quotedMarkup(m)}
        ${media}
        ${showText ? `<div class="txt">${linkify(m.content)}</div>` : ""}
        <div class="at">${esc(timeLabel(m.created_at))}</div>
      </div>`;
    return row;
  }

  /** Burbujas + nubesita de día (Hoy / Ayer / fecha) al cambiar de jornada. */
  function threadNodes(messages) {
    const nodes = [];
    let lastKey = null;

    for (const m of messages) {
      const key = dayKey(m.created_at);
      const label = dayLabel(m.created_at) || (key ? key : null);
      if (key && key !== lastKey) {
        lastKey = key;
        const sep = document.createElement("div");
        sep.className = "day-sep";
        sep.setAttribute("role", "separator");
        sep.innerHTML = `<span>${esc(label || "Hoy")}</span>`;
        nodes.push(sep);
      }
      nodes.push(bubble(m));
    }
    return nodes;
  }

  function renderLead(conv, lead) {
    const name = conv.contact?.name || conv.contact?.wa_id || "—";
    el.leadAvatar.className = `avatar lead-avatar ${avatarClass(conv.contact?.wa_id || conv.id)}`;
    el.leadAvatar.textContent = initials(name);
    el.leadName.textContent = name;
    el.leadSub.textContent = STATUS_META[statusOf(conv)].label;

    const icons = {
      phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"></path>',
      tag: '<path d="M20.59 13.41 11 3.83A2 2 0 0 0 9.59 3.24H4a1 1 0 0 0-1 1v5.59a2 2 0 0 0 .59 1.41l9.58 9.59a2 2 0 0 0 2.83 0l4.59-4.59a2 2 0 0 0 0-2.83z"></path><circle cx="7.5" cy="7.5" r="1.2"></circle>',
      note: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline>',
      clock: '<circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline>',
    };

    const fields = [
      { icon: "phone", k: "Teléfono", v: conv.contact?.wa_id },
      { icon: "tag", k: "Interés actual", v: lead?.objetivo },
      { icon: "note", k: "Situación", v: lead?.situacion },
      { icon: "note", k: "Resumen", v: lead?.resumen },
    ];
    if (lead?.temperatura) {
      fields.push({ icon: "clock", k: "Temperatura", v: lead.temperatura, tag: true });
    }
    if (statusOf(conv) === "help") {
      fields.push({
        icon: "clock",
        k: "Urgencia",
        v: waitLabel(conv.last_message_at) || "Necesita ayuda",
        tag: true,
      });
    }

    el.leadFields.innerHTML = fields
      .filter((f) => f.v)
      .map(
        (f) => `
        <div class="lead-field">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[f.icon]}</svg>
          <div>
            <div class="k">${esc(f.k)}</div>
            ${f.tag ? `<span class="tag tag-accent">${esc(f.v)}</span>` : `<div class="v">${esc(f.v)}</div>`}
          </div>
        </div>`
      )
      .join("");
  }

  /**
   * Ficha del pedido que cerró el agente. Sin esto, el vendedor entra al chat y
   * tiene que reconstruir producto, distrito, fecha y horario leyendo veinte
   * mensajes hacia arriba.
   */
  /**
   * Ficha de la venta que cerró el agente. Se puede plegar: ocupaba media pantalla
   * sobre el hilo y no había forma de quitarla de en medio. Plegada NO desaparece
   * —deja el producto a la vista—, porque el verde existe justo para que el asesor
   * sepa que entra a cobrar algo ya cerrado.
   */
  function saleCard(sale) {
    if (!sale) return "";
    const fila = (etiqueta, valor) =>
      valor ? `<div class="sale-row"><span>${etiqueta}</span><b>${esc(String(valor))}</b></div>` : "";
    const envio =
      sale.envio_sol != null ? `S/${Number(sale.envio_sol).toFixed(2)}` : "";
    const monto =
      sale.monto_sol != null ? `S/${Number(sale.monto_sol).toFixed(2)}` : "";
    // Quién la cerró. Una venta registrada a mano y una que cerró el bot valen
    // lo mismo para cobrar, pero no para saber a quién preguntarle por ella.
    const porAsesor = sale.origen === "asesor";
    const titulo = porAsesor
      ? `💚 Venta registrada${sale.registrado_por ? ` por ${esc(String(sale.registrado_por))}` : ""} — falta entregar`
      : "💚 Venta cerrada por Don Regalo — solo falta cobrar";
    // El pedido ya está creado en el panel: el asesor solo lo convierte, sin
    // recapturar nada. Sin este número tiene que buscarlo a mano.
    const pedido = sale.pedido_temporal_id
      ? `<div class="sale-row"><span>Pedido temporal</span><b>#${esc(String(sale.pedido_temporal_id))} — ya en el panel, solo conviértelo</b></div>`
      : "";

    return `
      <div class="sale-card${saleCollapsed ? " is-collapsed" : ""}">
        <button type="button" class="sale-head" id="sale-toggle"
                aria-expanded="${saleCollapsed ? "false" : "true"}"
                title="${saleCollapsed ? "Ver el pedido" : "Plegar el pedido"}">
          <span class="sale-head-text">
            ${titulo}${
              saleCollapsed && sale.producto ? `: ${esc(String(sale.producto))}` : ""
            }
          </span>
          <svg class="sale-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
              stroke-linejoin="round" aria-hidden="true">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>
        <div class="sale-body">
          ${fila("Producto", sale.producto)}
          ${fila("Monto", monto)}
          ${fila("Distrito", sale.distrito)}
          ${fila("Envío", envio)}
          ${fila("Fecha", sale.fecha)}
          ${fila("Horario", sale.horario)}
          ${pedido}
          <div class="sale-actions">
            <button type="button" class="sale-delivered" id="sale-delivered">
              ✓ Marcar como entregado
            </button>
          </div>
        </div>
      </div>`;
  }

  function toggleSaleCard() {
    saleCollapsed = !saleCollapsed;
    try {
      localStorage.setItem("dr.saleCollapsed", saleCollapsed ? "1" : "0");
    } catch {
      /* sin localStorage: no recuerda el pliegue, pero pliega igual */
    }
    repaintSaleCard();
  }

  function repaintSaleCard() {
    if (lastThread) {
      el.saleCard.innerHTML = saleCard(lastThread.conv.sale);
      el.saleCard.hidden = !lastThread.conv.sale;
    }
  }

  /**
   * De qué anuncio vino el lead.
   *
   * Varios clientes abren con "¡Hola! Quiero más información." y el asesor no
   * sabe por qué: no lo escribió el cliente, es el mensaje predefinido de un
   * anuncio de Click-to-WhatsApp. Y como toda la campaña comparte el mismo
   * texto, por el mensaje es imposible saber cuál. Aquí se dice, con el copy
   * que el cliente SÍ vio antes de escribir — que es el contexto que hacía
   * falta: si vio "DESAYUNOS SORPRESA", viene por desayunos.
   */
  function adCard(ad) {
    if (!ad) return "";
    const titular = ad.headline ? esc(String(ad.headline)) : "";
    const cuerpo = ad.body ? esc(String(ad.body)) : "";
    const enlace = ad.url
      ? `<a class="ad-link" href="${esc(String(ad.url))}" target="_blank" rel="noopener noreferrer">ver anuncio</a>`
      : "";
    return `
      <div class="ad-card">
        <div class="ad-head">📣 Vino de un anuncio ${enlace}</div>
        ${titular ? `<div class="ad-headline">${titular}</div>` : ""}
        ${cuerpo ? `<div class="ad-body">${cuerpo}</div>` : ""}
      </div>`;
  }

  function repaintAdCard(ad) {
    if (!el.adCard) return;
    el.adCard.innerHTML = adCard(ad);
    el.adCard.hidden = !ad;
  }

  async function markSaleDelivered() {
    if (selectedId == null || !lastThread?.conv?.sale) return;
    const confirmed = window.confirm(
      "¿Confirmas que este pedido fue entregado? " +
        "La ficha desaparecerá del chat y quedará disponible en Historial."
    );
    if (!confirmed) return;

    const button = el.saleCard.querySelector("#sale-delivered");
    if (button) button.disabled = true;
    try {
      await api(`/conversations/${selectedId}/sale/delivered`, {
        method: "PATCH",
        body: JSON.stringify({}),
      });
      lastThread.conv.sale = null;
      const listed = conversations.find((conversation) => conversation.id === selectedId);
      if (listed) listed.sale = null;
      el.chatBody.classList.remove("is-sold");
      listSig = "";
      repaintSaleCard();
      renderList();
      showError("");
    } catch (error) {
      showError(error.message || String(error));
      if (button) button.disabled = false;
    }
  }

  /**
   * Burbuja de un mensaje que aún viaja: se pinta al instante con el relojito, como
   * WhatsApp. El asesor no espera al round trip para seguir escribiendo.
   */
  function pendingBubble(p) {
    const row = document.createElement("div");
    row.className = "msg-row is-out";
    const icon = p.failed
      ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="13"></line><line x1="12" y1="16.5" x2="12" y2="16.5"></line></svg>'
      : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><polyline points="12 7 12 12 15 14"></polyline></svg>';
    row.innerHTML = `
      <div class="bubble from-agent is-pending${p.failed ? " is-failed" : ""}">
        <div class="who">ASESOR</div>
        ${p.quoted ? `<div class="quoted">${esc(p.quoted)}</div>` : ""}
        ${p.text ? `<div class="txt">${esc(p.text)}</div>` : ""}
        ${p.kind ? `<div class="txt pending-attach">📎 ${esc(p.kind)}</div>` : ""}
        <div class="at">${p.failed ? "No se envió" : "Enviando"} ${icon}</div>
      </div>`;
    return row;
  }

  /** Repinta el hilo con lo último que trajo el API + las burbujas optimistas. */
  function repaintThread() {
    if (lastThread) renderThread(lastThread.conv, lastThread.messages, lastThread.lead);
  }

  function renderThread(conv, messages, lead) {
    lastThread = { conv, messages, lead };
    el.chatPlaceholder.hidden = true;
    el.chatBody.hidden = false;

    const status = statusOf(conv);
    const name = displayName(conv);

    if (el.saleCard) {
      el.saleCard.innerHTML = saleCard(conv.sale);
      el.saleCard.hidden = !conv.sale;
    }
    repaintAdCard(conv.ad);
    el.chatBody.classList.toggle("is-sold", !!conv.sale);

    el.chatAvatar.className = `avatar ${avatarClass(conv.contact?.wa_id || conv.id)}`;
    el.chatAvatar.textContent = initials(name);
    el.chatName.textContent = name;
    el.chatDot.className = `dot-sm is-${status}`;
    el.chatState.textContent = `${STATUS_META[status].label} · ${conv.contact?.wa_id || ""}`;

    // Ojo: en producción (CRM_MODE=external) el handoff hace `set_mode(HUMAN)`,
    // que enciende modo HUMAN, `human_support` y apaga `bot_active` de una vez.
    // Así que un chat de la cola de atención NO está en AI, está en HUMAN con el
    // bot callado — que es por lo que basta este flag para el composer.
    renderAssignChip(conv);
    renderWindow(conv);

    const isHuman = conv.mode === "HUMAN";
    // El botón de tomar se oculta SOLO cuando el chat ya es tuyo.
    //
    // Antes se ocultaba en cuanto el modo era HUMAN, y ese es justo el caso que
    // llega desde el bot: el handoff hace `set_mode(HUMAN)` sin asignar a nadie,
    // así que el chat más urgente del panel —el de la cola de atención— era el
    // único que no se podía reclamar. Quedaba en manos humanas y sin dueño.
    const mine = isMine(conv);
    el.btnHuman.hidden = mine;
    el.btnHuman.querySelector(".btn-label").textContent = conv.assigned
      ? "Tomar de todas formas"
      : "Tomar conversación";
    el.btnAi.hidden = !isHuman;
    el.composerWrap.hidden = !isHuman;
    el.aiBanner.hidden = isHuman;

    // Los mensajes que aún viajan van al final, con su relojito. Entran en la firma
    // para que aparecer/desaparecer repinte el hilo.
    const enVuelo = pendingSends.filter((p) => p.convId === conv.id);
    const sig = JSON.stringify([
      messages.map((m) => m.id),
      enVuelo.map((p) => `${p.id}:${p.failed ? 1 : 0}`),
    ]);
    if (sig !== threadSig) {
      threadSig = sig;
      el.thread.replaceChildren(...threadNodes(messages), ...enVuelo.map(pendingBubble));
      el.thread.classList.remove("thread-anim");
      void el.thread.offsetWidth;
      el.thread.classList.add("thread-anim");
      el.thread.scrollTop = el.thread.scrollHeight;
    }

    renderLead(conv, lead);
  }

  // ── asignación de asesor ────────────────────────────────────

  function renderAssignChip(conv) {
    if (!el.chipAssign) return;
    const asignado = conv.assigned;
    el.chipAssign.hidden = !asignado;
    if (!asignado) return;
    const mio = !!currentUser.id && asignado.id === currentUser.id;
    el.chipAssign.className = `chip-assign${mio ? " is-mine" : ""}`;
    el.chipAssign.textContent = mio
      ? "La tienes tú"
      : `La atiende ${asignado.name || "otro asesor"}`;
  }

  /**
   * Toma la conversación: primero reclama, y solo si gana el claim la pasa a
   * HUMAN.
   *
   * El orden importa. Al revés —cambiar el modo y luego intentar reclamar— el
   * chat se quedaría en HUMAN aunque el claim lo hubiera ganado otro, o sea que
   * el asesor vería el composer abierto sobre un cliente que no es suyo.
   */
  async function takeConversation() {
    if (selectedId == null) return;
    const convId = selectedId;
    try {
      let res = await api(`/conversations/${convId}/claim`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (!res.claimed) {
        const quien = res.assigned?.name || "otro asesor";
        // Salida de emergencia para el supervisor: un turno que termina no
        // puede dejar un chat bloqueado hasta que el releaser lo suelte.
        const forzar = window.confirm(
          `Esta conversación la está atendiendo ${quien}. ` +
            `¿Quieres tomarla igualmente? Se le quitará a ${quien}.`
        );
        if (!forzar) return;
        res = await api(`/conversations/${convId}/claim`, {
          method: "POST",
          body: JSON.stringify({ force: true }),
        });
        if (!res.claimed) {
          showError("No se pudo tomar la conversación. Recarga e inténtalo de nuevo.");
          return;
        }
      }
      // `human_support: false` al tomarla: la franja pasa a significar "nadie
      // lo está atendiendo". Antes seguía gritando por un chat que un compañero
      // ya tenía abierto, y una alarma que suena cuando no pasa nada se ignora.
      // No es el viejo estado a medias —aquel dejaba el chat en HUMAN sin dueño
      // y fuera de la vista de todos—: ahora tiene dueño y sale en "Mías".
      await setMode("HUMAN", { human_support: false });
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  // ── ventana de servicio de WhatsApp (24 h) ──────────────────
  //
  // Pasadas 24h desde el último mensaje del cliente, la Cloud API rechaza el
  // texto libre. El panel no lo sabía: el mensaje se encolaba, moría con
  // `failed` y salía un "No se envió" sin motivo — el asesor reintentaba y
  // volvía a fallar. Ahora se ve ANTES de escribir.

  /** ¿Se puede escribir en el chat que está abierto? */
  function windowIsClosed() {
    const win = lastThread?.conv?.window;
    return !!win && win.known && !win.open;
  }

  function renderWindow(conv) {
    const chip = windowChip(conv.window);
    if (el.chipWindow) {
      el.chipWindow.hidden = !chip;
      if (chip) {
        el.chipWindow.className = `chip-window is-${chip.state}`;
        el.chipWindow.textContent = chip.text;
      }
    }

    const cerrada = !!conv.window && conv.window.known && !conv.window.open;
    if (el.windowBanner) {
      el.windowBanner.hidden = !cerrada;
      if (cerrada && el.windowBannerText) {
        const desde = conv.window.last_inbound_at
          ? `${dayLabel(conv.window.last_inbound_at) || ""} ${timeLabel(
              conv.window.last_inbound_at
            )}`.trim()
          : "hace más de 24 h";
        el.windowBannerText.textContent =
          `WhatsApp no deja escribir: el cliente no escribe desde ${desde}. ` +
          `Hay que esperar a que vuelva a escribir, o contactarlo con una ` +
          `plantilla aprobada por Meta.`;
      }
    }
    // El composer se bloquea entero: dejar escribir para que el envío falle
    // después es hacerle perder el mensaje al asesor.
    if (el.draft) {
      el.draft.disabled = cerrada;
      el.draft.placeholder = cerrada
        ? "Ventana de 24 h cerrada — el cliente tiene que escribir primero"
        : "Escribe como asesor… ( / mensajes rápidos )";
    }
    if (el.btnSend) el.btnSend.disabled = cerrada;
    if (el.btnAttach) el.btnAttach.disabled = cerrada;
    if (el.btnRecord) el.btnRecord.disabled = cerrada;
  }

  // ── notas internas ──────────────────────────────────────────

  function renderNotes(notes) {
    if (!el.noteList) return;
    if (!notes.length) {
      el.noteList.innerHTML = `<p class="lead-block-empty">Sin notas todavía.</p>`;
      return;
    }
    el.noteList.innerHTML = notes
      .map(
        (n) => `<article class="note-item">
          <div class="note-body">${linkify(n.text)}</div>
          <div class="note-meta">${esc(n.author || "Asesor")} · ${esc(
            `${dayLabel(n.created_at) || ""} ${timeLabel(n.created_at)}`.trim()
          )}</div>
        </article>`
      )
      .join("");
  }

  async function loadNotes(convId) {
    try {
      const json = await api(`/conversations/${convId}/notes`);
      // El asesor pudo cambiar de chat mientras viajaba la respuesta: pintar
      // las notas de otro cliente sobre este chat es peor que no pintarlas.
      if (convId !== selectedId) return;
      renderNotes(json.data || []);
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function submitNote(event) {
    event.preventDefault();
    if (selectedId == null) return;
    const convId = selectedId;
    const text = (el.noteText.value || "").trim();
    if (!text) return;
    try {
      await api(`/conversations/${convId}/notes`, {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      el.noteText.value = "";
      await loadNotes(convId);
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  // ── seguimientos ────────────────────────────────────────────

  const FOLLOWUP_LABEL = { pendiente: "Pendiente", hecho: "Hecho", cancelado: "Cancelado" };

  function renderFollowups(items) {
    if (!el.followupList) return;
    if (!items.length) {
      el.followupList.innerHTML = `<p class="lead-block-empty">Sin seguimientos programados.</p>`;
      return;
    }
    el.followupList.innerHTML = items
      .map((f) => {
        const due = parseTs(f.due_at);
        const vencido = f.status === "pendiente" && !!due && due.getTime() <= Date.now();
        const cuando = `${dayLabel(f.due_at) || ""} ${timeLabel(f.due_at)}`.trim();
        return `<article class="followup-item is-${esc(f.status)}${vencido ? " is-overdue" : ""}">
          <div class="followup-when-label">${esc(cuando)}${
            vencido ? ' <span class="followup-overdue">vencido</span>' : ""
          }</div>
          <div class="followup-reason">${esc(f.reason)}</div>
          <div class="followup-meta">
            <span>${esc(FOLLOWUP_LABEL[f.status] || f.status)} · ${esc(f.author || "Asesor")}</span>
            ${
              f.status === "pendiente"
                ? `<span class="followup-actions">
                     <button type="button" class="link-btn" data-followup="${f.id}" data-status="hecho">Hecho</button>
                     <button type="button" class="link-btn" data-followup="${f.id}" data-status="cancelado">Cancelar</button>
                   </span>`
                : `<button type="button" class="link-btn" data-followup="${f.id}" data-status="pendiente">Reabrir</button>`
            }
          </div>
        </article>`;
      })
      .join("");
  }

  async function loadFollowups(convId) {
    try {
      const json = await api(`/conversations/${convId}/followups`);
      if (convId !== selectedId) return;
      renderFollowups(json.data || []);
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  /** Los vencidos de TODO el tenant: es lo que alimenta el rail de arriba. */
  async function loadDueFollowups() {
    try {
      const json = await api("/followups");
      dueFollowups = json.data || [];
      renderFollowupRail();
    } catch (err) {
      // El rail es un extra: si falla, no se tumba el inbox entero.
      dueFollowups = [];
      renderFollowupRail();
    }
  }

  /** Fecha local en el formato que quiere <input type="datetime-local">. */
  function localInputValue(date) {
    const pad = (n) => String(n).padStart(2, "0");
    return (
      `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
      `T${pad(date.getHours())}:${pad(date.getMinutes())}`
    );
  }

  async function submitFollowup(event) {
    event.preventDefault();
    if (selectedId == null) return;
    const convId = selectedId;
    const reason = (el.followupReason.value || "").trim();
    const when = el.followupWhen.value;
    if (!reason) {
      showError("Ponle un motivo al seguimiento: en tres días no vas a recordar cuál era.");
      return;
    }
    if (!when) {
      showError("Falta cuándo retomar el seguimiento.");
      return;
    }
    try {
      await api(`/conversations/${convId}/followups`, {
        method: "POST",
        // El input local ya viene en hora del asesor; el servidor guarda tal cual.
        body: JSON.stringify({ reason, when: when.replace("T", " ") }),
      });
      el.followupReason.value = "";
      el.followupWhen.value = "";
      await Promise.all([loadFollowups(convId), loadDueFollowups()]);
      listSig = "";
      await loadList();
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function closeFollowup(id, status) {
    try {
      await api(`/followups/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      const tareas = [loadDueFollowups()];
      if (selectedId != null) tareas.push(loadFollowups(selectedId));
      await Promise.all(tareas);
      listSig = "";
      await loadList();
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  // ── venta registrada por el asesor ──────────────────────────

  function openSaleDialog() {
    if (selectedId == null || !el.saleDialog) return;
    el.saleForm.reset();
    el.saleDialogError.hidden = true;
    el.saleDialogError.textContent = "";
    el.saleDialogLead.textContent = lastThread
      ? `Cliente: ${displayName(lastThread.conv)} · ${lastThread.conv.contact?.wa_id || ""}`
      : "";
    el.saleDialog.showModal();
    document.getElementById("sale-producto")?.focus();
  }

  async function submitSale(event) {
    event.preventDefault();
    if (selectedId == null) return;
    const convId = selectedId;
    const data = Object.fromEntries(new FormData(el.saleForm).entries());
    if (!String(data.producto || "").trim()) {
      el.saleDialogError.hidden = false;
      el.saleDialogError.textContent = "El producto es obligatorio.";
      return;
    }

    el.saleSubmit.disabled = true;
    try {
      await api(`/conversations/${convId}/sale`, {
        method: "POST",
        body: JSON.stringify(data),
      });
      el.saleDialog.close();
      listSig = "";
      await Promise.all([loadThread(), loadList()]);
      showError("");
    } catch (err) {
      el.saleDialogError.hidden = false;
      el.saleDialogError.textContent = err.message || String(err);
    } finally {
      el.saleSubmit.disabled = false;
    }
  }

  // ── responder / copiar (clic derecho sobre un mensaje) ──────
  //
  // El vendedor pidió las acciones que WhatsApp da al pulsar un mensaje. NO hay
  // "Eliminar": la Cloud API no permite revocar un mensaje ya enviado, así que
  // solo se borraría del CRM y el cliente lo seguiría viendo en su teléfono — un
  // botón que miente es peor que no tenerlo.

  /** Texto legible de un mensaje para la cita: sin URLs de foto ni relleno. */
  function quotePreview(text) {
    const clean = String(text || "")
      .split("\n")
      .filter((line) => !/^\s*https?:\/\/\S+\s*$/.test(line))
      .join(" ")
      .trim();
    return clean.length > 120 ? `${clean.slice(0, 117)}…` : clean;
  }

  function setReplyTo(waId, text) {
    if (!waId) return;
    replyTo = { waId, text: quotePreview(text) || "Mensaje" };
    el.replyBarText.textContent = replyTo.text;
    el.replyBar.hidden = false;
    el.draft.focus();
  }

  function clearReplyTo() {
    replyTo = null;
    el.replyBar.hidden = true;
    el.replyBarText.textContent = "";
  }

  function hideMsgMenu() {
    menuTarget = null;
    el.msgMenu.hidden = true;
  }

  function openMsgMenu(row, x, y) {
    menuTarget = row;
    // Responder necesita el id de WhatsApp; un mensaje que aún no lo tiene
    // (en cola) se puede copiar pero no citar.
    const replyBtn = el.msgMenu.querySelector('[data-action="reply"]');
    replyBtn.hidden = !row.dataset.waId;

    el.msgMenu.hidden = false;
    // Se posiciona tras mostrarlo para poder medirlo y no salirse de pantalla.
    const menu = el.msgMenu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - menu.width - 8);
    const top = Math.min(y, window.innerHeight - menu.height - 8);
    el.msgMenu.style.left = `${Math.max(8, left)}px`;
    el.msgMenu.style.top = `${Math.max(8, top)}px`;
  }

  async function copyText(text) {
    const value = String(text || "");
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Sin permiso de portapapeles (o http sin TLS): copia por textarea oculto.
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
      } catch {
        showError("Tu navegador no permitió copiar. Selecciona el texto a mano.");
      }
      ta.remove();
    }
  }

  // ── adjuntos ────────────────────────────────────────────────

  const ATTACH_ICONS = {
    image:
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg>',
    audio:
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path></svg>',
    document: DOC_ICON,
  };

  function kindOfFile(file) {
    const type = file.type || "";
    if (type.startsWith("image/")) return "image";
    if (type.startsWith("audio/")) return "audio";
    return "document";
  }

  function renderPendingFiles() {
    if (!pendingFiles.length) {
      el.attachPreview.hidden = true;
      el.attachList.innerHTML = "";
      return;
    }
    el.attachPreview.hidden = false;
    el.attachList.innerHTML = pendingFiles
      .map(
        (f, i) => `<div class="attach-item">
          <div class="attach-icon">${ATTACH_ICONS[f.kind]}</div>
          <div class="attach-meta">
            <div class="attach-name">${esc(f.name)}</div>
            <div class="attach-size">${esc(humanSize(f.blob.size || 0))}</div>
          </div>
          <button type="button" class="icon-btn attach-remove" data-idx="${i}" aria-label="Quitar adjunto">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>`
      )
      .join("");
  }

  function addPendingFile(file, name) {
    if (file.size > MAX_BYTES) {
      showError(`El archivo pesa ${humanSize(file.size)}; el máximo es 16 MB.`);
      return;
    }
    if (pendingFiles.length >= MAX_ATTACH) {
      showError(`Máximo ${MAX_ATTACH} archivos por envío.`);
      return;
    }
    pendingFiles.push({
      blob: file,
      name: name || file.name || "archivo",
      kind: kindOfFile(file),
    });
    renderPendingFiles();
    showError("");
    el.draft.focus();
  }

  function clearPendingFiles() {
    pendingFiles = [];
    el.fileInput.value = "";
    renderPendingFiles();
  }

  function removePendingAt(idx) {
    pendingFiles.splice(idx, 1);
    renderPendingFiles();
  }

  // ── grabación de nota de voz ────────────────────────────────

  // WhatsApp solo acepta ogg/opus, mp3, aac o mp4. Chrome graba en webm/opus,
  // que WhatsApp rechaza: el agente lo convierte con ffmpeg antes de enviarlo.
  const REC_FORMATS = ["audio/ogg;codecs=opus", "audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
  const EXT_BY_FORMAT = { ogg: "ogg", mp4: "m4a", webm: "webm" };

  let recorder = null;
  let recChunks = [];
  let recTimer = null;
  let recStart = 0;
  let recCancelled = false;

  function pickRecordingFormat() {
    if (typeof MediaRecorder === "undefined") return null;
    return REC_FORMATS.find((f) => MediaRecorder.isTypeSupported(f)) || null;
  }

  function extForMime(mime) {
    const base = String(mime).split(";")[0];
    const sub = base.split("/")[1] || "webm";
    return EXT_BY_FORMAT[sub] || "webm";
  }

  function tickRecTime() {
    const secs = Math.floor((Date.now() - recStart) / 1000);
    const m = Math.floor(secs / 60);
    const s = String(secs % 60).padStart(2, "0");
    el.recTime.textContent = `${m}:${s}`;
  }

  async function startRecording() {
    const format = pickRecordingFormat();
    if (!format) {
      showError("Tu navegador no permite grabar audio. Adjunta un archivo en su lugar.");
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      showError("No se pudo usar el micrófono. Revisa los permisos del navegador.");
      return;
    }

    recChunks = [];
    recCancelled = false;
    recorder = new MediaRecorder(stream, { mimeType: format });

    recorder.addEventListener("dataavailable", (e) => {
      if (e.data && e.data.size) recChunks.push(e.data);
    });

    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(recTimer);
      el.recBar.hidden = true;
      el.composer.hidden = false;

      if (recCancelled || !recChunks.length) return;

      const blob = new Blob(recChunks, { type: format });
      const ext = extForMime(format);
      clearPendingFiles();
      addPendingFile(blob, `nota-de-voz.${ext}`);
    });

    recorder.start();
    recStart = Date.now();
    tickRecTime();
    recTimer = setInterval(tickRecTime, 500);
    el.recBar.hidden = false;
    el.composer.hidden = true;
    showError("");
  }

  function stopRecording(cancel) {
    if (!recorder || recorder.state === "inactive") return;
    recCancelled = !!cancel;
    recorder.stop();
    recorder = null;
  }

  // ── acciones ────────────────────────────────────────────────

  function select(id) {
    selectedId = id;
    threadSig = "";
    root.dataset.mobileChat = "true";
    clearPendingFiles();
    clearReplyTo(); // la cita es de un mensaje de ESE chat, no del nuevo
    hideMsgMenu();
    hideSlashMenu();
    // El panel lateral es de ESTE chat: dejar las notas del anterior a la vista
    // mientras cargan las nuevas es la forma más fácil de leer el contexto
    // equivocado y contestarle a un cliente lo que pidió otro.
    renderNotes([]);
    renderFollowups([]);
    renderList();
    loadThread();
    loadNotes(id);
    loadFollowups(id);
  }

  // ── aviso sonoro del handoff ────────────────────────────────
  //
  // Los vendedores no viven mirando el panel. Cuando el agente cede el control
  // ("necesita ayuda humana"), hay un cliente esperando AHORA. El pitido solo
  // suena en la TRANSICIÓN, nunca en cada refresco: un panel que pita cada cuatro
  // segundos se silencia el primer día y deja de servir para nada.

  let audioCtx = null;

  function beep() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      audioCtx = audioCtx || new AudioCtx();
      // Los navegadores bloquean el audio hasta que el usuario interactúa.
      if (audioCtx.state === "suspended") audioCtx.resume();

      const now = audioCtx.currentTime;
      [880, 1320].forEach((freq, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = "sine";
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(0.0001, now + i * 0.18);
        gain.gain.exponentialRampToValueAtTime(0.25, now + i * 0.18 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.18 + 0.16);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(now + i * 0.18);
        osc.stop(now + i * 0.18 + 0.18);
      });
    } catch (err) {
      /* sin audio no pasa nada: el chat igual se pinta */
    }
  }

  function notifyHandoff(conv) {
    beep();
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      new Notification("Don Regalo pidió ayuda", {
        body: `${displayName(conv)} necesita un asesor ahora.`,
        tag: `handoff-${conv.id}`,
      });
    }
  }

  function notifyNewLead(conv) {
    beep();
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      new Notification("Lead nuevo", {
        body: `${displayName(conv)} escribió por primera vez.`,
        tag: `lead-${conv.id}`,
      });
    }
  }

  /** Solo los que ACABAN de pasar a "necesita ayuda humana". */
  function alertOnHandoff(prev, next) {
    if (!prev.length) return; // primera carga: no es una transición
    const antes = new Set(prev.filter((c) => statusOf(c) === "help").map((c) => c.id));
    const nuevos = next.filter((c) => statusOf(c) === "help" && !antes.has(c.id));
    if (nuevos.length) notifyHandoff(nuevos[0]);
  }

  // Ids ya avisados: el aviso es por lead, no por refresco. Sin esto, una
  // conversación que sigue siendo "nueva" durante media hora pitaría en cada
  // tick de la lista.
  const leadsAvisados = new Set();

  /**
   * Solo la PRIMERA vez que un número escribe.
   *
   * No basta con "no estaba en la lista anterior": la lista está limitada a 80 y
   * ordenada por recencia, así que una conversación vieja puede reaparecer desde
   * abajo al llegarle un mensaje y no es un lead nuevo. Por eso manda `is_new`,
   * que el CRM calcula contra `fecha_creacion` de la conversación.
   */
  function alertOnNewLead(prev, next) {
    if (!prev.length) {
      // Primera carga: marcamos los que ya estaban como avisados para no soltar
      // una ráfaga de pitidos al abrir el panel por la mañana.
      next.forEach((c) => c.is_new && leadsAvisados.add(c.id));
      return;
    }
    const nuevos = next.filter((c) => c.is_new && !leadsAvisados.has(c.id));
    nuevos.forEach((c) => leadsAvisados.add(c.id));
    if (nuevos.length) notifyNewLead(nuevos[0]);
  }

  async function loadList() {
    try {
      const json = await api("/conversations");
      if (!json || !Array.isArray(json.data)) {
        throw new Error(
          `Respuesta inválida del API (${apiBase}/conversations). Revisa base_path en config.php.`
        );
      }
      const next = json.data;
      // La respuesta del API puede ser idéntica al cruzar medianoche. El día
      // local forma parte de la firma para que 15:00 cambie a "Ayer" sin que
      // tenga que llegar otro mensaje.
      const sig = `${localDayKey(new Date())}|${JSON.stringify(next)}`;
      if (sig !== listSig) {
        alertOnHandoff(conversations, next);
        alertOnNewLead(conversations, next);
        listSig = sig;
        conversations = next;
        renderList();
      }
      const meta = json.meta || {};
      if (
        next.length === 0 &&
        Number(meta.count_all_tenants || 0) > 0 &&
        Number(meta.count_all_tenants) > Number(meta.count || 0)
      ) {
        showError(
          `Hay ${meta.count_all_tenants} chat(s) en la BD pero 0 para el tenant ` +
            `"${meta.tenant_slug || "?"}" (id ${meta.tenant_id || "?"}). ` +
            `Revisa tenant_slug en config.php.`
        );
      } else {
        showError("");
      }
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function loadThread() {
    if (selectedId == null) return;
    try {
      const json = await api(`/conversations/${selectedId}`);
      renderThread(json.conversation, json.messages || [], json.lead);
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function setMode(mode, extra = {}) {
    if (selectedId == null) return;
    try {
      await api(`/conversations/${selectedId}/mode`, {
        method: "PATCH",
        body: JSON.stringify({ mode, ...extra }),
      });
      listSig = "";
      await Promise.all([loadList(), loadThread()]);
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  /**
   * La × de la franja: "Devolver a Don Regalo", sin abrir el chat.
   *
   * Antes esto apagaba solo `human_support`, y eso no era un estado: el chat
   * salía de la franja pero seguía en modo HUMAN y mudo, asignado a un asesor
   * que ya había terminado — hasta que el releaser lo pasaba a AI a los 20 min
   * por su cuenta. O sea que el bot lo retomaba igual, pero tarde y sin que
   * nadie lo hubiera decidido.
   *
   * Manda exactamente lo mismo que el botón "Devolver a Don Regalo"; lo único
   * que aporta es despachar la cola sin entrar chat por chat. Y quien SÍ quiere
   * quedarse la conversación tiene «Mantener humano», que frena al releaser.
   */
  async function returnToBot(conversationId) {
    const id = conversationId ?? selectedId;
    if (id == null) return;
    try {
      await api(`/conversations/${id}/mode`, {
        method: "PATCH",
        body: JSON.stringify({ mode: "AI", human_support: false, keep_human: false }),
      });
      listSig = "";
      const refresh = [loadList()];
      if (selectedId === id) refresh.push(loadThread());
      await Promise.all(refresh);
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function setKeepHuman(on) {
    if (selectedId == null) return;
    try {
      await api(`/conversations/${selectedId}/mode`, {
        method: "PATCH",
        body: JSON.stringify({ keep_human: !!on }),
      });
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  /**
   * Enter → el mensaje sale del input AL INSTANTE y aparece en el hilo con el
   * relojito, como WhatsApp. El envío real va detrás, encolado por conversación
   * para que dos Enter seguidos lleguen en orden. El asesor no espera al round
   * trip: antes el input se quedaba con el texto y los botones bloqueados.
   */
  function send(event) {
    event.preventDefault();
    if (selectedId == null) return;

    // Fijamos la conversación de ESTE envío. Un bloque de imágenes se sube una a
    // una (await uploadMedia/await api entre cada una); si el asesor cambia de
    // chat mientras tanto, `selectedId` ya apunta a otro cliente y las imágenes
    // restantes se irían a ese otro chat. `convId` las mantiene en su destino.
    const convId = selectedId;
    const content = (el.draft.value || "").trim();
    const attachments = pendingFiles.slice();
    // La cita se fija igual que la conversación: si el asesor cambia de chat
    // mientras se envía, esto ya viaja con su destino.
    const replyToWaId = replyTo?.waId || null;
    const quoted = replyTo?.text || null;
    if (!content && !attachments.length) return;

    // Ventana de 24h agotada: WhatsApp lo va a rechazar. Se corta aquí, con el
    // texto todavía en el composer — antes se enviaba, fallaba, y el asesor
    // perdía el mensaje sin saber por qué.
    if (windowIsClosed()) {
      showError(
        "No se puede enviar: pasaron más de 24 h desde el último mensaje del " +
          "cliente. Espera a que escriba o contáctalo con una plantilla aprobada."
      );
      return;
    }

    // Burbuja optimista y composer libre YA. Vaciar el borrador aquí es además lo
    // que evita el doble envío de dos Enter seguidos: el segundo sale vacío.
    const pending = {
      id: `p${++pendingSeq}`,
      convId,
      text: content,
      quoted,
      kind: attachments.length
        ? attachments.map((f) => f.name).join(", ")
        : "",
      failed: false,
    };
    pendingSends.push(pending);
    el.draft.value = "";
    el.draft.style.height = "auto";
    clearPendingFiles();
    clearReplyTo();
    hideSlashMenu();
    repaintThread();

    const settle = (ok) => {
      if (ok) {
        pendingSends = pendingSends.filter((p) => p.id !== pending.id);
      } else {
        pending.failed = true; // se queda a la vista: no se pierde en silencio
      }
      repaintThread();
    };

    // Escribir es tomar el chat. El handoff del bot lo deja en HUMAN sin dueño,
    // y quien contesta primero es de hecho quien lo está atendiendo: obligarle a
    // pulsar además un botón solo consigue que la asignación se quede vacía y el
    // módulo no sirva para nada. Va suelto y sin await: es contabilidad, no
    // puede retrasar el mensaje ni tumbarlo si falla.
    if (!lastThread?.conv?.assigned && lastThread?.conv?.id === convId) {
      api(`/conversations/${convId}/claim`, {
        method: "POST",
        body: JSON.stringify({}),
      }).catch(() => {});
    }

    enqueueSend(convId, async () => {
      try {
        if (!attachments.length) {
          const json = await api("/outbox", {
            method: "POST",
            body: JSON.stringify({
              conversation_id: convId,
              content,
              reply_to_wa_id: replyToWaId,
            }),
          });
          if (json.queued && json.pushed === false) {
            // No dar por enviado: el push al agente falló. La burbuja queda en rojo.
            settle(false);
            showError(
              json.warning ||
                "No se pudo enviar a WhatsApp. Revisa agent_base_url / tokens del agente."
            );
            return;
          }
        } else {
          // Un outbox por archivo (WA no agrupa álbumes desde Cloud API).
          // El texto del borrador va de pie en la primera imagen/doc.
          //
          // Cada archivo se contabiliza aparte. Antes un fallo en el segundo
          // hacía `return` y marcaba TODO el envío como "no se envió" — pero el
          // primero ya estaba en el WhatsApp del cliente. El asesor reintentaba
          // y le llegaba dos veces. Un fallo tampoco corta los siguientes: que
          // un archivo sea inválido no es razón para no mandar el resto.
          const fallidos = [];
          for (let i = 0; i < attachments.length; i++) {
            const file = attachments[i];
            try {
              const up = await uploadMedia(file.blob, file.name);
              const json = await api("/outbox", {
                method: "POST",
                body: JSON.stringify({
                  conversation_id: convId,
                  content: i === 0 ? content : "",
                  media_path: up.key,
                  filename: file.name,
                  // La cita solo tiene sentido en el primer adjunto: es la respuesta.
                  reply_to_wa_id: i === 0 ? replyToWaId : null,
                }),
              });
              if (json.queued && json.pushed === false) {
                fallidos.push({ name: file.name, why: json.warning || "el agente no respondió" });
              }
            } catch (err) {
              fallidos.push({ name: file.name, why: err.message || String(err) });
            }
          }
          if (fallidos.length) {
            // La burbuja se queda solo con lo que NO salió, para que reintentar
            // no reenvíe lo que el cliente ya tiene.
            pending.kind = fallidos.map((f) => f.name).join(", ");
            const enviados = attachments.length - fallidos.length;
            settle(false);
            showError(
              (enviados ? `Se enviaron ${enviados} de ${attachments.length}. ` : "") +
                fallidos.map((f) => `«${f.name}»: ${f.why}`).join(" · ")
            );
            listSig = "";
            await Promise.all([loadThread(), loadList()]);
            return;
          }
        }
        settle(true);
        listSig = "";
        await Promise.all([loadThread(), loadList()]);
        showError("");
      } catch (err) {
        settle(false);
        showError(err.message || String(err));
      }
    });
  }

  function toggleLeadPanel(force) {
    const collapsed = force ?? !el.leadPanel.classList.contains("collapsed");
    el.leadPanel.classList.toggle("collapsed", collapsed);
    try {
      localStorage.setItem("dr.leadPanelCollapsed", collapsed ? "1" : "0");
    } catch {
      /* almacenamiento no disponible: el panel no recuerda su estado */
    }
  }

  // ── mensajes rápidos (/) ────────────────────────────────────

  function hideSlashMenu() {
    slashOpen = false;
    slashMatches = [];
    slashIndex = 0;
    slashRange = null;
    if (el.slashMenu) {
      el.slashMenu.hidden = true;
      el.slashMenu.innerHTML = "";
    }
  }

  /** Detecta un token /cmd justo antes del caret. */
  function detectSlashToken(text, caret) {
    const before = text.slice(0, caret ?? text.length);
    const m = before.match(/(^|\s)\/([^\s]*)$/);
    if (!m) return null;
    const query = m[2];
    const start = before.length - query.length - 1;
    return { query: query.toLowerCase(), start, end: caret ?? text.length };
  }

  function filterQuickReplies(query) {
    const q = String(query || "").toLowerCase();
    if (!q) return QUICK_REPLIES.slice();
    return QUICK_REPLIES.filter(
      (r) =>
        r.cmd.includes(q) ||
        r.label.toLowerCase().includes(q) ||
        r.body.toLowerCase().includes(q)
    );
  }

  function renderSlashMenu() {
    if (!el.slashMenu || !slashOpen) return;
    if (!slashMatches.length) {
      el.slashMenu.hidden = false;
      el.slashMenu.innerHTML =
        `<div class="slash-empty">Sin coincidencias. Prueba /formulario, /origen o /ubicacion.</div>`;
      return;
    }
    el.slashMenu.hidden = false;
    el.slashMenu.innerHTML =
      `<div class="slash-menu-hint">Mensajes rápidos</div>` +
      slashMatches
        .map((r, i) => {
          const preview = String(r.body).replace(/\s+/g, " ").slice(0, 110);
          return `<button type="button" class="slash-item${i === slashIndex ? " is-active" : ""}" role="option" data-idx="${i}" aria-selected="${i === slashIndex}">
            <div class="slash-item-cmd">/${esc(r.cmd)}</div>
            <div class="slash-item-label">${esc(r.label)}</div>
            <div class="slash-item-preview">${esc(preview)}</div>
          </button>`;
        })
        .join("");
  }

  function refreshSlashMenu() {
    const text = el.draft.value || "";
    const caret = el.draft.selectionStart ?? text.length;
    const token = detectSlashToken(text, caret);
    if (!token) {
      hideSlashMenu();
      return;
    }
    slashRange = { start: token.start, end: token.end };
    slashMatches = filterQuickReplies(token.query);
    if (slashIndex >= slashMatches.length) slashIndex = Math.max(0, slashMatches.length - 1);
    slashOpen = true;
    renderSlashMenu();
  }

  function applyQuickReply(item) {
    if (!item || !slashRange) return;
    const text = el.draft.value || "";
    const before = text.slice(0, slashRange.start);
    const after = text.slice(slashRange.end);
    const next = before + item.body + after;
    el.draft.value = next;
    const pos = before.length + item.body.length;
    el.draft.focus();
    el.draft.setSelectionRange(pos, pos);
    el.draft.style.height = "auto";
    el.draft.style.height = `${Math.min(el.draft.scrollHeight, 120)}px`;
    hideSlashMenu();
  }

  function onDraftKeydown(e) {
    if (slashOpen && slashMatches.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        slashIndex = (slashIndex + 1) % slashMatches.length;
        renderSlashMenu();
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        slashIndex = (slashIndex - 1 + slashMatches.length) % slashMatches.length;
        renderSlashMenu();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        applyQuickReply(slashMatches[slashIndex]);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        applyQuickReply(slashMatches[slashIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        hideSlashMenu();
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      el.composer.requestSubmit();
    }
  }

  // ── enlaces ─────────────────────────────────────────────────

  // Tomar la conversación pasa por el claim: el modo HUMAN solo se activa si
  // nadie más la tiene ya.
  el.btnHuman.addEventListener("click", takeConversation);
  el.btnTake.addEventListener("click", takeConversation);
  el.btnAi.addEventListener("click", () => setMode("AI", { human_support: false, keep_human: false }));
  if (el.btnAiBanner) {
    el.btnAiBanner.addEventListener("click", () =>
      setMode("AI", { human_support: false, keep_human: false })
    );
  }
  if (el.keepHuman) {
    el.keepHuman.addEventListener("change", () => setKeepHuman(el.keepHuman.checked));
  }
  el.btnLead.addEventListener("click", () => toggleLeadPanel());
  el.btnLeadClose.addEventListener("click", () => toggleLeadPanel(true));
  el.btnBack.addEventListener("click", () => {
    root.dataset.mobileChat = "false";
  });

  // Los navegadores bloquean audio y notificaciones hasta que el usuario
  // interactúa con la página. Lo desbloqueamos en el primer clic del asesor; si no,
  // el primer handoff del día sonaría en silencio.
  document.addEventListener(
    "click",
    () => {
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
          audioCtx = audioCtx || new AudioCtx();
          if (audioCtx.state === "suspended") audioCtx.resume();
        }
        if ("Notification" in window && Notification.permission === "default") {
          Notification.requestPermission();
        }
      } catch (err) {
        /* sin audio ni notificaciones el panel sigue funcionando */
      }
    },
    { once: true }
  );

  el.composer.addEventListener("submit", send);

  el.btnAttach.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", () => {
    const files = el.fileInput.files;
    if (!files?.length) return;
    for (const file of files) addPendingFile(file);
    el.fileInput.value = "";
  });
  el.attachClear.addEventListener("click", clearPendingFiles);
  el.attachList.addEventListener("click", (e) => {
    const btn = e.target.closest(".attach-remove");
    if (!btn) return;
    removePendingAt(Number(btn.dataset.idx));
  });

  el.btnRecord.addEventListener("click", startRecording);
  el.recStop.addEventListener("click", () => stopRecording(false));
  el.recCancel.addEventListener("click", () => stopRecording(true));

  // Clic derecho sobre un mensaje → menú propio (Responder / Copiar).
  el.thread.addEventListener("contextmenu", (e) => {
    const row = e.target.closest(".msg-row");
    if (!row) return; // fuera de una burbuja: menú normal del navegador
    e.preventDefault();
    openMsgMenu(row, e.clientX, e.clientY);
  });

  el.msgMenu.addEventListener("click", (e) => {
    const btn = e.target.closest(".msg-menu-item");
    if (!btn || !menuTarget) return;
    const { waId, text } = menuTarget.dataset;
    if (btn.dataset.action === "reply") setReplyTo(waId, text);
    if (btn.dataset.action === "copy") copyText(text);
    hideMsgMenu();
  });

  // La ficha se repinta entera en cada render, así que el listener va delegado.
  el.saleCard.addEventListener("click", (e) => {
    if (e.target.closest("#sale-delivered")) {
      markSaleDelivered();
      return;
    }
    if (e.target.closest("#sale-toggle")) toggleSaleCard();
  });

  el.replyCancel.addEventListener("click", clearReplyTo);
  // Cerrar el menú con un clic fuera, Escape o al hacer scroll del hilo.
  document.addEventListener("click", (e) => {
    if (!el.msgMenu.hidden && !e.target.closest("#msg-menu")) hideMsgMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!el.msgMenu.hidden) hideMsgMenu();
    else if (replyTo) clearReplyTo();
  });
  el.thread.addEventListener("scroll", hideMsgMenu, { passive: true });

  el.draft.addEventListener("keydown", onDraftKeydown);
  el.draft.addEventListener("input", () => {
    el.draft.style.height = "auto";
    el.draft.style.height = `${Math.min(el.draft.scrollHeight, 120)}px`;
    refreshSlashMenu();
  });
  el.draft.addEventListener("click", refreshSlashMenu);
  el.draft.addEventListener("keyup", (e) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) refreshSlashMenu();
  });

  // Pegar (Ctrl/⌘+V) una imagen copiada de fuera —una captura o una imagen de una
  // web— la detecta y la deja lista para enviar, sin pasar por el botón de
  // adjuntar. Solo intercepta imágenes: pegar texto normal sigue igual.
  el.draft.addEventListener("paste", (e) => {
    const dt = e.clipboardData;
    if (!dt) return;
    const blobs = [];
    for (const item of dt.items || []) {
      if (item.kind === "file" && (item.type || "").startsWith("image/")) {
        const blob = item.getAsFile();
        if (blob) blobs.push(blob);
      }
    }
    if (!blobs.length) return; // no hay imagen en el portapapeles: pegado normal
    // Evita que el navegador pegue además la ruta/alt de la imagen como texto.
    e.preventDefault();
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    blobs.forEach((blob, i) => {
      const ext = ((blob.type.split("/")[1] || "png").split("+")[0]) || "png";
      const name =
        blob.name && blob.name.toLowerCase() !== "image.png"
          ? blob.name
          : `pegada-${stamp}-${i + 1}.${ext}`;
      addPendingFile(blob, name);
    });
  });
  if (el.slashMenu) {
    el.slashMenu.addEventListener("mousedown", (e) => {
      // Evita que el textarea pierda el caret antes del click.
      e.preventDefault();
      const btn = e.target.closest(".slash-item");
      if (!btn) return;
      const idx = Number(btn.dataset.idx);
      applyQuickReply(slashMatches[idx]);
    });
  }

  el.search.addEventListener("input", () => {
    query = el.search.value.trim();
    renderList();
  });

  // ── enlaces de los módulos del asesor ───────────────────────

  el.scopeTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      scope = tab.dataset.scope || "all";
      el.scopeTabs.forEach((t) => {
        const activa = t === tab;
        t.classList.toggle("is-active", activa);
        t.setAttribute("aria-selected", activa ? "true" : "false");
      });
      renderList();
    });
  });

  if (el.btnSale) el.btnSale.addEventListener("click", openSaleDialog);
  if (el.saleForm) el.saleForm.addEventListener("submit", submitSale);
  if (el.saleCancel) el.saleCancel.addEventListener("click", () => el.saleDialog.close());
  if (el.saleDialogClose) {
    el.saleDialogClose.addEventListener("click", () => el.saleDialog.close());
  }

  if (el.noteForm) el.noteForm.addEventListener("submit", submitNote);
  if (el.followupForm) el.followupForm.addEventListener("submit", submitFollowup);

  // Presets del seguimiento: rellenan el datetime, no lo envían. Programar a
  // ciegas un recordatorio a una hora que el asesor no ha visto es la manera de
  // que le salte a las 3 de la mañana.
  if (el.followupForm) {
    el.followupForm.addEventListener("click", (e) => {
      const chip = e.target.closest(".preset-chip");
      if (!chip) return;
      const cuando = new Date();
      if (chip.dataset.preset === "tomorrow") {
        cuando.setDate(cuando.getDate() + 1);
        cuando.setHours(9, 0, 0, 0);
      } else if (chip.dataset.days) {
        cuando.setDate(cuando.getDate() + Number(chip.dataset.days));
        cuando.setHours(9, 0, 0, 0);
      } else {
        cuando.setHours(cuando.getHours() + Number(chip.dataset.hours || 3));
      }
      el.followupWhen.value = localInputValue(cuando);
      el.followupReason.focus();
    });
  }

  // La lista se repinta entera al cambiar de chat: listener delegado.
  if (el.followupList) {
    el.followupList.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-followup]");
      if (!btn) return;
      closeFollowup(Number(btn.dataset.followup), btn.dataset.status);
    });
  }

  try {
    if (localStorage.getItem("dr.leadPanelCollapsed") === "1") toggleLeadPanel(true);
    saleCollapsed = localStorage.getItem("dr.saleCollapsed") === "1";
  } catch {
    /* sin localStorage: panel abierto por defecto */
  }

  loadList();
  loadDueFollowups();
  setInterval(loadList, pollList);
  setInterval(loadThread, pollThread);
  // Los seguimientos vencen por el paso del tiempo, no por un mensaje nuevo, así
  // que el rail necesita su propio reloj. Un minuto basta: nadie programa un
  // recordatorio y espera que salte al segundo.
  setInterval(loadDueFollowups, 60000);
})();
