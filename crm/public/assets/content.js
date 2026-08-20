(() => {
  const root = document.getElementById("studio-app");
  if (!root) return;

  // Misma detección que el inbox: si `base_path` está vacío pero la app vive en
  // /crm/public/, el fetch iría a /api/... y devolvería el 404 en HTML.
  function detectBase() {
    const path = window.location.pathname || "";
    const folder = path.match(/^(.*\/crm\/public)(?:\/|$)/i);
    if (folder) return folder[1].replace(/\/$/, "");
    const configured = (root.dataset.base || "").replace(/\/$/, "");
    if (configured) return configured;
    if (/\/[^/]+\.php$/i.test(path)) return path.replace(/\/[^/]+\.php$/i, "") || "";
    return path.replace(/\/$/, "") || "";
  }

  const base = detectBase();
  const apiBase = `${base}/api`;

  // El selector se habilita a los 3 caracteres. Con menos, la búsqueda devuelve
  // medio catálogo: el asesor elige a ciegas y encima se paga una llamada por
  // cada tecla. El agente aplica el mismo corte — el navegador no decide solo.
  const MIN_CHARS = 3;
  // Teclear "desayuno" son ocho pulsaciones; sin espera son ocho búsquedas.
  const DEBOUNCE_MS = 280;

  const el = {
    error: document.getElementById("studio-error"),
    search: document.getElementById("product-search"),
    searchHint: document.getElementById("search-hint"),
    results: document.getElementById("product-results"),
    chosen: document.getElementById("product-chosen"),
    tone: document.getElementById("tone"),
    variants: document.getElementById("variants"),
    optPrice: document.getElementById("opt-price"),
    optCta: document.getElementById("opt-cta"),
    brief: document.getElementById("brief"),
    generate: document.getElementById("btn-generate"),
    generateHint: document.getElementById("generate-hint"),
    stepFormat: document.getElementById("step-format"),
    stepBrief: document.getElementById("step-brief"),
    stepResult: document.getElementById("step-result"),
    resultEmpty: document.getElementById("result-empty"),
    resultGrid: document.getElementById("result-grid"),
    copies: document.getElementById("result-copies"),
    previewFrame: document.getElementById("preview-frame"),
    previewImg: document.getElementById("preview-img"),
    previewTitle: document.getElementById("preview-title"),
    previewBody: document.getElementById("preview-body"),
    previewPrice: document.getElementById("preview-price"),
    previewCta: document.getElementById("preview-cta"),
    photo: document.getElementById("btn-photo"),
  };

  let producto = null;
  let variantes = [];
  let elegida = 0;
  let buscando = 0; // id de la última búsqueda lanzada
  let debounce = null;

  const esc = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function showError(msg) {
    el.error.hidden = !msg;
    el.error.textContent = msg || "";
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

  const money = (p) => (p == null ? "" : `S/ ${Number(p).toFixed(2)}`);

  // ── paso 1: elegir producto ─────────────────────────────────

  function renderResults(items) {
    if (!items.length) {
      el.results.hidden = false;
      el.results.innerHTML = `<p class="list-note">Ningún producto coincide. Prueba con otra palabra.</p>`;
      return;
    }
    el.results.hidden = false;
    el.results.innerHTML = items
      .map(
        (p, i) => `
        <button type="button" class="product-hit" data-idx="${i}">
          ${
            p.imagen_url
              ? `<img src="${esc(p.imagen_url)}" alt="" loading="lazy" />`
              : `<span class="product-hit-noimg">sin foto</span>`
          }
          <span class="product-hit-body">
            <span class="product-hit-name">${esc(p.nombre)}</span>
            <span class="product-hit-meta">${esc(p.categoria || "")}</span>
          </span>
          <span class="product-hit-price">${esc(money(p.precio_sol))}</span>
        </button>`
      )
      .join("");
    el.results._items = items;
  }

  async function search(term) {
    const ticket = ++buscando;
    try {
      const json = await api(`/content/products?q=${encodeURIComponent(term)}`);
      // Una respuesta lenta de una búsqueda vieja no puede pisar a la nueva: el
      // asesor vería resultados de un término que ya borró.
      if (ticket !== buscando) return;
      renderResults(json.data || []);
      showError("");
    } catch (err) {
      if (ticket !== buscando) return;
      el.results.hidden = true;
      showError(err.message || String(err));
    }
  }

  function chooseProduct(p) {
    producto = p;
    el.results.hidden = true;
    el.chosen.hidden = false;
    el.chosen.innerHTML = `
      <div class="chosen-card">
        ${p.imagen_url ? `<img src="${esc(p.imagen_url)}" alt="" />` : ""}
        <div class="chosen-body">
          <div class="chosen-name">${esc(p.nombre)}</div>
          <div class="chosen-meta">${esc(p.categoria || "")}</div>
          <div class="chosen-price">${esc(money(p.precio_sol))}</div>
        </div>
        <button type="button" class="btn btn-secondary btn-sm" id="chosen-clear">Cambiar</button>
      </div>`;
    el.stepFormat.classList.add("is-open");
    el.stepBrief.classList.add("is-open");
    el.generate.disabled = false;
    el.generateHint.textContent = "Listo para generar.";
  }

  function clearProduct() {
    producto = null;
    el.chosen.hidden = true;
    el.chosen.innerHTML = "";
    el.generate.disabled = true;
    el.generateHint.textContent = "Primero elige un producto.";
    el.search.focus();
    el.search.select();
  }

  el.search.addEventListener("input", () => {
    const term = el.search.value.trim();
    clearTimeout(debounce);
    if (term.length < MIN_CHARS) {
      buscando++; // invalida cualquier búsqueda en vuelo
      el.results.hidden = true;
      el.searchHint.textContent = `Faltan ${MIN_CHARS - term.length} caracter(es) para buscar.`;
      return;
    }
    el.searchHint.textContent = "Buscando…";
    debounce = setTimeout(() => {
      el.searchHint.textContent = "Elige uno de la lista.";
      search(term);
    }, DEBOUNCE_MS);
  });

  el.results.addEventListener("click", (e) => {
    const hit = e.target.closest(".product-hit");
    if (!hit) return;
    const items = el.results._items || [];
    const item = items[Number(hit.dataset.idx)];
    if (item) chooseProduct(item);
  });

  el.chosen.addEventListener("click", (e) => {
    if (e.target.closest("#chosen-clear")) clearProduct();
  });

  // ── paso 2: formato ─────────────────────────────────────────

  function formatoElegido() {
    const marcado = document.querySelector('input[name="formato"]:checked');
    return marcado ? marcado.value : "9:16";
  }

  document.querySelectorAll('input[name="formato"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document
        .querySelectorAll(".format-option")
        .forEach((o) => o.classList.toggle("is-active", o.contains(radio) && radio.checked));
      el.previewFrame.className = `preview-frame is-${formatoElegido().replace(":", "-")}`;
    });
  });

  // ── paso 4: resultado ───────────────────────────────────────

  function renderPreview() {
    const v = variantes[elegida];
    if (!v || !producto) return;
    el.previewFrame.className = `preview-frame is-${formatoElegido().replace(":", "-")}`;
    el.previewImg.src = producto.imagen_url || "";
    el.previewImg.hidden = !producto.imagen_url;
    el.previewTitle.textContent = v.titular || "";
    el.previewBody.textContent = v.cuerpo || "";
    el.previewPrice.textContent = v.precio || "";
    el.previewPrice.hidden = !v.precio;
    el.previewCta.textContent = v.cta || "";
    el.previewCta.hidden = !v.cta;
    el.photo.href = producto.imagen_url || "#";
    el.photo.hidden = !producto.imagen_url;
  }

  function renderCopies() {
    el.copies.innerHTML = variantes
      .map(
        (v, i) => `
        <article class="copy-card${i === elegida ? " is-active" : ""}" data-idx="${i}">
          <header class="copy-head">
            <span class="tag tag-accent">Versión ${i + 1}</span>
            <button type="button" class="btn btn-secondary btn-sm copy-btn" data-copy="${i}">Copiar todo</button>
          </header>
          <div class="copy-title">${esc(v.titular)}</div>
          <p class="copy-body">${esc(v.cuerpo)}</p>
          ${v.precio ? `<div class="copy-price">${esc(v.precio)}</div>` : ""}
          ${v.cta ? `<div class="copy-cta">${esc(v.cta)}</div>` : ""}
          ${
            v.hashtags && v.hashtags.length
              ? `<div class="copy-tags">${esc(v.hashtags.join(" "))}</div>`
              : ""
          }
        </article>`
      )
      .join("");
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Sin permiso de portapapeles (o http sin TLS): textarea oculto.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
      ta.remove();
      return ok;
    }
  }

  el.copies.addEventListener("click", async (e) => {
    const boton = e.target.closest(".copy-btn");
    if (boton) {
      const v = variantes[Number(boton.dataset.copy)];
      if (!v) return;
      const ok = await copyText(v.texto);
      boton.textContent = ok ? "¡Copiado!" : "Copia a mano";
      setTimeout(() => {
        boton.textContent = "Copiar todo";
      }, 1800);
      return;
    }
    const card = e.target.closest(".copy-card");
    if (!card) return;
    elegida = Number(card.dataset.idx);
    renderCopies();
    renderPreview();
  });

  // ── generar ─────────────────────────────────────────────────

  async function generate() {
    if (!producto) return;
    el.generate.disabled = true;
    el.generate.textContent = "Generando…";
    el.generateHint.textContent = "La IA está escribiendo. Suele tardar unos segundos.";
    try {
      const json = await api("/content/draft", {
        method: "POST",
        body: JSON.stringify({
          producto,
          tono: el.tone.value,
          formato: formatoElegido(),
          instrucciones: el.brief.value || "",
          incluir_precio: el.optPrice.checked,
          incluir_cta: el.optCta.checked,
          variantes: Number(el.variants.value || 3),
        }),
      });
      variantes = json.variantes || [];
      elegida = 0;
      if (!variantes.length) throw new Error("La IA no devolvió ningún texto. Inténtalo otra vez.");
      el.resultEmpty.hidden = true;
      el.resultGrid.hidden = false;
      el.stepResult.classList.add("is-open");
      renderCopies();
      renderPreview();
      showError("");
      el.stepResult.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      el.generate.disabled = !producto;
      el.generate.textContent = "Generar contenido";
      el.generateHint.textContent = producto ? "Listo para generar." : "Primero elige un producto.";
    }
  }

  el.generate.addEventListener("click", generate);
})();
