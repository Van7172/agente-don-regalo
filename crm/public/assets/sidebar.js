/**
 * Menú lateral.
 *  - Pantalla ancha: simple (solo iconos) por defecto, o ampliado si el asesor lo
 *    elige; la elección se recuerda.
 *  - Pantalla mediana: siempre simple al cargar. Ampliarlo lo pone encima del
 *    contenido, sin empujarlo, porque el inbox no tiene ancho que ceder.
 *  - Móvil: panel deslizable.
 * El modo inicial lo pone un script en <head>, antes de pintar (views/layout.php).
 */
(function () {
  const root = document.documentElement;
  const sidebar = document.getElementById("app-sidebar");
  const toggle = document.getElementById("sidebar-toggle");
  const openButton = document.getElementById("sidebar-open");
  const backdrop = document.getElementById("sidebar-backdrop");
  const main = document.querySelector(".app-main");
  const firstLink = document.querySelector(".sidebar-link");
  if (!sidebar || !toggle || !openButton || !backdrop || !main) return;

  // Los mismos cortes que usan app.css y el script de <head>.
  const mobile = window.matchMedia("(max-width: 900px)");
  const compact = window.matchMedia("(max-width: 1199px)");

  function savedMode() {
    try {
      return localStorage.getItem("dr.sidebarMode") === "full" ? "full" : "simple";
    } catch {
      return "simple";
    }
  }

  function syncToggle() {
    const simple = root.dataset.sidebar === "simple";
    const label = mobile.matches ? "Cerrar menú" : simple ? "Ampliar menú" : "Simplificar menú";
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
    toggle.setAttribute(
      "aria-expanded",
      String(mobile.matches ? document.body.classList.contains("sidebar-open") : !simple)
    );
  }

  function setMode(mode) {
    root.dataset.sidebar = mode;
    syncToggle();
  }

  function setDrawer(open) {
    document.body.classList.toggle("sidebar-open", open);
    openButton.setAttribute("aria-expanded", String(open));
    syncToggle();
    if (open) {
      // Fuerza el estilo ya visible: un elemento oculto no puede recibir el foco.
      void sidebar.offsetWidth;
      firstLink?.focus();
    }
  }

  const overlayOpen = () =>
    compact.matches && !mobile.matches && root.dataset.sidebar === "full";

  toggle.addEventListener("click", () => {
    if (mobile.matches) {
      setDrawer(false);
      openButton.focus();
      return;
    }
    const next = root.dataset.sidebar === "simple" ? "full" : "simple";
    setMode(next);
    // En pantalla mediana es temporal: se abre para ir a otra sección, no es una preferencia.
    if (!compact.matches) {
      try {
        localStorage.setItem("dr.sidebarMode", next);
      } catch {
        /* sin localStorage: el modo dura hasta cambiar de página */
      }
    }
  });

  openButton.addEventListener("click", () => setDrawer(true));
  backdrop.addEventListener("click", () => setDrawer(false));
  main.addEventListener("click", () => {
    if (overlayOpen()) setMode("simple");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (document.body.classList.contains("sidebar-open")) {
      setDrawer(false);
      openButton.focus();
    } else if (overlayOpen()) {
      setMode("simple");
      toggle.focus();
    }
  });
  // Al girar el teléfono o cambiar el ancho de la ventana, nada debe quedar colgado.
  mobile.addEventListener("change", () => setDrawer(false));
  compact.addEventListener("change", () => setMode(compact.matches ? "simple" : savedMode()));

  syncToggle();
  // La animación se activa aquí para que no corra al cargar cada página.
  requestAnimationFrame(() => root.classList.add("sidebar-ready"));
})();
