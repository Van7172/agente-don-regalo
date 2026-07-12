(() => {
  const root = document.getElementById("inbox-app");
  if (!root) return;

  const base = (root.dataset.base || "").replace(/\/$/, "");
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
    btnTake: document.getElementById("btn-take"),
    btnLead: document.getElementById("btn-lead"),
    btnLeadClose: document.getElementById("btn-lead-close"),
    thread: document.getElementById("thread"),
    aiBanner: document.getElementById("ai-banner"),
    composer: document.getElementById("composer"),
    draft: document.getElementById("draft"),
    leadPanel: document.getElementById("lead-panel"),
    leadAvatar: document.getElementById("lead-avatar"),
    leadName: document.getElementById("lead-name"),
    leadSub: document.getElementById("lead-sub"),
    leadFields: document.getElementById("lead-fields"),
    error: document.getElementById("error-box"),
  };

  let conversations = [];
  let selectedId = null;
  let query = "";
  let listSig = "";
  let threadSig = "";

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

  /** Índice de paleta estable por contacto. */
  function avatarClass(seed) {
    const key = String(seed ?? "");
    let sum = 0;
    for (let i = 0; i < key.length; i++) sum += key.charCodeAt(i);
    return `avatar-p${Math.abs(sum) % 3}`;
  }

  /** MySQL DATETIME ("2026-07-12 10:15:00") o ISO. Devuelve Date o null. */
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
    const hours = Math.floor(mins / 60);
    return `Esperando ${hours} h`;
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

  function bubble(m) {
    const inbound = m.direction === "inbound";
    const sender = inbound ? "contact" : m.sender_type === "agent" ? "agent" : "bot";
    const label =
      sender === "contact" ? "CONTACTO" : sender === "agent" ? "ASESOR" : "REGALITO · BOT";

    const row = document.createElement("div");
    row.className = `msg-row ${inbound ? "is-in" : "is-out"}`;

    const media =
      typeof m.media_url === "string" && /^https?:\/\//i.test(m.media_url)
        ? `<img class="media" src="${esc(m.media_url)}" alt="" loading="lazy" />`
        : "";

    row.innerHTML = `
      <div class="bubble from-${sender}">
        <div class="who">${esc(label)}</div>
        <div class="txt">${esc(m.content || "")}</div>
        ${media}
        <div class="at">${esc(timeLabel(m.created_at))}</div>
      </div>`;
    return row;
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
    el.composer.hidden = !isHuman;
    el.aiBanner.hidden = isHuman;

    const sig = JSON.stringify(messages.map((m) => m.id));
    if (sig !== threadSig) {
      threadSig = sig;
      el.thread.replaceChildren(...messages.map(bubble));
      el.thread.classList.remove("thread-anim");
      void el.thread.offsetWidth;
      el.thread.classList.add("thread-anim");
      el.thread.scrollTop = el.thread.scrollHeight;
    }

    renderLead(conv, lead);
  }

  // ── acciones ────────────────────────────────────────────────

  function select(id) {
    selectedId = id;
    threadSig = "";
    root.dataset.mobileChat = "true";
    renderList();
    loadThread();
  }

  async function loadList() {
    try {
      const json = await api("/conversations");
      const next = json.data || [];
      const sig = JSON.stringify(next);
      if (sig !== listSig) {
        listSig = sig;
        conversations = next;
        renderList();
      }
      showError("");
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

  async function setMode(mode) {
    if (selectedId == null) return;
    try {
      await api(`/conversations/${selectedId}/mode`, {
        method: "PATCH",
        body: JSON.stringify({ mode }),
      });
      listSig = "";
      await Promise.all([loadList(), loadThread()]);
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function send(event) {
    event.preventDefault();
    if (selectedId == null) return;
    const content = (el.draft.value || "").trim();
    if (!content) return;
    el.draft.value = "";
    try {
      await api("/outbox", {
        method: "POST",
        body: JSON.stringify({ conversation_id: selectedId, content }),
      });
      listSig = "";
      await Promise.all([loadThread(), loadList()]);
    } catch (err) {
      el.draft.value = content;
      showError(err.message || String(err));
    }
  }

  function toggleLeadPanel(force) {
    const collapsed = force ?? !el.leadPanel.classList.contains("collapsed");
    el.leadPanel.classList.toggle("collapsed", collapsed);
    try {
      localStorage.setItem("dr.leadPanelCollapsed", collapsed ? "1" : "0");
    } catch {
      /* almacenamiento no disponible: el panel simplemente no recuerda su estado */
    }
  }

  // ── enlaces ─────────────────────────────────────────────────

  el.btnHuman.addEventListener("click", () => setMode("HUMAN"));
  el.btnTake.addEventListener("click", () => setMode("HUMAN"));
  el.btnAi.addEventListener("click", () => setMode("AI"));
  el.btnLead.addEventListener("click", () => toggleLeadPanel());
  el.btnLeadClose.addEventListener("click", () => toggleLeadPanel(true));
  el.btnBack.addEventListener("click", () => {
    root.dataset.mobileChat = "false";
  });
  el.composer.addEventListener("submit", send);

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
