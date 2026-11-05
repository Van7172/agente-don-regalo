(() => {
  const source = document.getElementById('campaign-chart-data');
  if (!source) return;
  let data;
  try { data = JSON.parse(source.textContent); } catch (_) { return; }
  const trendTarget = document.getElementById('campaign-trend-chart');
  const rankingTarget = document.getElementById('campaign-ranking-chart');
  const trendSelect = document.getElementById('campaign-trend-metric');
  const rankingSelect = document.getElementById('campaign-ranking-metric');
  const format = (value, metric) => metric === 'spend'
    ? `S/${Number(value).toLocaleString('es-PE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : Number(value).toLocaleString('es-PE');
  const empty = (target, message) => {
    target.replaceChildren();
    const p = document.createElement('p');
    p.className = 'chart-empty';
    p.textContent = message;
    target.append(p);
  };
  const weekOf = (date) => {
    const d = new Date(`${date}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7);
    return d.toISOString().slice(0, 10);
  };
  const dateLabel = (date) => new Intl.DateTimeFormat('es-PE', {
    day: 'numeric', month: 'short', timeZone: 'UTC',
  }).format(new Date(`${date}T12:00:00Z`));
  const shortValue = (value, metric) => {
    const number = Number(value);
    const prefix = metric === 'spend' ? 'S/' : '';
    if (number >= 1000000) return `${prefix}${(number / 1000000).toFixed(1)} M`;
    if (number >= 1000) return `${prefix}${(number / 1000).toFixed(1)} mil`;
    if (metric !== 'spend') return String(Math.round(number));
    return `${prefix}${Number.isInteger(number) ? number : number.toFixed(1)}`;
  };
  const bucketKey = (date, grouping) => {
    if (grouping === 'week') return weekOf(date);
    if (grouping === 'month') return `${date.slice(0, 7)}-01`;
    if (grouping === 'quarter') {
      const month = Math.floor((Number(date.slice(5, 7)) - 1) / 3) * 3 + 1;
      return `${date.slice(0, 4)}-${String(month).padStart(2, '0')}-01`;
    }
    if (grouping === 'year') return `${date.slice(0, 4)}-01-01`;
    return date;
  };
  const periodLabel = (date, grouping) => {
    if (grouping === 'week') return `Semana del ${dateLabel(date)}`;
    if (grouping === 'month') return new Intl.DateTimeFormat('es-PE', {
      month: 'long', year: 'numeric', timeZone: 'UTC',
    }).format(new Date(`${date}T12:00:00Z`));
    if (grouping === 'quarter') return `Trimestre ${Math.floor((Number(date.slice(5, 7)) - 1) / 3) + 1} de ${date.slice(0, 4)}`;
    if (grouping === 'year') return date.slice(0, 4);
    return dateLabel(date);
  };

  function renderTrend() {
    const metric = trendSelect.value;
    const days = Math.max(0, (Date.parse(data.to) - Date.parse(data.from)) / 86400000);
    const grouping = days > 2920 ? 'year' : days > 730 ? 'quarter'
      : days > 180 ? 'month' : days > 45 ? 'week' : 'day';
    const buckets = new Map();
    const first = bucketKey(data.from, grouping);
    const last = bucketKey(data.to, grouping);
    const cursor = new Date(`${first}T12:00:00Z`);
    const end = new Date(`${last}T12:00:00Z`);
    while (cursor <= end && buckets.size < 120) {
      const key = cursor.toISOString().slice(0, 10);
      buckets.set(key, 0);
      if (grouping === 'day') cursor.setUTCDate(cursor.getUTCDate() + 1);
      else if (grouping === 'week') cursor.setUTCDate(cursor.getUTCDate() + 7);
      else if (grouping === 'month') cursor.setUTCMonth(cursor.getUTCMonth() + 1);
      else if (grouping === 'quarter') cursor.setUTCMonth(cursor.getUTCMonth() + 3);
      else cursor.setUTCFullYear(cursor.getUTCFullYear() + 1);
    }
    data.trend.forEach((item) => {
      const key = bucketKey(item.date, grouping);
      if (buckets.has(key)) buckets.set(key, buckets.get(key) + Number(item[metric] || 0));
    });
    const values = [...buckets];
    const max = Math.max(0, ...values.map(([, value]) => value));
    if (!max) return empty(trendTarget, `Sin ${metric === 'spend' ? 'gasto' : 'actividad'} en estas fechas.`);
    trendTarget.replaceChildren();
    const measure = metric === 'spend' ? 'de gasto' : metric === 'buyers' ? 'chats que compraron' : 'chats';
    const peakIndex = values.findIndex(([, value]) => value === max);
    const total = values.reduce((sum, [, value]) => sum + value, 0);
    const summary = document.createElement('div');
    summary.className = 'campaigns-trend-summary';
    const totalText = document.createElement('span');
    const totalValue = document.createElement('strong');
    totalText.textContent = 'Total del periodo: ';
    totalValue.textContent = `${format(total, metric)} ${measure}`;
    totalText.append(totalValue);
    const peakText = document.createElement('span');
    const peakValue = document.createElement('strong');
    peakText.textContent = 'Pico: ';
    peakValue.textContent = `${format(max, metric)} ${measure}`;
    peakText.append(peakValue, ` · ${periodLabel(values[peakIndex][0], grouping)}`);
    summary.append(totalText, peakText);
    trendTarget.append(summary);

    const chart = document.createElement('div');
    chart.className = 'campaigns-plot-shell';
    const scale = document.createElement('div');
    scale.className = 'campaigns-plot-scale';
    const area = document.createElement('div');
    area.className = 'campaigns-plot-area';
    [1, 0.5, 0].forEach((part) => {
      const tick = document.createElement('span');
      tick.style.top = `${100 - part * 80}%`;
      tick.textContent = shortValue(max * part, metric);
      scale.append(tick);
      const line = document.createElement('span');
      line.className = 'campaigns-plot-grid';
      line.style.top = `${100 - part * 80}%`;
      line.setAttribute('aria-hidden', 'true');
      area.append(line);
    });
    const plot = document.createElement('div');
    plot.className = 'campaigns-plot';
    const labelEvery = Math.max(1, Math.ceil(values.length / (window.innerWidth < 640 ? 7 : 14)));
    const detail = document.createElement('p');
    detail.className = 'campaigns-trend-detail';
    detail.setAttribute('aria-live', 'polite');
    const selectBar = (item, date, value) => {
      plot.querySelectorAll('.is-selected').forEach((selected) => selected.classList.remove('is-selected'));
      item.classList.add('is-selected');
      detail.textContent = `${periodLabel(date, grouping)}: ${format(value, metric)} ${measure}`;
    };
    values.forEach(([date, value], index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'campaigns-plot-item';
      item.title = `${periodLabel(date, grouping)}: ${format(value, metric)} ${measure}`;
      item.setAttribute('aria-label', item.title);
      const stack = document.createElement('span');
      stack.className = 'campaigns-plot-stack';
      stack.style.height = `${value > 0 ? Math.max(1.5, value / max * 80) : 0}%`;
      const bar = document.createElement('span');
      bar.className = 'campaigns-plot-bar';
      const visibleValue = document.createElement('strong');
      visibleValue.className = 'campaigns-plot-value';
      visibleValue.textContent = value > 0 ? shortValue(value, metric) : '';
      if (values.length > 18 && index !== peakIndex && index !== values.length - 1) {
        visibleValue.classList.add('is-quiet');
      }
      stack.append(visibleValue, bar);
      const label = document.createElement('small');
      label.textContent = index % labelEvery === 0
        ? (grouping === 'year' ? date.slice(0, 4) : grouping === 'month' || grouping === 'quarter'
          ? date.slice(0, 7) : dateLabel(date)) : '';
      item.append(stack, label);
      item.addEventListener('click', () => selectBar(item, date, value));
      plot.append(item);
      if (index === peakIndex) selectBar(item, date, value);
    });
    area.append(plot);
    chart.append(scale, area);
    trendTarget.append(chart, detail);
    const note = document.createElement('p');
    note.className = 'campaigns-chart-note';
    const groupingLabel = { day: 'día', week: 'semana', month: 'mes', quarter: 'trimestre', year: 'año' };
    note.textContent = `Agrupado por ${groupingLabel[grouping]}. Toca una barra para ver su valor exacto.`;
    trendTarget.append(note);
  }

  function renderRanking() {
    const metric = rankingSelect.value;
    const ranked = data.rows
      .filter((row) => row.source_id && Number(row[metric]) > 0)
      .sort((a, b) => Number(b[metric]) - Number(a[metric]))
      .slice(0, 8);
    if (!ranked.length) return empty(rankingTarget, 'No hay anuncios con actividad para esta medida.');
    rankingTarget.replaceChildren();
    const max = Number(ranked[0][metric]);
    ranked.forEach((row) => {
      const item = document.createElement('div');
      item.className = 'campaigns-rank-item';
      const head = document.createElement('div');
      head.className = 'campaigns-rank-head';
      const label = document.createElement('span');
      label.textContent = row.label;
      label.title = row.label;
      const value = document.createElement('strong');
      value.textContent = format(row[metric], metric);
      head.append(label, value);
      const track = document.createElement('div');
      track.className = 'campaigns-rank-track';
      const fill = document.createElement('span');
      fill.style.width = `${Number(row[metric]) / max * 100}%`;
      track.append(fill);
      item.append(head, track);
      rankingTarget.append(item);
    });
    const note = document.createElement('p');
    note.className = 'campaigns-chart-note';
    note.textContent = 'Cada barra se compara con el anuncio que tiene el valor más alto.';
    rankingTarget.append(note);
  }
  trendSelect.addEventListener('change', renderTrend);
  rankingSelect.addEventListener('change', renderRanking);
  renderTrend();
  renderRanking();
})();
