/**
 * Cambiar el estado de una venta desde el Historial.
 *
 * Antes solo se podía marcar "entregado" desde la ficha del chat, y era de ida:
 * un clic por error se quedaba así para siempre. Aquí el vendedor corrige la
 * fila que tiene delante, en los dos sentidos.
 */
(function () {
  "use strict";

  const table = document.querySelector("[data-sales-history]");
  if (!table) return;

  const base = (table.dataset.base || "").replace(/\/$/, "");
  const apiBase = `${base}/api`;

  /** dd/mm/aaaa hh:mm, igual que lo formatea el controlador en PHP. */
  function formatNow() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return (
      `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}`
    );
  }

  async function setStatus(select) {
    const row = select.closest("tr");
    const saleId = row && row.dataset.saleId;
    const status = select.value;
    // El valor anterior se guarda ANTES de la llamada: si el API falla hay que
    // devolver el select a donde estaba, o la pantalla miente sobre la venta.
    const previous = select.dataset.current || "";
    if (!saleId) return;

    select.disabled = true;
    try {
      const res = await fetch(`${apiBase}/sales/${saleId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ status }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || !json.ok) {
        throw new Error(json.error || `Error ${res.status}`);
      }

      select.dataset.current = status;
      select.classList.remove("is-pendiente", "is-entregado");
      select.classList.add(`is-${status}`);

      // La columna de confirmación se repinta aquí mismo: obligar a recargar
      // para ver el resultado de tu propio clic es lo que hace que la gente
      // dude de si el cambio se guardó y lo vuelva a pulsar.
      const cell = row.querySelector("[data-confirmation]");
      if (cell) {
        const when = cell.querySelector("strong");
        const who = cell.querySelector("small");
        if (when) when.textContent = status === "entregado" ? formatNow() : "—";
        if (who) who.textContent = table.dataset.userName || "—";
      }
    } catch (err) {
      select.value = previous;
      window.alert(`No se pudo cambiar el estado: ${err.message}`);
    } finally {
      select.disabled = false;
    }
  }

  table.addEventListener("change", function (event) {
    const select = event.target.closest("[data-sale-status]");
    if (select) setStatus(select);
  });
})();
