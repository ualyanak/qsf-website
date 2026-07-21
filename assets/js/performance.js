/* global Chart */
(function () {
  "use strict";

  const REMOTE_DATA_URL =
    "https://raw.githubusercontent.com/ualyanak/qsf-website/market-data/data/performance.json";
  const LOCAL_DATA_URL = "./data/performance.json";
  const REFRESH_BUCKET_MS = 15 * 60 * 1000;
  const LOAD_TIMEOUT_MS = 9000;
  const RANGE_OPTIONS = [
    ["all", "All history"],
    ["5y", "Last 5 years"],
    ["3y", "Last 3 years"],
    ["1y", "Last 12 months"],
    ["ytd", "Year to date"],
  ];

  const state = {
    chart: null,
    columns: [],
    logScale: false,
    payload: null,
    range: "all",
    series: [],
  };

  function appendCacheBucket(url) {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}v=${Math.floor(Date.now() / REFRESH_BUCKET_MS)}`;
  }

  async function fetchJson(url) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), LOAD_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      validatePayload(payload);
      return payload;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function validatePayload(payload) {
    if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.series)) {
      throw new Error("unsupported data format");
    }
    if (!payload.series.some((series) => series && Array.isArray(series.points))) {
      throw new Error("performance series are missing");
    }
  }

  function shortError(error) {
    if (error && error.name === "AbortError") return "request timed out";
    if (error && typeof error.message === "string") return error.message.slice(0, 80);
    return "request failed";
  }

  async function loadSnapshot() {
    const failures = [];
    try {
      const payload = await fetchJson(appendCacheBucket(REMOTE_DATA_URL));
      return { failures, origin: "remote", payload };
    } catch (error) {
      failures.push(`Live refresh: ${shortError(error)}`);
    }

    try {
      const payload = await fetchJson(appendCacheBucket(LOCAL_DATA_URL));
      return { failures, origin: "local", payload };
    } catch (error) {
      failures.push(`Local snapshot: ${shortError(error)}`);
    }
    throw new Error(failures.join("; "));
  }

  function element(tag, attributes, text) {
    const node = document.createElement(tag);
    Object.entries(attributes || {}).forEach(([key, value]) => {
      if (key === "className") node.className = value;
      else node.setAttribute(key, value);
    });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function ensureStatus(canvas) {
    const designedStatus = document.querySelector("#performance .data-status");
    if (designedStatus) return designedStatus;
    let status = document.getElementById("performanceDataStatus");
    if (status) return status;
    status = element("div", {
      "aria-atomic": "true",
      "aria-live": "polite",
      className: "performance-data-status",
      id: "performanceDataStatus",
      role: "status",
    });
    const container = canvas.closest(".tracker-container") || canvas.parentElement;
    container.parentElement.insertBefore(status, container);
    return status;
  }

  function ensureRangeControl(canvas) {
    let select = document.getElementById("performanceRange");
    if (select) return select;
    const toolbar =
      document.querySelector("#performance .toolbar") ||
      document.querySelector(".toolbar") ||
      canvas.parentElement;
    const wrapper = element("span", { className: "performance-range-control" });
    const label = element("label", { for: "performanceRange" }, "Range");
    select = element("select", {
      "aria-label": "Performance chart date range",
      id: "performanceRange",
    });
    RANGE_OPTIONS.forEach(([value, labelText]) => {
      select.appendChild(element("option", { value }, labelText));
    });
    wrapper.append(label, select);
    toolbar.appendChild(wrapper);
    return select;
  }

  function ensureLogButton(canvas) {
    let button = document.getElementById("logToggleBtn");
    if (!button) {
      const toolbar =
        document.querySelector("#performance .toolbar") ||
        document.querySelector(".toolbar") ||
        canvas.parentElement;
      button = element(
        "button",
        { className: "btn", id: "logToggleBtn", type: "button" },
        "Use log scale"
      );
      toolbar.appendChild(button);
    }
    // Older pages used an inline handler. Removing it prevents a single click
    // from toggling twice when this progressively enhanced module is loaded.
    button.removeAttribute("onclick");
    button.setAttribute("aria-pressed", "false");
    return button;
  }

  function readableTimestamp(value, includeTime) {
    if (!value) return "unavailable";
    const raw = String(value);
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return String(value);
    const preserveUtcDate = /^\d{4}-\d{2}-\d{2}$/.test(raw)
      || (!includeTime && /T00:00:00(?:\.000)?Z$/.test(raw));
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric",
      hour: includeTime ? "numeric" : undefined,
      minute: includeTime ? "2-digit" : undefined,
      month: "short",
      timeZone: preserveUtcDate ? "UTC" : undefined,
      timeZoneName: includeTime ? "short" : undefined,
      year: "numeric",
    }).format(parsed);
  }

  function setStatus(status, loaded) {
    const snapshot = loaded.payload.snapshot || {};
    const live = ["current_public_sources", "live_public_sources"].includes(snapshot.benchmark_status);
    const stale = snapshot.benchmark_status === "stale_public_sources";
    const sourceText =
      loaded.origin === "remote"
        ? "Market-data branch"
        : "Checked-in local fallback";
    const checkedAt = snapshot.checked_at || loaded.payload.generated_at;
    const benchmarkText = live
      ? `Public benchmark sources checked ${readableTimestamp(checkedAt, true)}; each series retains its own as-of time.`
      : stale
        ? `Public sources responded, but one or more observations were older than the expected freshness window when checked ${readableTimestamp(checkedAt, true)}.`
        : `Benchmark refresh is unavailable; retained or static data is shown as of ${readableTimestamp(
            snapshot.benchmark_as_of || loaded.payload.generated_at,
            false
          )}.`;
    const strategyText = `QSF strategy history is management-reported through ${readableTimestamp(
      snapshot.fund_as_of || snapshot.strategy_as_of,
      false
    )} and is not live.`;
    const delayText = snapshot.delay_notice || "Public quotes may be delayed.";
    const errors = loaded.failures.length ? ` ${loaded.failures.join("; ")}.` : "";
    const summary = live && loaded.origin === "remote"
      ? "Best-effort public benchmarks connected"
      : stale
        ? "Some benchmark observations are stale"
        : "Checked-in benchmark snapshot";
    const asOf = checkedAt;
    const statusText = document.getElementById("chartStatus");
    const asOfText = document.getElementById("chartAsOf");
    if (statusText && asOfText) {
      statusText.textContent = summary;
      asOfText.textContent = `As of ${readableTimestamp(asOf, true)}`;
      status.title = `${sourceText}. ${benchmarkText} ${strategyText} ${delayText}${errors}`;
      const note = document.querySelector("#performance .data-note p");
      if (note) note.textContent = `${strategyText} ${delayText}`;
    } else {
      status.textContent = `${sourceText}. ${benchmarkText} ${strategyText} ${delayText}${errors}`;
    }
    status.classList.add("is-ready");
    status.classList.remove("is-error");
    status.classList.toggle("is-live", live && loaded.origin === "remote");
    status.classList.toggle("is-fallback", !live || loaded.origin !== "remote");
  }

  function monthLabel(period) {
    const parsed = new Date(`${period}-01T00:00:00Z`);
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      timeZone: "UTC",
      year: "numeric",
    }).format(parsed);
  }

  function buildColumns(seriesCollection) {
    const periods = new Set();
    let newestTimestamp = null;
    seriesCollection.forEach((series) => {
      (series.points || []).forEach((point) => {
        if (typeof point.period === "string") periods.add(point.period);
      });
      const timestamp = series.latest && series.latest.timestamp;
      if (timestamp && (!newestTimestamp || timestamp > newestTimestamp)) {
        newestTimestamp = timestamp;
      }
    });
    const columns = Array.from(periods)
      .sort()
      .map((period) => ({
        date: new Date(`${period}-01T00:00:00Z`),
        key: period,
        label: monthLabel(period),
        latest: false,
      }));
    if (newestTimestamp) {
      columns.push({
        date: new Date(newestTimestamp),
        key: "__latest__",
        label: "Latest available",
        latest: true,
      });
    }
    return columns;
  }

  function rangeCutoff(columns, range) {
    if (range === "all") return null;
    const latest = columns[columns.length - 1];
    const anchor = latest && latest.date instanceof Date ? new Date(latest.date) : new Date();
    if (range === "ytd") return new Date(Date.UTC(anchor.getUTCFullYear(), 0, 1));
    const years = range === "5y" ? 5 : range === "3y" ? 3 : 1;
    anchor.setUTCFullYear(anchor.getUTCFullYear() - years);
    anchor.setUTCDate(1);
    return anchor;
  }

  function visibleColumns() {
    const cutoff = rangeCutoff(state.columns, state.range);
    return cutoff ? state.columns.filter((column) => column.date >= cutoff) : state.columns.slice();
  }

  function seriesValues(series, columns) {
    const monthly = new Map((series.points || []).map((point) => [point.period, Number(point.value)]));
    return columns.map((column) => {
      if (column.latest) {
        return series.latest && Number.isFinite(Number(series.latest.value))
          ? Number(series.latest.value)
          : null;
      }
      const value = monthly.get(column.key);
      return Number.isFinite(value) ? value : null;
    });
  }

  function displayValues(rawValues) {
    if (!state.logScale) return rawValues.slice();
    return rawValues.map((value) => (value === null ? null : Math.max(0.0001, 100 + value)));
  }

  function makeDataset(series, columns) {
    const returns = seriesValues(series, columns);
    const radii = columns.map((column, index) =>
      column.latest && returns[index] !== null ? 3 : 0
    );
    return {
      _latestTimestamp: series.latest ? series.latest.timestamp : null,
      _qsfReturns: returns,
      _seriesId: series.id,
      borderColor: series.color || "#d6b35a",
      borderWidth: series.category === "strategy" ? 2.5 : 2,
      data: displayValues(returns),
      fill: false,
      hidden: Boolean(series.default_hidden),
      label: series.label,
      pointBackgroundColor: series.color || "#d6b35a",
      pointHoverRadius: 5,
      pointRadius: radii,
      spanGaps: false,
      tension: 0.22,
    };
  }

  const launchMarkers = {
    id: "qsfLaunchMarkers",
    beforeDatasetsDraw(chart) {
      const chartArea = chart.chartArea;
      const xScale = chart.scales.x;
      if (!chartArea || !xScale) return;
      const markers = [
        ["2023-08", "Medium–High split", "#d6b35a"],
        ["2024-12", "High–Extreme split", "#d9834d"],
      ];
      const columns = chart.$qsfColumns || visibleColumns();
      const context = chart.ctx;
      context.save();
      context.setLineDash([4, 5]);
      context.font = chart.width < 600 ? "11px sans-serif" : "12px sans-serif";
      markers.forEach(([period, text, color], markerIndex) => {
        const index = columns.findIndex((column) => column.key === period);
        if (index < 0) return;
        const x = xScale.getPixelForValue(index);
        context.strokeStyle = color;
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(x, chartArea.top);
        context.lineTo(x, chartArea.bottom);
        context.stroke();
        if (chart.width >= 480) {
          const width = context.measureText(text).width;
          context.fillStyle = color;
          context.fillText(
            text,
            Math.min(Math.max(x + 5, chartArea.left + 5), chartArea.right - width - 5),
            chartArea.top + 7 + markerIndex * 16
          );
        }
      });
      context.restore();
    },
  };

  function compactNumber(value) {
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: Math.abs(value) >= 1000 ? 0 : 1,
      notation: Math.abs(value) >= 10000 ? "compact" : "standard",
    }).format(value);
  }

  function chartOptions() {
    return {
      animation: { duration: 350 },
      interaction: { intersect: false, mode: "index" },
      maintainAspectRatio: false,
      normalized: true,
      plugins: {
        decimation: { enabled: true, samples: 150 },
        legend: {
          display: false,
        },
        tooltip: {
          callbacks: {
            afterLabel(context) {
              if (!context.dataset._latestTimestamp || !state.columns.find((column) => column.latest && column.label === context.label)) {
                return "";
              }
              return `As of ${readableTimestamp(context.dataset._latestTimestamp, true)}`;
            },
            label(context) {
              const value = context.dataset._qsfReturns[context.dataIndex];
              if (!Number.isFinite(value)) return "";
              const sign = value > 0 ? "+" : "";
              return `${context.dataset.label}: ${sign}${value.toFixed(2)}%`;
            },
          },
        },
      },
      responsive: true,
      scales: {
        x: {
          grid: { display: false },
          ticks: { autoSkip: true, maxRotation: 0, maxTicksLimit: 12 },
        },
        y: {
          beginAtZero: true,
          ticks: {
            callback(value) {
              return state.logScale ? `$${compactNumber(value)}` : `${compactNumber(value)}%`;
            },
          },
          title: {
            display: true,
            text: "Cumulative return (%)",
          },
          type: "linear",
        },
      },
    };
  }

  function updateChartData() {
    if (!state.chart) return;
    const columns = visibleColumns();
    const existingVisibility = new Map(
      state.chart.data.datasets.map((dataset, index) => [
        dataset._seriesId,
        !state.chart.isDatasetVisible(index),
      ])
    );
    state.chart.$qsfColumns = columns;
    state.chart.data.labels = columns.map((column) => column.label);
    state.chart.data.datasets = state.series.map((series) => {
      const dataset = makeDataset(series, columns);
      if (existingVisibility.has(series.id)) dataset.hidden = existingVisibility.get(series.id);
      return dataset;
    });
    state.chart.data.datasets.forEach((dataset, index) => {
      state.chart.setDatasetVisibility(index, !dataset.hidden);
    });
    const yScale = state.chart.options.scales.y;
    yScale.type = state.logScale ? "logarithmic" : "linear";
    yScale.beginAtZero = !state.logScale;
    yScale.title.text = state.logScale
      ? "Growth of $100 (log scale)"
      : "Cumulative return (%)";
    state.chart.update();
    renderCustomLegend();
  }

  function updateScaleButtons() {
    document.querySelectorAll("[data-scale]").forEach((button) => {
      const selected = (button.dataset.scale === "logarithmic") === state.logScale;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    const legacyButton = document.getElementById("logToggleBtn");
    if (legacyButton) {
      legacyButton.textContent = state.logScale ? "Use linear scale" : "Use log scale";
      legacyButton.setAttribute("aria-pressed", String(state.logScale));
    }
  }

  function setLogScale(enabled) {
    state.logScale = Boolean(enabled);
    updateScaleButtons();
    updateChartData();
  }

  function toggleLogScale() {
    setLogScale(!state.logScale);
  }

  function renderCustomLegend() {
    const container = document.getElementById("chartLegend");
    if (!container || !state.chart) return;
    container.replaceChildren();
    state.chart.data.datasets.forEach((dataset, index) => {
      const visible = state.chart.isDatasetVisible(index);
      const button = element("button", {
        "aria-pressed": String(visible),
        type: "button",
      });
      const swatch = element("span", { className: "legend-swatch", "aria-hidden": "true" });
      swatch.style.backgroundColor = dataset.borderColor;
      button.append(swatch, element("span", {}, dataset.label));
      button.addEventListener("click", () => {
        state.chart.setDatasetVisibility(index, !state.chart.isDatasetVisible(index));
        state.chart.update();
        renderCustomLegend();
      });
      container.append(button);
    });
  }

  function latestSeriesPoint(series) {
    if (series.latest && Number.isFinite(Number(series.latest.value))) {
      return {
        asOf: series.latest.timestamp,
        value: Number(series.latest.value),
      };
    }
    const points = Array.isArray(series.points) ? series.points : [];
    const point = points.length ? points[points.length - 1] : null;
    return point ? { asOf: point.date, value: Number(point.value) } : null;
  }

  function renderFallback(payload, message) {
    const wrapper = document.getElementById("chartFallback");
    const body = document.querySelector("#performanceFallbackTable tbody");
    if (!wrapper || !body) return;
    const messageNode = document.getElementById("chartFallbackMessage");
    if (messageNode && message) messageNode.textContent = message;
    body.replaceChildren();
    (payload && Array.isArray(payload.series) ? payload.series : []).forEach((series) => {
      const latest = latestSeriesPoint(series);
      const row = document.createElement("tr");
      const returnText = latest && Number.isFinite(latest.value)
        ? `${latest.value > 0 ? "+" : ""}${latest.value.toFixed(2)}%`
        : "Unavailable";
      row.append(
        element("th", { scope: "row" }, series.label || series.id),
        element("td", {}, returnText),
        element("td", {}, latest ? readableTimestamp(latest.asOf, false) : "Unavailable")
      );
      body.append(row);
    });
    wrapper.hidden = false;
  }

  function render(canvas, payload) {
    state.payload = payload;
    state.series = payload.series;
    state.columns = buildColumns(state.series);
    const columns = visibleColumns();
    const existing = typeof Chart.getChart === "function" ? Chart.getChart(canvas) : null;
    if (existing) existing.destroy();
    canvas.setAttribute(
      "aria-label",
      "Interactive cumulative performance comparison from January 2020; use the legend to show or hide series."
    );
    canvas.setAttribute("role", "img");
    state.chart = new Chart(canvas.getContext("2d"), {
      data: {
        datasets: state.series.map((series) => makeDataset(series, columns)),
        labels: columns.map((column) => column.label),
      },
      options: chartOptions(),
      plugins: [launchMarkers],
      type: "line",
    });
    state.chart.$qsfColumns = columns;
    const fallback = document.getElementById("chartFallback");
    if (fallback) fallback.hidden = true;
    renderCustomLegend();
  }

  async function waitForChart() {
    const started = Date.now();
    while (typeof window.Chart !== "function") {
      if (Date.now() - started > LOAD_TIMEOUT_MS) throw new Error("Chart.js did not load");
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
  }

  async function initialize() {
    const canvas = document.getElementById("performanceChart");
    if (!canvas) return;
    const status = ensureStatus(canvas);
    const statusText = document.getElementById("chartStatus");
    const asOfText = document.getElementById("chartAsOf");
    if (statusText) {
      statusText.textContent = "Loading delayed public market data…";
      if (asOfText) asOfText.textContent = "As of —";
    } else {
      status.textContent = "Loading delayed public market data…";
    }
    const rangeButtons = Array.from(document.querySelectorAll("#chartRange [data-range]"));
    if (rangeButtons.length) {
      rangeButtons.forEach((button) => {
        button.addEventListener("click", () => {
          state.range = String(button.dataset.range || "all").toLowerCase();
          rangeButtons.forEach((candidate) => {
            const selected = candidate === button;
            candidate.classList.toggle("is-active", selected);
            candidate.setAttribute("aria-pressed", String(selected));
          });
          updateChartData();
        });
      });
    } else {
      const range = ensureRangeControl(canvas);
      range.addEventListener("change", () => {
        state.range = range.value;
        updateChartData();
      });
    }

    const scaleButtons = Array.from(document.querySelectorAll("[data-scale]"));
    if (scaleButtons.length) {
      scaleButtons.forEach((button) => {
        button.addEventListener("click", () => setLogScale(button.dataset.scale === "logarithmic"));
      });
    } else {
      ensureLogButton(canvas).addEventListener("click", toggleLogScale);
    }
    window.toggleLogScale = toggleLogScale;
    let loaded = null;
    try {
      loaded = await loadSnapshot();
      setStatus(status, loaded);
      await waitForChart();
      render(canvas, loaded.payload);
    } catch (error) {
      status.classList.remove("is-ready");
      status.classList.add("is-error");
      const statusText = document.getElementById("chartStatus");
      const message = loaded
        ? `Interactive chart unavailable (${shortError(error)}); accessible table shown.`
        : `Performance data unavailable (${shortError(error)}).`;
      if (statusText) statusText.textContent = message;
      else status.textContent = message;
      renderFallback(loaded && loaded.payload, message);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
