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
    btnSend: document.getElementById("btn-send"),
    fileInput: document.getElementById("file-input"),
    btnAttach: document.getElementById("btn-attach"),
    btnRecord: document.getElementById("btn-record"),
    attachPreview: document.getElementById("attach-preview"),
    attachIcon: document.getElementById("attach-icon"),
    attachName: document.getElementById("attach-name"),
    attachSize: document.getElementById("attach-size"),
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

  let conversations = [];
  let selectedId = null;
  let query = "";
  let listSig = "";
  let threadSig = "";
  let pendingFile = null; // File o Blob elegido/grabado, aún sin enviar
  let sending = false;

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
    btn.className = `list-item${c.id === selectedId ? " active" : ""}`;

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

  /** Burbujas + un separador cada vez que cambia el día. */
  function threadNodes(messages) {
    const nodes = [];
    let lastDay = null;

    for (const m of messages) {
      const label = dayLabel(m.created_at);
      if (label && label !== lastDay) {
        lastDay = label;
        const sep = document.createElement("div");
        sep.className = "day-sep";
        sep.innerHTML = `<span>${esc(label)}</span>`;
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

  function renderThread(conv, messages, lead) {
    el.chatPlaceholder.hidden = true;
    el.chatBody.hidden = false;

    const status = statusOf(conv);
    const name = displayName(conv);

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

  function setPendingFile(file, name) {
    if (file.size > MAX_BYTES) {
      showError(`El archivo pesa ${humanSize(file.size)}; el máximo es 16 MB.`);
      return;
    }
    pendingFile = { blob: file, name: name || file.name || "archivo", kind: kindOfFile(file) };
    el.attachIcon.innerHTML = ATTACH_ICONS[pendingFile.kind];
    el.attachName.textContent = pendingFile.name;
    el.attachSize.textContent = humanSize(file.size);
    el.attachPreview.hidden = false;
    showError("");
    el.draft.focus();
  }

  function clearPendingFile() {
    pendingFile = null;
    el.attachPreview.hidden = true;
    el.fileInput.value = "";
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
      setPendingFile(blob, `nota-de-voz.${ext}`);
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
    clearPendingFile();
    renderList();
    loadThread();
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
    const attachment = pendingFile;
    if (!content && !attachment) return;

    setSending(true);
    try {
      const payload = { conversation_id: selectedId, content };

      if (attachment) {
        const up = await uploadMedia(attachment.blob, attachment.name);
        payload.media_path = up.key;
        payload.filename = attachment.name;
      }

      await api("/outbox", { method: "POST", body: JSON.stringify(payload) });

      el.draft.value = "";
      el.draft.style.height = "auto";
      clearPendingFile();
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

  el.composer.addEventListener("submit", send);

  el.btnAttach.addEventListener("click", () => el.fileInput.click());
  el.fileInput.addEventListener("change", () => {
    const file = el.fileInput.files?.[0];
    if (file) setPendingFile(file);
  });
  el.attachClear.addEventListener("click", clearPendingFile);

  el.btnRecord.addEventListener("click", startRecording);
  el.recStop.addEventListener("click", () => stopRecording(false));
  el.recCancel.addEventListener("click", () => stopRecording(true));

  el.draft.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      el.composer.requestSubmit();
    }
  });
  el.draft.addEventListener("input", () => {
    el.draft.style.height = "auto";
    el.draft.style.height = `${Math.min(el.draft.scrollHeight, 120)}px`;
  });

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
