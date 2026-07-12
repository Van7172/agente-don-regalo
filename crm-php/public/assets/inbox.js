(() => {
  const root = document.getElementById("inbox-app");
  if (!root) return;

  const base = (root.dataset.base || "").replace(/\/$/, "");
  const apiBase = `${base}/api`;
  const listEl = document.getElementById("conv-list");
  const emptyEl = document.getElementById("empty-state");
  const wrapEl = document.getElementById("thread-wrap");
  const threadEl = document.getElementById("thread");
  const titleEl = document.getElementById("thread-title");
  const metaEl = document.getElementById("thread-meta");
  const errorEl = document.getElementById("error-box");
  const helpCountEl = document.getElementById("needs-help-count");
  const draftEl = document.getElementById("draft");
  const pollList = Number(root.dataset.pollList || 4000);
  const pollThread = Number(root.dataset.pollThread || 4000);

  let selectedId = null;
  let conversations = [];

  function showError(msg) {
    if (!msg) {
      errorEl.hidden = true;
      errorEl.textContent = "";
      return;
    }
    errorEl.hidden = false;
    errorEl.textContent = msg;
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

  function renderList() {
    const helpN = conversations.filter((c) => c.human_support).length;
    if (helpN > 0) {
      helpCountEl.hidden = false;
      helpCountEl.textContent = `${helpN} ayuda`;
    } else {
      helpCountEl.hidden = true;
    }

    listEl.innerHTML = "";
    if (!conversations.length) {
      listEl.innerHTML = `<div class="empty">Aún no hay conversaciones.</div>`;
      return;
    }

    for (const c of conversations) {
      const btn = document.createElement("button");
      btn.type = "button";
      const classes = ["item"];
      if (selectedId === c.id) classes.push("active");
      if (c.human_support) classes.push("needs-help");
      btn.className = classes.join(" ");
      const name = c.contact?.name || c.contact?.wa_id || "Sin nombre";
      const badgeClass = c.mode === "HUMAN" ? "human" : "ai";
      const helpBadge = c.human_support
        ? `<span class="badge" style="background:#ffe4e6;color:#9f1239">AYUDA</span>`
        : "";
      btn.innerHTML = `
        <div class="name">
          <span>${escapeHtml(name)}</span>
          <span style="display:flex;gap:0.25rem;align-items:center">
            ${helpBadge}
            <span class="badge ${badgeClass}">${escapeHtml(c.mode || "AI")}</span>
          </span>
        </div>
        <div class="preview">${escapeHtml(c.last_message || "Sin mensajes")}</div>`;
      btn.addEventListener("click", () => {
        selectedId = c.id;
        renderList();
        loadThread();
      });
      listEl.appendChild(btn);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function loadList() {
    try {
      const json = await api(`/conversations`);
      conversations = json.data || [];
      renderList();
      showError("");
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function loadThread() {
    if (selectedId == null) return;
    try {
      const json = await api(`/conversations/${selectedId}`);
      const conv = json.conversation;
      const messages = json.messages || [];
      emptyEl.hidden = true;
      wrapEl.hidden = false;
      titleEl.textContent = conv.contact?.name || conv.contact?.wa_id || "Chat";
      const help = conv.human_support ? " · necesita ayuda" : "";
      metaEl.textContent = `${conv.contact?.wa_id || ""} · modo ${conv.mode}${help}`;
      threadEl.innerHTML = messages
        .map(
          (m) => `<div class="bubble ${m.direction === "inbound" ? "inbound" : "outbound"}">
            <div class="who">${escapeHtml(m.sender_type || m.role || "")}</div>
            ${escapeHtml(m.content || "")}
          </div>`
        )
        .join("");
      threadEl.scrollTop = threadEl.scrollHeight;
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
      await loadList();
      await loadThread();
    } catch (err) {
      showError(err.message || String(err));
    }
  }

  async function send() {
    if (selectedId == null) return;
    const content = (draftEl.value || "").trim();
    if (!content) return;
    draftEl.value = "";
    try {
      await api(`/outbox`, {
        method: "POST",
        body: JSON.stringify({ conversation_id: selectedId, content }),
      });
      await loadThread();
      await loadList();
    } catch (err) {
      draftEl.value = content;
      showError(err.message || String(err));
    }
  }

  document.getElementById("btn-ai")?.addEventListener("click", () => setMode("AI"));
  document.getElementById("btn-human")?.addEventListener("click", () => setMode("HUMAN"));
  document.getElementById("btn-send")?.addEventListener("click", () => send());
  draftEl?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  loadList();
  setInterval(loadList, pollList);
  setInterval(() => {
    if (selectedId != null) loadThread();
  }, pollThread);
})();
