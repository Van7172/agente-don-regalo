(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const kpis = $("#ops-kpis");
  const alertBox = $("#ops-alert");
  const updated = $("#ops-updated");
  const refreshButton = $("#ops-refresh");
  let loading = false;

  const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const text = (value, fallback = "—") =>
    value === null || value === undefined || value === "" ? fallback : String(value);
  const milliseconds = (value) => `${number(value).toFixed(number(value) >= 10 ? 0 : 1)} ms`;
  const minutes = (value) => `${number(value)} min`;

  function node(tag, content, className) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined) element.textContent = text(content, "");
    return element;
  }

  function badge(label, state = "neutral") {
    return node("span", label, `ops-badge is-${state}`);
  }

  function emptyRow(tbody, message, columns) {
    const row = tbody.insertRow();
    const cell = row.insertCell();
    cell.colSpan = columns;
    cell.className = "ops-empty";
    cell.textContent = message;
  }

  function renderKpis(crm, agentEnvelope) {
    const agent = agentEnvelope?.data || {};
    const queue = agent.queue || {};
    const operations = agent.operations?.operation_series || {};
    const circuits = agent.circuits || {};
    const openCircuits = Object.values(circuits)
      .filter((item) => item?.state !== "closed").length;
    const retries = number(operations["inbound.worker:retry"]?.count);
    const cards = [
      {
        label: "Agente",
        value: agentEnvelope?.reachable ? "En línea" : "Sin conexión",
        meta: agentEnvelope?.reachable ? "telemetría disponible" : text(agentEnvelope?.status),
        state: agentEnvelope?.reachable ? "ok" : "danger",
      },
      {
        label: "Cola pendiente",
        value: number(queue.global_pending ?? queue.depth),
        meta: queue.durable ? "Redis Streams" : "memoria local",
        state: number(queue.global_pending ?? queue.depth) > 20 ? "warn" : "ok",
      },
      {
        label: "Dead letter",
        value: number(queue.dead_letter),
        meta: "requieren revisión",
        state: number(queue.dead_letter) > 0 ? "danger" : "ok",
      },
      {
        label: "Reintentos",
        value: retries,
        meta: "desde el último reinicio",
        state: retries > 0 ? "warn" : "ok",
      },
      {
        label: "Circuitos abiertos",
        value: openCircuits,
        meta: `${Object.keys(circuits).length} dependencias`,
        state: openCircuits > 0 ? "danger" : "ok",
      },
      {
        label: "Handoffs",
        value: number(crm?.handoffs?.pending),
        meta: `más antiguo: ${minutes(crm?.handoffs?.oldest_minutes)}`,
        state: number(crm?.handoffs?.pending) > 0 ? "warn" : "ok",
      },
      {
        label: "Outbox pendiente",
        value: number(crm?.outbox?.pending) + number(crm?.outbox?.sending),
        meta: `más antiguo: ${minutes(crm?.outbox?.oldest_pending_minutes)}`,
        state: number(crm?.outbox?.pending) > 0 ? "warn" : "ok",
      },
      {
        label: "Outbox fallido",
        value: number(crm?.outbox?.failed),
        meta: "entregas no completadas",
        state: number(crm?.outbox?.failed) > 0 ? "danger" : "ok",
      },
    ];

    kpis.replaceChildren();
    cards.forEach((item) => {
      const card = node("article", undefined, `card elev-sm ops-kpi is-${item.state}`);
      card.append(node("span", item.label, "card-kicker"));
      card.append(node("strong", item.value, "ops-kpi-value"));
      card.append(node("span", item.meta, "card-meta"));
      kpis.append(card);
    });
  }

  function renderCircuits(circuits) {
    const tbody = $("#ops-circuits");
    const status = $("#ops-circuit-status");
    tbody.replaceChildren();
    const entries = Object.entries(circuits || {});
    const unhealthy = entries.filter(([, item]) => item?.state !== "closed").length;
    status.className = `ops-status ${unhealthy ? "is-danger" : "is-ok"}`;
    status.textContent = unhealthy ? `${unhealthy} requieren atención` : "Todos cerrados";
    if (!entries.length) return emptyRow(tbody, "Sin datos del agente.", 4);

    entries.forEach(([name, item]) => {
      const row = tbody.insertRow();
      row.insertCell().textContent = name;
      const stateCell = row.insertCell();
      const state = item?.state || "unknown";
      stateCell.append(badge(
        state === "closed" ? "Cerrado" : state === "open" ? "Abierto" : "Semiabierto",
        state === "closed" ? "ok" : state === "open" ? "danger" : "warn"
      ));
      row.insertCell().textContent = `${number(item?.consecutive_failures)} / ${number(item?.failure_threshold)}`;
      row.insertCell().textContent = `${number(item?.retry_after_seconds).toFixed(1)} s`;
    });
  }

  function renderLatencies(series) {
    const tbody = $("#ops-latencies");
    tbody.replaceChildren();
    const rows = Object.entries(series || {})
      .filter(([, item]) => number(item?.duration_ms_sum) > 0)
      .map(([key, item]) => {
        // Se divide entre las que SE CRONOMETRARON, no entre el total: hay
        // operaciones que se registran sin medir tiempo, y repartir la suma
        // entre ellas daba una media más baja que la real. `count` sigue de
        // respaldo para snapshots de un agente anterior a este cambio.
        const timed = number(item.duration_count) || number(item.count);
        return {
          key,
          count: number(item.count),
          average: number(item.duration_ms_sum) / Math.max(1, timed),
          p95: number(item.duration_ms_p95),
          maximum: number(item.duration_ms_max),
        };
      })
      // Ordenado por p95 y no por la media: una operación que va bien de media
      // pero tiene cola es justo la que hay que mirar, y por media quedaba
      // enterrada.
      .sort((a, b) => (b.p95 || b.average) - (a.p95 || a.average))
      .slice(0, 12);
    if (!rows.length) return emptyRow(tbody, "Aún no hay muestras de latencia.", 5);
    rows.forEach((item) => {
      const row = tbody.insertRow();
      row.insertCell().textContent = item.key;
      row.insertCell().textContent = String(item.count);
      row.insertCell().textContent = milliseconds(item.average);
      // Un agente sin percentiles todavía desplegado: mejor un guion que un 0
      // que se lea como "tarda nada".
      row.insertCell().textContent = item.p95 ? milliseconds(item.p95) : "—";
      row.insertCell().textContent = milliseconds(item.maximum);
    });
  }

  function renderHandoffs(handoffs) {
    const tbody = $("#ops-handoffs");
    tbody.replaceChildren();
    const items = handoffs?.items || [];
    if (!items.length) return emptyRow(tbody, "No hay handoffs activos.", 4);
    items.forEach((item) => {
      const row = tbody.insertRow();
      row.insertCell().textContent = `#${number(item.id_conversation)}`;
      row.insertCell().textContent = text(item.nombre_contact, "Sin nombre");
      const stateCell = row.insertCell();
      stateCell.append(badge(
        number(item.human_support) ? "Solicita ayuda" : text(item.mode_conversation),
        number(item.human_support) ? "warn" : "neutral"
      ));
      row.insertCell().textContent = minutes(item.waiting_minutes);
    });
  }

  function renderErrors(crm, series, queue) {
    const tbody = $("#ops-errors");
    tbody.replaceChildren();
    const rows = Object.entries(series || {})
      .map(([key, item]) => {
        const split = key.lastIndexOf(":");
        return {
          origin: split >= 0 ? key.slice(0, split) : key,
          outcome: split >= 0 ? key.slice(split + 1) : "unknown",
          count: number(item?.count),
        };
      })
      .filter((item) =>
        item.count > 0 && ["error", "failed", "retry", "dead_letter", "rejected", "unavailable"]
          .includes(item.outcome)
      )
      .sort((a, b) => b.count - a.count);

    (crm?.outbox?.failed_items || []).forEach((item) => rows.push({
      origin: `outbox #${number(item.id_outbox)}`,
      outcome: "failed",
      count: 1,
      detail: text(item.error_outbox, text(item.type_outbox)),
    }));
    if (number(queue?.dead_letter) > 0) rows.unshift({
      origin: "redis.dlq",
      outcome: "dead_letter",
      count: number(queue.dead_letter),
      detail: "Revisar antes de reinyectar",
    });

    if (!rows.length) return emptyRow(tbody, "No hay errores ni reintentos registrados.", 4);
    rows.slice(0, 15).forEach((item) => {
      const row = tbody.insertRow();
      row.insertCell().textContent = item.origin;
      const outcomeCell = row.insertCell();
      outcomeCell.append(badge(item.outcome, item.outcome === "retry" ? "warn" : "danger"));
      row.insertCell().textContent = String(item.count);
      row.insertCell().textContent = text(item.detail);
    });
  }

  function render(payload) {
    const crm = payload?.crm || {};
    const agentEnvelope = payload?.agent || {};
    const agent = agentEnvelope.data || {};
    const series = agent.operations?.operation_series || {};
    const queue = agent.queue || {};

    renderKpis(crm, agentEnvelope);
    renderCircuits(agent.circuits);
    renderLatencies(series);
    renderHandoffs(crm.handoffs);
    renderErrors(crm, series, queue);

    if (!agentEnvelope.reachable) {
      alertBox.hidden = false;
      alertBox.textContent = `No se pudo consultar el agente: ${text(agentEnvelope.error, agentEnvelope.status)}`;
    } else if (queue.telemetry_status === "error") {
      alertBox.hidden = false;
      alertBox.textContent = `El agente está en línea, pero Redis no pudo entregar métricas: ${text(queue.telemetry_error)}`;
    } else {
      alertBox.hidden = true;
      alertBox.textContent = "";
    }
    const stamp = agentEnvelope.fetched_at || crm.generated_at;
    updated.textContent = stamp
      ? `Actualizado ${new Date(stamp).toLocaleTimeString("es-PE")}`
      : "Actualizado";
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    refreshButton.disabled = true;
    refreshButton.textContent = "Actualizando…";
    try {
      const response = await fetch(window.__OPS_API__, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      alertBox.hidden = false;
      alertBox.textContent = `No se pudo actualizar el panel: ${error.message}`;
    } finally {
      loading = false;
      refreshButton.disabled = false;
      refreshButton.textContent = "Actualizar";
    }
  }

  refreshButton.addEventListener("click", refresh);
  render(window.__OPS_INITIAL__ || {});
  window.setInterval(refresh, 15000);
})();
