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

  const el = {
    rail: document.getElementById("help-rail"),
    railChips: document.getElementById("help-rail-chips"),
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
    btnRecord: document.getElementById("btn-record"),
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
  };

  const MAX_BYTES = 16 * 1024 * 1024;
  const MAX_ATTACH = 10;

  let conversations = [];
  let selectedId = null;
  let query = "";
  let listSig = "";
  let threadSig = "";
  let pendingFiles = []; // [{ blob, name, kind }]
  let sending = false;

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

  function parseTs(value) {
    if (!value) return null;
    const iso = String(value).includes("T") ? String(value) : String(value).replace(" ", "T");
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function timeLabel(value) {
    const d = parseTs(value);
    if (!d) return "";
    return d.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit", hour12: false });
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
    ai: { badge: "AI", tag: "tag-accent-2", label: "Regalito escuchando" },
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

  function visibleConversations() {
    if (!query) return conversations;
    const q = query.toLowerCase();
    return conversations.filter((c) => {
      const hay = [displayName(c), c.contact?.wa_id, c.last_message].join(" ").toLowerCase();
      return hay.includes(q);
    });
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
    btn.innerHTML = `
      <div class="avatar-wrap">
        <div class="avatar ${avatarClass(c.contact?.wa_id || c.id)}">${esc(initials(displayName(c)))}</div>
        <span class="status-dot is-${status}"></span>
      </div>
      <div class="item-body">
        <div class="item-top">
          <span class="item-name">${esc(displayName(c))}</span>
          <span class="item-time">${esc(timeLabel(c.last_message_at))}</span>
        </div>
        <div class="item-phone">${esc(c.contact?.wa_id || "")}</div>
        <div class="item-preview">${esc(c.last_message || "Sin mensajes")}</div>
        <div class="item-foot">
          ${sold ? `<span class="tag tag-sold">💚 VENTA CERRADA</span>` : ""}
          <span class="tag ${meta.tag}">${esc(meta.badge)}</span>
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
      <span class="chip-meta">${esc(waitLabel(c.last_message_at))}</span>`;
    btn.addEventListener("click", () => select(c.id));
    return btn;
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
      el.list.innerHTML = `<div class="list-note">Ningún chat coincide con la búsqueda.</div>`;
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

  function bubble(m) {
    const inbound = m.direction === "inbound";
    const sender = inbound ? "contact" : m.sender_type === "agent" ? "agent" : "bot";
    const label =
      sender === "contact" ? "CONTACTO" : sender === "agent" ? "ASESOR" : "REGALITO · BOT";

    const row = document.createElement("div");
    row.className = `msg-row ${inbound ? "is-in" : "is-out"}`;

    const media = mediaMarkup(m);
    // En un documento el texto ES el nombre del archivo: ya va dentro del propio enlace.
    const showText =
      m.content && !isPlaceholder(m.content) && !(media && m.media_kind === "document");

    row.innerHTML = `
      <div class="bubble from-${sender}${media ? " has-media" : ""}">
        <div class="who">${esc(label)}</div>
        ${media}
        ${showText ? `<div class="txt">${esc(m.content)}</div>` : ""}
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
  function saleCard(sale) {
    if (!sale) return "";
    const fila = (etiqueta, valor) =>
      valor ? `<div class="sale-row"><span>${etiqueta}</span><b>${esc(String(valor))}</b></div>` : "";
    const envio =
      sale.envio_sol != null ? `S/${Number(sale.envio_sol).toFixed(2)}` : "";

    return `
      <div class="sale-card">
        <div class="sale-head">💚 Venta cerrada por Regalito — solo falta cobrar</div>
        ${fila("Producto", sale.producto)}
        ${fila("Distrito", sale.distrito)}
        ${fila("Envío", envio)}
        ${fila("Fecha", sale.fecha)}
        ${fila("Horario", sale.horario)}
      </div>`;
  }

  function renderThread(conv, messages, lead) {
    el.chatPlaceholder.hidden = true;
    el.chatBody.hidden = false;

    const status = statusOf(conv);
    const name = displayName(conv);

    if (el.saleCard) {
      el.saleCard.innerHTML = saleCard(conv.sale);
      el.saleCard.hidden = !conv.sale;
    }
    el.chatBody.classList.toggle("is-sold", !!conv.sale);

    el.chatAvatar.className = `avatar ${avatarClass(conv.contact?.wa_id || conv.id)}`;
    el.chatAvatar.textContent = initials(name);
    el.chatName.textContent = name;
    el.chatDot.className = `dot-sm is-${status}`;
    el.chatState.textContent = `${STATUS_META[status].label} · ${conv.contact?.wa_id || ""}`;

    // El bot solo cede el turno en modo HUMAN; 'help' sigue siendo AI.
    const isHuman = conv.mode === "HUMAN";
    el.btnHuman.hidden = isHuman;
    el.btnAi.hidden = !isHuman;
    el.composerWrap.hidden = !isHuman;
    el.aiBanner.hidden = isHuman;

    const sig = JSON.stringify(messages.map((m) => m.id));
    if (sig !== threadSig) {
      threadSig = sig;
      el.thread.replaceChildren(...threadNodes(messages));
      el.thread.classList.remove("thread-anim");
      void el.thread.offsetWidth;
      el.thread.classList.add("thread-anim");
      el.thread.scrollTop = el.thread.scrollHeight;
    }

    renderLead(conv, lead);
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
    hideSlashMenu();
    renderList();
    loadThread();
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
      new Notification("Regalito pidió ayuda", {
        body: `${displayName(conv)} necesita un asesor ahora.`,
        tag: `handoff-${conv.id}`,
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

  async function loadList() {
    try {
      const json = await api("/conversations");
      if (!json || !Array.isArray(json.data)) {
        throw new Error(
          `Respuesta inválida del API (${apiBase}/conversations). Revisa base_path en config.php.`
        );
      }
      const next = json.data;
      const sig = JSON.stringify(next);
      if (sig !== listSig) {
        alertOnHandoff(conversations, next);
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

  function setSending(on) {
    sending = on;
    el.btnSend.disabled = on;
    el.btnAttach.disabled = on;
    el.btnRecord.disabled = on;
  }

  async function send(event) {
    event.preventDefault();
    if (selectedId == null || sending) return;

    const content = (el.draft.value || "").trim();
    const attachments = pendingFiles.slice();
    if (!content && !attachments.length) return;

    setSending(true);
    try {
      if (!attachments.length) {
        const json = await api("/outbox", {
          method: "POST",
          body: JSON.stringify({ conversation_id: selectedId, content }),
        });
        if (json.queued && json.pushed === false) {
          showError(json.warning || "Mensaje en cola hacia WhatsApp…");
        }
      } else {
        // Un outbox por archivo (WA no agrupa álbumes desde Cloud API).
        // El texto del borrador va de pie en la primera imagen/doc.
        for (let i = 0; i < attachments.length; i++) {
          const file = attachments[i];
          const up = await uploadMedia(file.blob, file.name);
          const json = await api("/outbox", {
            method: "POST",
            body: JSON.stringify({
              conversation_id: selectedId,
              content: i === 0 ? content : "",
              media_path: up.key,
              filename: file.name,
            }),
          });
          if (json.queued && json.pushed === false) {
            showError(json.warning || "Mensaje en cola hacia WhatsApp…");
          }
        }
      }

      el.draft.value = "";
      el.draft.style.height = "auto";
      clearPendingFiles();
      listSig = "";
      await Promise.all([loadThread(), loadList()]);
      showError("");
    } catch (err) {
      // No se limpia el borrador: el asesor no debe perder lo que escribió.
      showError(err.message || String(err));
    } finally {
      setSending(false);
    }
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

  el.btnHuman.addEventListener("click", () => setMode("HUMAN"));
  el.btnTake.addEventListener("click", () => setMode("HUMAN"));
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

  try {
    if (localStorage.getItem("dr.leadPanelCollapsed") === "1") toggleLeadPanel(true);
  } catch {
    /* sin localStorage: panel abierto por defecto */
  }

  loadList();
  setInterval(loadList, pollList);
  setInterval(loadThread, pollThread);
})();
