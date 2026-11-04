/**
 * Ajustes: respuestas rápidas y catálogo de etiquetas.
 * Todo lo escrito por el equipo se pinta con textContent: nada se interpreta como HTML.
 */
(function () {
  const root = document.getElementById("settings-app");
  if (!root) return;

  const apiBase = `${root.dataset.base || ""}/api`.replace(/\/{2,}/g, "/");
  const maxBody = Number(root.dataset.maxBody || 4096);
  const colors = JSON.parse(root.dataset.colors || "{}");
  const $ = (id) => document.getElementById(id);
  const errorBox = $("settings-error");

  function showError(message) {
    errorBox.hidden = !message;
    errorBox.textContent = message || "";
    if (message) errorBox.scrollIntoView({ block: "nearest" });
  }

  async function api(path, options = {}) {
    const res = await fetch(`${apiBase}${path}`, {
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      ...options,
    });
    const json = await res.json().catch(() => ({}));
    if (res.status === 401) {
      window.location.href = `${root.dataset.base || ""}/login.php`;
      throw new Error("Sesión expirada");
    }
    if (!res.ok) throw new Error(json.error || "No se pudo guardar");
    return json;
  }

  function button(label, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-secondary btn-sm";
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  /** Corre una acción y repinta; si falla, deja el error a la vista. */
  async function run(action) {
    showError("");
    try {
      await action();
      return true;
    } catch (err) {
      showError(err.message || String(err));
      return false;
    }
  }

  // ── respuestas rápidas ──────────────────────────────────────

  const replyForm = $("reply-form");
  const replyId = $("reply-id");
  const replyCmd = $("reply-cmd");
  const replyLabel = $("reply-label");
  const replyBody = $("reply-body");
  const replyCount = $("reply-count");
  const replySubmit = $("reply-submit");
  const replyCancel = $("reply-cancel");
  const replyList = $("reply-list");

  function updateCount() {
    replyCount.textContent = `(${replyBody.value.length} de ${maxBody})`;
  }

  function resetReplyForm() {
    replyForm.reset();
    replyId.value = "";
    replySubmit.textContent = "Crear respuesta";
    replyCancel.hidden = true;
    updateCount();
  }

  function editReply(reply) {
    replyId.value = String(reply.id);
    replyCmd.value = reply.cmd;
    replyLabel.value = reply.label;
    replyBody.value = reply.body;
    replySubmit.textContent = "Guardar cambios";
    replyCancel.hidden = false;
    updateCount();
    replyCmd.focus();
    replyForm.scrollIntoView({ block: "nearest" });
  }

  function replyRow(reply) {
    const row = document.createElement("article");
    row.className = `settings-row${reply.active ? "" : " is-off"}`;

    const main = document.createElement("div");
    main.className = "settings-row-main";
    const title = document.createElement("strong");
    title.textContent = `/${reply.cmd} · ${reply.label}`;
    const body = document.createElement("p");
    body.className = "settings-row-body";
    body.textContent = reply.body;
    main.append(title, body);
    if (reply.updated_by) {
      const who = document.createElement("small");
      who.textContent = `Último cambio: ${reply.updated_by}`;
      main.append(who);
    }

    const actions = document.createElement("div");
    actions.className = "settings-row-actions";
    actions.append(
      button("Editar", () => editReply(reply)),
      button(reply.active ? "Desactivar" : "Activar", () =>
        run(async () => {
          await api(`/quick-replies/${reply.id}`, {
            method: "PATCH",
            body: JSON.stringify({ active: !reply.active }),
          });
          await loadReplies();
        })
      )
    );
    row.append(main, actions);
    return row;
  }

  async function loadReplies() {
    const { data } = await api("/quick-replies?all=1");
    if (!data.length) {
      replyList.textContent = "Todavía no hay respuestas rápidas.";
      return;
    }
    replyList.replaceChildren(...data.map(replyRow));
  }

  replyBody.addEventListener("input", updateCount);
  replyCancel.addEventListener("click", resetReplyForm);
  replyForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const id = replyId.value;
    const payload = {
      cmd: replyCmd.value.trim(),
      label: replyLabel.value.trim(),
      body: replyBody.value,
    };
    run(async () => {
      await api(id ? `/quick-replies/${id}` : "/quick-replies", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      resetReplyForm();
      await loadReplies();
    });
  });

  // ── etiquetas ───────────────────────────────────────────────

  const tagForm = $("tag-form");
  const tagId = $("tag-id");
  const tagName = $("tag-name");
  const tagColor = $("tag-color");
  const tagSubmit = $("tag-submit");
  const tagCancel = $("tag-cancel");
  const tagList = $("tag-list");

  function resetTagForm() {
    tagForm.reset();
    tagId.value = "";
    tagSubmit.textContent = "Crear etiqueta";
    tagCancel.hidden = true;
  }

  function tagRow(tag) {
    const row = document.createElement("article");
    row.className = `settings-row is-tag${tag.active ? "" : " is-off"}`;

    const main = document.createElement("div");
    main.className = "settings-row-main";
    const chip = document.createElement("span");
    chip.className = `label-chip label-${colors[tag.color] ? tag.color : "arena"}`;
    chip.textContent = tag.name;
    main.append(chip);
    if (!tag.active) {
      const off = document.createElement("small");
      off.textContent = "Archivada";
      main.append(off);
    }

    const actions = document.createElement("div");
    actions.className = "settings-row-actions";
    actions.append(
      button("Editar", () => {
        tagId.value = String(tag.id);
        tagName.value = tag.name;
        tagColor.value = tag.color;
        tagSubmit.textContent = "Guardar cambios";
        tagCancel.hidden = false;
        tagName.focus();
      }),
      button(tag.active ? "Archivar" : "Reactivar", () =>
        run(async () => {
          await api(`/tags/${tag.id}`, {
            method: "PATCH",
            body: JSON.stringify({ active: !tag.active }),
          });
          await loadTags();
        })
      )
    );
    row.append(main, actions);
    return row;
  }

  async function loadTags() {
    const { data } = await api("/tags?all=1");
    if (!data.length) {
      tagList.textContent = "Todavía no hay etiquetas.";
      return;
    }
    tagList.replaceChildren(...data.map(tagRow));
  }

  tagCancel.addEventListener("click", resetTagForm);
  tagForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const id = tagId.value;
    const payload = { name: tagName.value.trim(), color: tagColor.value };
    run(async () => {
      await api(id ? `/tags/${id}` : "/tags", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      resetTagForm();
      await loadTags();
    });
  });

  updateCount();
  run(() => Promise.all([loadReplies(), loadTags()]));
})();
