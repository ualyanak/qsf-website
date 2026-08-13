(function (root) {
  "use strict";

  const config = Object.assign({
    mode: "public-demo",
    demoDataUrl: "../data/demo-accounts.json",
    demoQuoteUrls: ["../data/demo-quotes.json"],
    demoSessionKey: "qsf.publicDemo.session.v1",
    demoStoragePrefix: "qsf.publicDemo.account."
  }, root.QSF_PORTAL_CONFIG || {});
  const QUOTE_REFRESH_INTERVAL_MS = 15 * 60 * 1000;
  const MAX_EXPECTED_QUOTE_AGE_MS = 4 * 24 * 60 * 60 * 1000;

  const page = document.body && document.body.dataset.page;
  const colors = ["#15344f", "#c9a24f", "#3d6f8c", "#7b8793", "#967638", "#56816f", "#8b5f63", "#445467"];
  const state = {
    data: null,
    quotes: null,
    accountId: null,
    account: null,
    local: null,
    view: null,
    storageAvailable: true
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(target, value) {
    const node = typeof target === "string" ? byId(target) : target;
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function setMessage(id, message, type) {
    const node = byId(id);
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("is-error", type === "error");
    node.classList.toggle("is-success", type === "success");
  }

  function setStatus(kind, message) {
    const node = byId("portal-status");
    if (!node) return;
    node.classList.remove("is-pending", "is-ready", "is-error");
    node.classList.add(kind === "ready" ? "is-ready" : kind === "error" ? "is-error" : "is-pending");
    setText(node.querySelector("[data-status-text]"), message);
  }

  function enableAuthControls() {
    document.querySelectorAll("[data-auth-control]").forEach(function (control) {
      control.disabled = false;
    });
  }

  function setBusy(form, busy) {
    if (!form) return;
    form.setAttribute("aria-busy", busy ? "true" : "false");
    form.querySelectorAll("button, input, select, textarea").forEach(function (control) {
      if (busy) {
        control.dataset.demoWasDisabled = control.disabled ? "true" : "false";
        control.disabled = true;
      } else if (control.dataset.demoWasDisabled !== "true") {
        control.disabled = false;
      }
      if (!busy) delete control.dataset.demoWasDisabled;
    });
  }

  function safeString(value, limit) {
    return String(value == null ? "" : value).replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, limit || 200);
  }

  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function formatCurrency(value, currency, cents) {
    const number = finite(value);
    if (number == null) return "—";
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: cents === false ? 0 : 2,
      maximumFractionDigits: cents === false ? 0 : 2
    }).format(number);
  }

  function formatNumber(value, digits) {
    const number = finite(value);
    if (number == null) return "—";
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits == null ? 4 : digits }).format(number);
  }

  function formatPercent(value) {
    const number = finite(value);
    if (number == null) return "—";
    return (number >= 0 ? "+" : "") + number.toFixed(2) + "%";
  }

  function parseDate(value) {
    if (!value) return null;
    const date = /^\d{4}-\d{2}-\d{2}$/.test(String(value))
      ? new Date(String(value) + "T12:00:00")
      : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function timestampRank(value) {
    const date = parseDate(value);
    return date ? date.getTime() : 0;
  }

  function quoteRecency(quote, snapshot) {
    return [
      timestampRank(quote && quote.as_of),
      timestampRank(quote && quote.valuation_as_of) || timestampRank(snapshot && snapshot.generated_at),
      timestampRank(snapshot && snapshot.generated_at)
    ];
  }

  function isNewerQuote(candidate, existing) {
    if (!existing) return true;
    for (let index = 0; index < candidate.length; index += 1) {
      if (candidate[index] !== existing[index]) return candidate[index] > existing[index];
    }
    return false;
  }

  function formatDate(value, withTime) {
    const date = value instanceof Date ? value : parseDate(value);
    if (!date) return "—";
    return new Intl.DateTimeFormat("en-US", withTime
      ? { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }
      : { month: "short", day: "numeric", year: "numeric" }).format(date);
  }

  function todayKey() {
    const date = new Date();
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
  }

  function localDateTimeValue(date) {
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
  }

  function makeId() {
    if (root.crypto && typeof root.crypto.randomUUID === "function") return root.crypto.randomUUID();
    return "demo-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  async function fetchJson(url, timeout) {
    const controller = new AbortController();
    const timer = root.setTimeout(function () { controller.abort(); }, timeout || 12000);
    try {
      const target = new URL(url, document.baseURI);
      target.searchParams.set("demo_refresh", String(Date.now()));
      const response = await fetch(target.href, { signal: controller.signal, cache: "no-store", credentials: "omit" });
      if (!response.ok) throw new Error("request_failed");
      return await response.json();
    } finally {
      root.clearTimeout(timer);
    }
  }

  function validDemoData(payload) {
    return payload && payload.demo === true && payload.accounts && payload.credentials && payload.instruments;
  }

  async function loadData() {
    if (state.data) return state.data;
    const payload = await fetchJson(config.demoDataUrl);
    if (!validDemoData(payload)) throw new Error("invalid_demo_data");
    state.data = payload;
    return payload;
  }

  async function loadQuotes(force) {
    if (state.quotes && !force) return state.quotes;
    const previous = state.quotes;
    const urls = Array.isArray(config.demoQuoteUrls) ? config.demoQuoteUrls : ["../data/demo-quotes.json"];
    const candidates = previous ? [previous] : [];
    for (const url of urls) {
      try {
        const payload = await fetchJson(url);
        if (payload && payload.demo === true && payload.quotes) {
          candidates.push(payload);
        }
      } catch (_error) {
        /* Try the packaged fallback next. */
      }
    }
    if (candidates.length) {
      const newestSnapshot = candidates.slice().sort(function (left, right) {
        const leftDate = parseDate(left.generated_at);
        const rightDate = parseDate(right.generated_at);
        return (rightDate ? rightDate.getTime() : 0) - (leftDate ? leftDate.getTime() : 0);
      })[0];
      const mergedQuotes = {};
      const mergedRecency = {};
      candidates.forEach(function (snapshot) {
        Object.entries(snapshot.quotes || {}).forEach(function (entry) {
          const symbol = entry[0];
          const quote = entry[1];
          if (!quote || finite(quote.price) == null) return;
          const recency = quoteRecency(quote, snapshot);
          if (isNewerQuote(recency, mergedRecency[symbol])) {
            mergedQuotes[symbol] = quote;
            mergedRecency[symbol] = recency;
          }
        });
      });
      state.quotes = Object.assign({}, newestSnapshot, { quotes: mergedQuotes });
      return state.quotes;
    }
    state.quotes = { demo: true, generated_at: null, quotes: {}, failures: ["quote_snapshot_unavailable"] };
    return state.quotes;
  }

  function saveSession(accountId) {
    const session = { accountId: accountId, openedAt: new Date().toISOString() };
    try {
      sessionStorage.setItem(config.demoSessionKey, JSON.stringify(session));
      return session;
    } catch (_error) {
      return null;
    }
  }

  function readSession() {
    try {
      const session = JSON.parse(sessionStorage.getItem(config.demoSessionKey) || "null");
      if (!session || typeof session.accountId !== "string") return null;
      return session;
    } catch (_error) {
      return null;
    }
  }

  function clearSession() {
    try { sessionStorage.removeItem(config.demoSessionKey); } catch (_error) { /* no-op */ }
  }

  function routeToLogin() {
    root.location.replace(page === "admin" ? "../investor_login/" : "./index.html");
  }

  function emptyLocalState() {
    return { version: 1, transactions: [], marks: {}, modifiedAt: null };
  }

  function localStorageKey(accountId) {
    return config.demoStoragePrefix + accountId + ".v1";
  }

  function validateLocalState(value) {
    if (!value || value.version !== 1) return emptyLocalState();
    const transactions = Array.isArray(value.transactions) ? value.transactions.slice(-500) : [];
    const marks = value.marks && typeof value.marks === "object" && !Array.isArray(value.marks) ? value.marks : {};
    return {
      version: 1,
      transactions: transactions,
      marks: marks,
      modifiedAt: typeof value.modifiedAt === "string" ? value.modifiedAt : null
    };
  }

  function readLocalState(accountId) {
    try {
      const raw = localStorage.getItem(localStorageKey(accountId));
      if (!raw) return emptyLocalState();
      if (raw.length > 500000) throw new Error("local_state_too_large");
      return validateLocalState(JSON.parse(raw));
    } catch (_error) {
      state.storageAvailable = false;
      return emptyLocalState();
    }
  }

  function writeLocalState() {
    state.local.modifiedAt = new Date().toISOString();
    try {
      localStorage.setItem(localStorageKey(state.accountId), JSON.stringify(state.local));
      state.storageAvailable = true;
      return true;
    } catch (_error) {
      state.storageAvailable = false;
      return false;
    }
  }

  function resolveInstrument(input) {
    const target = safeString(input, 48).toUpperCase();
    if (!target || !state.data) return null;
    const entries = Object.entries(state.data.instruments);
    for (const entry of entries) {
      const id = entry[0];
      const instrument = entry[1];
      if (id.toUpperCase() === target || String(instrument.symbol || "").toUpperCase() === target) {
        return { id: id, instrument: instrument };
      }
    }
    const custom = (state.local && state.local.transactions || []).find(function (event) {
      return event.instrumentId === target || String(event.symbol || "").toUpperCase() === target;
    });
    return custom ? {
      id: custom.instrumentId,
      instrument: {
        symbol: custom.symbol,
        name: custom.name || custom.symbol,
        asset_class: custom.assetClass || "Other",
        multiplier: Number(custom.multiplier) || 1,
        mark_mode: "local"
      }
    } : null;
  }

  function customInstrumentId(symbol) {
    return safeString(symbol, 48).toUpperCase().replace(/[^A-Z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "CUSTOM";
  }

  function usesAutomaticMark(instrument) {
    return instrument && ["public_delayed", "model_delayed"].includes(instrument.mark_mode);
  }

  function derivePortfolio(accountId) {
    const account = state.data.accounts[accountId];
    if (!account) throw new Error("unknown_demo_account");
    const local = state.local || emptyLocalState();
    const instruments = Object.assign({}, state.data.instruments);
    const quantities = new Map();
    const fallbackBasis = new Map();

    (account.positions || []).forEach(function (position) {
      quantities.set(position.instrument, (quantities.get(position.instrument) || 0) + Number(position.quantity || 0));
      fallbackBasis.set(position.instrument, Number(position.basis_price));
    });

    let cash = Number(account.cash || 0);
    (local.transactions || []).forEach(function (event) {
      if (!event || event.cancelled === true) return;
      const id = safeString(event.instrumentId, 64);
      const quantity = Number(event.signedQuantity);
      const cashEffect = Number(event.cashEffect);
      if (!id || !Number.isFinite(quantity) || !Number.isFinite(cashEffect)) return;
      if (!instruments[id]) {
        instruments[id] = {
          symbol: safeString(event.symbol || id, 48),
          name: safeString(event.name || event.symbol || id, 100),
          asset_class: safeString(event.assetClass || "Other", 40),
          multiplier: Number(event.multiplier) || 1,
          mark_mode: "local"
        };
      }
      quantities.set(id, (quantities.get(id) || 0) + quantity);
      cash += cashEffect;
      if (Number.isFinite(Number(event.price))) fallbackBasis.set(id, Number(event.price));
    });

    const holdings = [];
    let positionsValue = 0;
    let latestMarkDate = null;
    quantities.forEach(function (quantity, id) {
      if (Math.abs(quantity) < 0.00000001) return;
      const instrument = instruments[id] || { symbol: id, name: id, asset_class: "Other", multiplier: 1, mark_mode: "local" };
      const localMark = local.marks && local.marks[id];
      const publicSymbol = instrument.quote_symbol || id;
      const publicMark = state.quotes && state.quotes.quotes && state.quotes.quotes[publicSymbol];
      let mark;
      if (usesAutomaticMark(instrument) && publicMark && finite(publicMark.price) != null) {
        const publicAsOf = parseDate(publicMark.as_of);
        const publicAge = publicAsOf ? Date.now() - publicAsOf.getTime() : Infinity;
        const publishedQuality = safeString(publicMark.quality || "public_delayed", 40);
        const expectedQuality = instrument.mark_mode === "model_delayed" ? "model_delayed" : "public_delayed";
        const clientQuality = publishedQuality === expectedQuality
          && publicAge >= -5 * 60 * 1000
          && publicAge <= MAX_EXPECTED_QUOTE_AGE_MS
          ? expectedQuality
          : instrument.mark_mode === "model_delayed" ? "stale_model" : "stale_fallback";
        mark = {
          price: Number(publicMark.price),
          asOf: instrument.mark_mode === "model_delayed" && parseDate(publicMark.valuation_as_of)
            ? publicMark.valuation_as_of
            : publicMark.as_of,
          inputAsOf: publicMark.as_of,
          source: safeString(publicMark.source || "Public delayed snapshot", 100),
          quality: clientQuality
        };
      } else if (localMark && finite(localMark.price) != null) {
        mark = {
          price: Number(localMark.price),
          asOf: localMark.asOf,
          inputAsOf: localMark.asOf,
          source: safeString(localMark.source || "Manual demo mark", 100),
          quality: "local_manual"
        };
      } else if (finite(instrument.manual_mark) != null) {
        mark = {
          price: Number(instrument.manual_mark),
          asOf: instrument.manual_as_of,
          inputAsOf: instrument.manual_as_of,
          source: safeString(instrument.manual_source || "Published manual test mark", 100),
          quality: "manual_demo"
        };
      } else {
        mark = {
          price: Number(fallbackBasis.get(id) || 0),
          asOf: account.opening_as_of,
          inputAsOf: account.opening_as_of,
          source: "Published opening-basis fallback",
          quality: "fallback_opening_mark"
        };
      }

      const date = parseDate(mark.asOf);
      if (date && (!latestMarkDate || date > latestMarkDate)) latestMarkDate = date;
      const multiplier = Number(instrument.multiplier) || 1;
      const marketValue = quantity * multiplier * Number(mark.price || 0);
      positionsValue += marketValue;
      holdings.push({
        id: id,
        symbol: instrument.symbol || id,
        name: instrument.name || "",
        assetClass: instrument.asset_class || "Other",
        quantity: quantity,
        multiplier: multiplier,
        price: Number(mark.price || 0),
        marketValue: marketValue,
        markAsOf: mark.asOf,
        markInputAsOf: mark.inputAsOf,
        markSource: mark.source,
        markQuality: mark.quality
      });
    });

    holdings.sort(function (a, b) { return Math.abs(b.marketValue) - Math.abs(a.marketValue); });
    const nav = cash + positionsValue;
    const openingNav = Number(account.opening_nav);
    const returnPct = openingNav ? (nav / openingNav - 1) * 100 : null;
    const grossGroups = new Map();
    holdings.forEach(function (holding) {
      grossGroups.set(holding.assetClass, (grossGroups.get(holding.assetClass) || 0) + Math.abs(holding.marketValue));
    });
    if (cash > 0) grossGroups.set("Cash", (grossGroups.get("Cash") || 0) + cash);
    const allocationTotal = Array.from(grossGroups.values()).reduce(function (sum, value) { return sum + value; }, 0);
    const allocation = Array.from(grossGroups.entries()).map(function (entry) {
      return { name: entry[0], value: entry[1], percent: allocationTotal ? entry[1] / allocationTotal * 100 : 0 };
    }).sort(function (a, b) { return b.value - a.value; });

    const historyByDate = new Map();
    (account.history || []).forEach(function (point) {
      if (point && /^\d{4}-\d{2}-\d{2}$/.test(point.date) && finite(point.value) != null) {
        historyByDate.set(point.date, { date: point.date, value: Number(point.value), kind: point.kind || "published_test" });
      }
    });
    historyByDate.set(todayKey(), { date: todayKey(), value: nav, kind: local.modifiedAt ? "local_scenario" : "latest_marks" });
    const history = Array.from(historyByDate.values()).sort(function (a, b) { return a.date.localeCompare(b.date); });
    const staleCount = holdings.filter(function (holding) {
      return !["public_delayed", "model_delayed"].includes(holding.markQuality);
    }).length;

    return {
      demo: true,
      accountId: accountId,
      accountName: account.display_name,
      portfolioName: account.portfolio_name,
      currency: account.currency || "USD",
      openingAsOf: account.opening_as_of,
      openingNav: openingNav,
      baselineLabel: account.baseline_label || "Published test baseline",
      returnBasisLabel: account.return_basis_label || "Illustrative return",
      returnBasisNote: account.return_basis_note || ("From " + formatCurrency(openingNav, account.currency || "USD") + " test baseline"),
      cash: cash,
      nav: nav,
      returnPct: returnPct,
      positionsValue: positionsValue,
      holdings: holdings,
      allocation: allocation,
      history: history,
      latestMarkAsOf: latestMarkDate ? latestMarkDate.toISOString() : null,
      quoteSnapshotGeneratedAt: state.quotes && state.quotes.generated_at,
      staleCount: staleCount,
      modifiedAt: local.modifiedAt,
      storageAvailable: state.storageAvailable,
      generatedAt: new Date().toISOString()
    };
  }

  function textCell(value, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value;
    return cell;
  }

  function markLabel(quality) {
    if (quality === "local_manual") return { label: "Local manual", className: "is-manual" };
    if (quality === "public_delayed") return { label: "Public delayed", className: "is-public" };
    if (quality === "model_delayed") return { label: "Auto model", className: "is-model" };
    if (quality === "manual_demo") return { label: "Manual test", className: "is-manual" };
    if (quality === "stale_model") return { label: "Stale model", className: "is-stale" };
    return { label: "Stale fallback", className: "is-stale" };
  }

  function renderChart(history, currency) {
    const shell = byId("performance-chart");
    if (!shell || !Array.isArray(history)) return;
    const points = history.map(function (item) {
      return { date: parseDate(item.date), value: Number(item.value) };
    }).filter(function (item) { return item.date && Number.isFinite(item.value); });
    if (points.length < 2) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const title = document.createElement("strong");
      title.textContent = "No dated performance history yet";
      const note = document.createElement("span");
      note.textContent = "The supplied cost basis has no acquisition date, so it is not plotted as a dated return.";
      empty.append(title, note);
      shell.replaceChildren(empty);
      shell.setAttribute("aria-label", "No dated performance history is available for this demonstration portfolio.");
      setText("chart-start", "Dated history pending");
      setText("chart-end", points.length ? "Latest " + formatCurrency(points[0].value, currency) : "Latest —");
      return;
    }
    const width = 900;
    const height = 310;
    const pad = { top: 25, right: 25, bottom: 30, left: 25 };
    const values = points.map(function (point) { return point.value; });
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (min === max) { min -= Math.max(1, Math.abs(min) * 0.01); max += Math.max(1, Math.abs(max) * 0.01); }
    const spread = max - min || 1;
    min -= spread * 0.12;
    max += spread * 0.12;
    function x(index) { return pad.left + index * ((width - pad.left - pad.right) / (points.length - 1)); }
    function y(value) { return pad.top + (max - value) * ((height - pad.top - pad.bottom) / (max - min)); }
    const linePath = points.map(function (point, index) {
      return (index ? "L" : "M") + x(index).toFixed(2) + " " + y(point.value).toFixed(2);
    }).join(" ");
    const areaPath = linePath + " L" + x(points.length - 1).toFixed(2) + " " + (height - pad.bottom) + " L" + x(0).toFixed(2) + " " + (height - pad.bottom) + " Z";
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("preserveAspectRatio", "none");
    const area = document.createElementNS(ns, "path");
    area.setAttribute("d", areaPath);
    area.setAttribute("fill", "rgba(201, 162, 79, .17)");
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", linePath);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#15344f");
    line.setAttribute("stroke-width", "3");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("stroke-linejoin", "round");
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", x(points.length - 1));
    dot.setAttribute("cy", y(points[points.length - 1].value));
    dot.setAttribute("r", "5");
    dot.setAttribute("fill", "#c9a24f");
    dot.setAttribute("stroke", "#fff");
    dot.setAttribute("stroke-width", "3");
    svg.append(area, line, dot);
    shell.replaceChildren(svg);
    shell.setAttribute("aria-label", "Illustrative value from " + formatDate(points[0].date) + " to " + formatDate(points[points.length - 1].date) + ", ending at " + formatCurrency(points[points.length - 1].value, currency));
    setText("chart-start", "Start " + formatDate(points[0].date));
    setText("chart-end", "Latest " + formatCurrency(points[points.length - 1].value, currency));
  }

  function renderAllocation(view) {
    const ring = document.querySelector(".allocation-ring");
    const legend = byId("allocation-legend");
    if (!ring || !legend || !view.allocation.length) return;
    let cursor = 0;
    const segments = view.allocation.map(function (item, index) {
      const start = cursor;
      cursor += item.percent;
      return colors[index % colors.length] + " " + start.toFixed(2) + "% " + cursor.toFixed(2) + "%";
    });
    ring.style.background = "conic-gradient(" + segments.join(",") + ")";
    setText(ring.querySelector("span"), "Gross mix");
    legend.replaceChildren();
    view.allocation.forEach(function (item, index) {
      const row = document.createElement("div");
      row.className = "legend-row";
      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      swatch.style.backgroundColor = colors[index % colors.length];
      const name = document.createElement("span");
      name.textContent = item.name;
      const value = document.createElement("strong");
      value.textContent = item.percent.toFixed(1) + "%";
      value.title = formatCurrency(item.value, view.currency) + " gross exposure";
      row.append(swatch, name, value);
      legend.append(row);
    });
  }

  function renderHoldings(view) {
    const body = byId("holdings-body");
    if (!body) return;
    body.replaceChildren();
    view.holdings.forEach(function (holding) {
      const row = document.createElement("tr");
      const instrumentCell = document.createElement("td");
      const symbol = document.createElement("span");
      symbol.className = "holding-symbol";
      symbol.textContent = holding.symbol;
      const name = document.createElement("span");
      name.className = "holding-name";
      name.textContent = holding.name;
      instrumentCell.append(symbol, name);
      const statusCell = document.createElement("td");
      const status = markLabel(holding.markQuality);
      const badge = document.createElement("span");
      badge.className = "source-badge " + status.className;
      badge.textContent = status.label;
      badge.title = holding.markSource + " · " + formatDate(holding.markAsOf, true);
      const detail = document.createElement("span");
      detail.className = "mark-detail";
      detail.textContent = holding.markSource + " · Valued " + formatDate(holding.markAsOf, true)
        + (holding.markInputAsOf && holding.markInputAsOf !== holding.markAsOf
          ? " · market input " + formatDate(holding.markInputAsOf, true)
          : "");
      statusCell.append(badge, detail);
      row.append(
        instrumentCell,
        textCell(formatNumber(holding.quantity, 6)),
        textCell(formatCurrency(holding.price, view.currency)),
        textCell(formatCurrency(holding.marketValue, view.currency)),
        textCell(view.nav ? formatPercent(holding.marketValue / view.nav * 100) : "—"),
        statusCell
      );
      body.append(row);
    });
    setText("holdings-note", view.staleCount
      ? view.staleCount + " holding mark" + (view.staleCount === 1 ? " is" : "s are") + " manual or stale. Source and time appear beneath each status."
      : "Equities use best-effort public snapshots; options use automatic model estimates from delayed underlier prices. Every source and time is shown.");
  }

  function renderDashboard(view) {
    state.view = view;
    setText("account-name", view.accountId + " · public demo");
    setText("dashboard-title", view.portfolioName);
    setText("metric-aum", formatCurrency(view.nav, view.currency));
    setText("metric-aum-note", "Latest available marks · not official NAV");
    setText("metric-return-label", view.returnBasisLabel);
    setText("metric-return", formatPercent(view.returnPct));
    setText("metric-inception", view.returnBasisNote);
    setText("metric-cash", formatCurrency(view.cash, view.currency));
    setText("metric-quote-age", view.latestMarkAsOf ? formatDate(view.latestMarkAsOf, true) : "Fallback marks");
    setText("portfolio-as-of", "Calculated " + formatDate(view.generatedAt, true) + (view.staleCount
      ? " from the latest available automatic, manual, and fallback marks."
      : " from delayed public marks and automatic option model estimates."));
    setText("scenario-state", view.modifiedAt ? "Locally modified" : "Published sample");
    renderChart(view.history, view.currency);
    renderAllocation(view);
    renderHoldings(view);
    setStatus("ready", view.modifiedAt
      ? "Public demo loaded with local scenario edits from this browser; source badges identify any manual or fallback marks."
      : view.staleCount
        ? "Public demo loaded with the latest available marks; source badges identify stale or fallback values."
        : "Public demo loaded. Equity marks and option model inputs refresh on a best-effort 15-minute cadence.");
  }

  function attachSignOut() {
    const button = byId("sign-out");
    if (!button) return;
    button.addEventListener("click", function () {
      clearSession();
      routeToLogin();
    });
  }

  async function initLogin() {
    if (config.mode !== "public-demo") throw new Error("demo_mode_disabled");
    const form = byId("login-form");
    await loadData();
    enableAuthControls();
    setStatus("ready", "Public demonstration is available. No private authentication is performed.");

    const session = readSession();
    if (session && state.data.accounts[session.accountId]) {
      root.location.replace("./dashboard.html");
      return;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      setMessage("login-message", "");
      if (!form.reportValidity()) return;
      const accountId = safeString(byId("login-username").value, 48).toLowerCase();
      const code = String(byId("login-password").value || "");
      if (!state.data.accounts[accountId] || state.data.credentials[accountId] !== code) {
        setMessage("login-message", "That username or access code was not recognized.", "error");
        return;
      }
      if (!saveSession(accountId)) {
        setMessage("login-message", "This browser blocked temporary demo session storage. Enable session storage and try again.", "error");
        return;
      }
      root.location.assign("./dashboard.html");
    });
  }

  async function bootstrapDemo() {
    const session = readSession();
    if (!session) {
      routeToLogin();
      return false;
    }
    await Promise.all([loadData(), loadQuotes()]);
    if (!state.data.accounts[session.accountId]) {
      clearSession();
      routeToLogin();
      return false;
    }
    state.accountId = session.accountId;
    state.account = state.data.accounts[state.accountId];
    state.local = readLocalState(state.accountId);
    let migratedAutomaticMarks = false;
    Object.keys(state.local.marks || {}).forEach(function (instrumentId) {
      if (usesAutomaticMark(state.data.instruments[instrumentId])) {
        delete state.local.marks[instrumentId];
        migratedAutomaticMarks = true;
      }
    });
    if (migratedAutomaticMarks) {
      if (!(state.local.transactions || []).length && !Object.keys(state.local.marks).length) state.local.modifiedAt = null;
      try { localStorage.setItem(localStorageKey(state.accountId), JSON.stringify(state.local)); } catch (_error) { state.storageAvailable = false; }
    }
    return true;
  }

  async function generateReport() {
    const button = byId("request-report");
    if (!button || !state.view || !root.QSFDemoPdf) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    setMessage("report-message", "Refreshing public marks before building the demonstration PDF…");
    try {
      await loadQuotes(true);
      renderDashboard(derivePortfolio(state.accountId));
      setMessage("report-message", "Building the demonstration PDF in this browser…");
      const result = await root.QSFDemoPdf.download(state.view, "../assets/images/qsf-mark.png");
      setMessage("report-message", "Demo PDF generated and downloaded.", "success");
      const jobs = byId("report-jobs");
      if (jobs) {
        jobs.replaceChildren();
        const row = document.createElement("div");
        row.className = "report-job";
        const name = document.createElement("strong");
        name.textContent = result.filename;
        const status = document.createElement("span");
        status.className = "job-state";
        status.textContent = "Downloaded";
        const note = document.createElement("span");
        note.textContent = formatDate(new Date(), true) + " · generated locally";
        row.append(name, status, note);
        jobs.append(row);
      }
    } catch (_error) {
      setMessage("report-message", "The demo PDF could not be generated in this browser. Please try again.", "error");
    } finally {
      button.removeAttribute("aria-busy");
      button.disabled = !byId("report-consent").checked;
    }
  }

  async function initDashboard() {
    attachSignOut();
    if (!(await bootstrapDemo())) return;
    renderDashboard(derivePortfolio(state.accountId));
    const consent = byId("report-consent");
    const reportButton = byId("request-report");
    consent.addEventListener("change", function () { reportButton.disabled = !consent.checked; });
    reportButton.addEventListener("click", generateReport);
    root.setInterval(async function () {
      try {
        await loadQuotes(true);
        renderDashboard(derivePortfolio(state.accountId));
      } catch (_error) {
        setStatus("error", "The scheduled public-mark check could not complete. Displayed timestamps remain authoritative.");
      }
    }, QUOTE_REFRESH_INTERVAL_MS);
  }

  function updateTradePreview() {
    const quantity = Number(byId("trade-quantity").value);
    const price = Number(byId("trade-price").value);
    const fees = Number(byId("trade-fees").value || 0);
    const multiplier = Number(byId("trade-multiplier").value || 1);
    const side = byId("trade-side").value;
    const node = byId("trade-preview") && byId("trade-preview").querySelector("strong");
    if (!node) return;
    if (!(quantity > 0) || !(price >= 0) || !(fees >= 0) || ![1, 100].includes(multiplier)) {
      node.textContent = "Enter quantity and price";
      return;
    }
    const signed = side === "sell" ? -quantity : quantity;
    const cashEffect = -(signed * price * multiplier) - fees;
    node.textContent = (cashEffect >= 0 ? "+" : "") + formatCurrency(cashEffect, "USD") + " scenario cash";
  }

  function setInstrumentDefaults() {
    const resolved = resolveInstrument(byId("trade-symbol").value);
    const multiplierControl = byId("trade-multiplier");
    const nameControl = byId("trade-name");
    if (!resolved) {
      if (multiplierControl.disabled) multiplierControl.value = "1";
      multiplierControl.disabled = false;
      if (nameControl.dataset.autoInstrumentName === "true") {
        nameControl.value = "";
        delete nameControl.dataset.autoInstrumentName;
      }
      byId("trade-asset-class").value = "Other";
      updateTradePreview();
      return;
    }
    const instrument = resolved.instrument;
    nameControl.value = instrument.name || instrument.symbol;
    nameControl.dataset.autoInstrumentName = "true";
    multiplierControl.value = String(Number(instrument.multiplier) === 100 ? 100 : 1);
    multiplierControl.disabled = true;
    const option = Array.from(byId("trade-asset-class").options).find(function (item) {
      return item.value === instrument.asset_class;
    });
    if (option) byId("trade-asset-class").value = option.value;
    updateTradePreview();
  }

  function populateInstrumentLists() {
    const tradeList = byId("instrument-list");
    const customList = byId("custom-instrument-list");
    if (!tradeList) return;
    const registered = new Set();
    const tradeSymbols = [];
    Object.entries(state.data.instruments || {}).forEach(function (entry) {
      registered.add(String(entry[0]).toUpperCase());
      registered.add(String(entry[1].symbol || "").toUpperCase());
      tradeSymbols.push(entry[1].symbol);
    });
    const customSymbols = [];
    (state.local.transactions || []).forEach(function (event) {
      const symbol = safeString(event.symbol || event.instrumentId, 48).toUpperCase();
      if (!symbol || registered.has(String(event.instrumentId || "").toUpperCase()) || registered.has(symbol)) return;
      if (!customSymbols.includes(symbol)) customSymbols.push(symbol);
    });
    tradeList.replaceChildren();
    tradeSymbols.concat(customSymbols).forEach(function (symbol) {
      const option = document.createElement("option");
      option.value = symbol;
      tradeList.append(option);
    });
    if (customList) {
      customList.replaceChildren();
      customSymbols.forEach(function (symbol) {
        const option = document.createElement("option");
        option.value = symbol;
        customList.append(option);
      });
    }
  }

  function renderEditor() {
    const view = derivePortfolio(state.accountId);
    state.view = view;
    setText("editor-title", view.portfolioName + " · local scenario");
    setText("operator-role", view.accountId + " public demo");
    setText("editor-nav", formatCurrency(view.nav, view.currency));
    setText("editor-cash", formatCurrency(view.cash, view.currency));
    setText("editor-modified", view.modifiedAt ? formatDate(view.modifiedAt, true) : "Published sample");
    setText("scenario-state", view.modifiedAt ? "Locally modified" : "Published sample");
    renderEventTable();
    renderMarkTable();
    populateInstrumentLists();
    setStatus("ready", state.storageAvailable
      ? "Local scenario ready. Registered instruments keep their automatic marks; nothing entered here is sent to a server."
      : "Browser storage is unavailable. Changes will last only until this page closes.");
  }

  function renderEventTable() {
    const body = byId("events-body");
    if (!body) return;
    body.replaceChildren();
    const events = (state.local.transactions || []).slice().reverse();
    if (!events.length) {
      const row = document.createElement("tr");
      const cell = textCell("No local scenario transactions.", "table-empty");
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
      return;
    }
    const reversedIds = new Set(events.map(function (event) { return event.reverseOf; }).filter(Boolean));
    events.forEach(function (event) {
      const row = document.createElement("tr");
      const activity = (event.signedQuantity >= 0 ? "Buy " : "Sell ") + formatNumber(Math.abs(event.signedQuantity), 6) + " @ " + formatCurrency(event.price, "USD");
      const actionCell = document.createElement("td");
      if (event.reverseOf) {
        actionCell.textContent = "Reversal";
      } else {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "table-action";
        button.textContent = reversedIds.has(event.id) ? "Reversed" : "Reverse";
        button.disabled = reversedIds.has(event.id);
        button.dataset.reverseEvent = event.id;
        actionCell.append(button);
      }
      row.append(
        textCell(formatDate(event.date, true)),
        textCell(event.symbol),
        textCell(activity),
        textCell(formatCurrency(event.cashEffect, "USD")),
        actionCell
      );
      body.append(row);
    });
  }

  function renderMarkTable() {
    const body = byId("marks-body");
    if (!body) return;
    body.replaceChildren();
    const entries = Object.entries(state.local.marks || {}).sort(function (a, b) { return a[0].localeCompare(b[0]); });
    if (!entries.length) {
      const row = document.createElement("tr");
      const cell = textCell("No local mark overrides.", "table-empty");
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
      return;
    }
    entries.forEach(function (entry) {
      const id = entry[0];
      const mark = entry[1];
      const resolved = resolveInstrument(id);
      const actionCell = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "table-action";
      button.textContent = "Remove";
      button.dataset.removeMark = id;
      actionCell.append(button);
      const row = document.createElement("tr");
      row.append(
        textCell(resolved ? resolved.instrument.symbol : id),
        textCell(formatCurrency(mark.price, "USD")),
        textCell(formatDate(mark.asOf, true)),
        textCell(mark.source || "Manual demo mark"),
        actionCell
      );
      body.append(row);
    });
  }

  function submitTrade(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setMessage("trade-message", "");
    if (!form.reportValidity()) return;
    const symbolInput = safeString(byId("trade-symbol").value, 48);
    const resolved = resolveInstrument(symbolInput);
    const instrumentId = resolved ? resolved.id : customInstrumentId(symbolInput);
    const quantity = Number(byId("trade-quantity").value);
    const price = Number(byId("trade-price").value);
    const fees = Number(byId("trade-fees").value || 0);
    const multiplier = Number(byId("trade-multiplier").value || 1);
    if (!(quantity > 0 && quantity <= 1000000) || !(price >= 0 && price <= 100000000) || !(fees >= 0 && fees <= 1000000) || ![1, 100].includes(multiplier)) {
      setMessage("trade-message", "Enter a valid quantity, price, fee, and multiplier.", "error");
      return;
    }
    const canonicalMultiplier = resolved ? (Number(resolved.instrument.multiplier) === 100 ? 100 : 1) : multiplier;
    if (resolved && multiplier !== canonicalMultiplier) {
      setMessage("trade-message", "This known instrument uses a fixed " + canonicalMultiplier + "x multiplier. Re-select the instrument and try again.", "error");
      return;
    }
    const signedQuantity = byId("trade-side").value === "sell" ? -quantity : quantity;
    const cashEffect = -(signedQuantity * price * multiplier) - fees;
    state.local.transactions.push({
      id: makeId(),
      instrumentId: instrumentId,
      symbol: resolved ? resolved.instrument.symbol : symbolInput.toUpperCase(),
      name: safeString(byId("trade-name").value || (resolved && resolved.instrument.name) || symbolInput, 100),
      assetClass: safeString(byId("trade-asset-class").value || "Other", 40),
      multiplier: multiplier,
      signedQuantity: signedQuantity,
      price: price,
      fees: fees,
      cashEffect: cashEffect,
      date: new Date(byId("trade-date").value).toISOString(),
      memo: safeString(byId("trade-memo").value, 300)
    });
    const stored = writeLocalState();
    form.reset();
    byId("trade-fees").value = "0";
    byId("trade-multiplier").value = "1";
    byId("trade-multiplier").disabled = false;
    delete byId("trade-name").dataset.autoInstrumentName;
    byId("trade-date").value = localDateTimeValue(new Date());
    updateTradePreview();
    renderEditor();
    const pricingNote = resolved && usesAutomaticMark(resolved.instrument)
      ? " Its registered mark will continue updating automatically."
      : " This custom instrument keeps its execution-price fallback until an administrator registers its quote metadata.";
    setMessage("trade-message", stored ? "Local scenario saved on this device." + pricingNote : "Scenario updated for this tab, but browser storage is unavailable.", stored ? "success" : "error");
  }

  function submitMark(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setMessage("mark-message", "");
    if (!form.reportValidity()) return;
    const resolved = resolveInstrument(byId("mark-symbol").value);
    if (!resolved) {
      setMessage("mark-message", "Choose an existing instrument before setting a mark.", "error");
      return;
    }
    if (usesAutomaticMark(resolved.instrument)) {
      setMessage("mark-message", "This registered instrument already updates automatically; no manual mark is needed.", "error");
      return;
    }
    const price = Number(byId("mark-price").value);
    if (!(price >= 0 && price <= 100000000)) {
      setMessage("mark-message", "Enter a valid non-negative mark.", "error");
      return;
    }
    state.local.marks[resolved.id] = {
      price: price,
      asOf: new Date(byId("mark-as-of").value).toISOString(),
      source: safeString(byId("mark-source").value || "Manual demo mark", 100)
    };
    const stored = writeLocalState();
    form.reset();
    byId("mark-as-of").value = localDateTimeValue(new Date());
    byId("mark-source").value = "Manual demo mark";
    renderEditor();
    setMessage("mark-message", stored ? "Manual mark saved on this device." : "Mark updated for this tab, but browser storage is unavailable.", stored ? "success" : "error");
  }

  function reverseEvent(eventId) {
    const original = state.local.transactions.find(function (event) { return event.id === eventId; });
    if (!original || state.local.transactions.some(function (event) { return event.reverseOf === eventId; })) return;
    state.local.transactions.push(Object.assign({}, original, {
      id: makeId(),
      signedQuantity: -Number(original.signedQuantity),
      fees: 0,
      cashEffect: -Number(original.cashEffect),
      date: new Date().toISOString(),
      memo: "Local reversal of " + original.id,
      reverseOf: original.id
    }));
    writeLocalState();
    renderEditor();
    setMessage("editor-message", "A reversing scenario event was added. The original entry remains visible.", "success");
  }

  function removeMark(instrumentId) {
    if (!state.local.marks[instrumentId]) return;
    delete state.local.marks[instrumentId];
    writeLocalState();
    renderEditor();
    setMessage("editor-message", "The local mark override was removed.", "success");
  }

  function downloadJson() {
    const payload = {
      demo: true,
      notice: "Public demonstration scenario. Not a trade file or official record.",
      account: state.accountId,
      exported_at: new Date().toISOString(),
      local_state: state.local,
      derived_snapshot: derivePortfolio(state.accountId)
    };
    const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "QSF-" + state.accountId + "-demo-scenario.json";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    root.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    setMessage("editor-message", "Scenario JSON downloaded. It contains public test data and local edits.", "success");
  }

  function resetLocalScenario() {
    const key = localStorageKey(state.accountId);
    try {
      localStorage.removeItem(key);
      if (localStorage.getItem(key) !== null) throw new Error("local_reset_not_persisted");
    } catch (_error) {
      state.storageAvailable = false;
      setMessage("editor-message", "This browser did not allow the saved local scenario to be removed. No in-memory edits were cleared.", "error");
      return;
    }
    state.local = emptyLocalState();
    state.storageAvailable = true;
    renderEditor();
    setMessage("editor-message", "Local edits cleared. The published sample is restored.", "success");
  }

  function attachEditorEvents() {
    byId("trade-form").addEventListener("submit", submitTrade);
    byId("mark-form").addEventListener("submit", submitMark);
    ["trade-quantity", "trade-price", "trade-fees", "trade-side", "trade-multiplier"].forEach(function (id) {
      byId(id).addEventListener("input", updateTradePreview);
    });
    byId("trade-symbol").addEventListener("input", setInstrumentDefaults);
    byId("trade-name").addEventListener("input", function () {
      delete byId("trade-name").dataset.autoInstrumentName;
    });
    byId("events-body").addEventListener("click", function (event) {
      const button = event.target.closest("[data-reverse-event]");
      if (button) reverseEvent(button.dataset.reverseEvent);
    });
    byId("marks-body").addEventListener("click", function (event) {
      const button = event.target.closest("[data-remove-mark]");
      if (button) removeMark(button.dataset.removeMark);
    });
    document.querySelectorAll("[data-local-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll("[data-local-tab]").forEach(function (item) { item.classList.toggle("is-active", item === button); });
        document.querySelectorAll("[data-local-panel]").forEach(function (panel) { panel.classList.toggle("is-hidden", panel.dataset.localPanel !== button.dataset.localTab); });
      });
    });
    byId("export-local-data").addEventListener("click", downloadJson);
    const dialog = byId("reset-dialog");
    byId("reset-local-data").addEventListener("click", function () {
      if (typeof dialog.showModal === "function") {
        dialog.returnValue = "";
        dialog.showModal();
      }
      else if (root.confirm("Reset this browser's local demo scenario?")) resetLocalScenario();
    });
    dialog.addEventListener("cancel", function () {
      dialog.returnValue = "cancel";
    });
    dialog.addEventListener("close", function () {
      if (dialog.returnValue === "confirm") resetLocalScenario();
    });
    document.querySelectorAll("[data-editor-form]").forEach(function (form) {
      form.removeAttribute("inert");
      form.removeAttribute("aria-busy");
      form.querySelectorAll("[data-editor-submit]").forEach(function (button) {
        button.disabled = false;
      });
    });
  }

  async function initAdmin() {
    attachSignOut();
    if (!(await bootstrapDemo())) return;
    byId("trade-date").value = localDateTimeValue(new Date());
    byId("mark-as-of").value = localDateTimeValue(new Date());
    renderEditor();
    updateTradePreview();
    attachEditorEvents();
    root.setInterval(async function () {
      try {
        await loadQuotes(true);
        renderEditor();
      } catch (_error) {
        setStatus("error", "The scheduled public-mark check could not complete. Displayed timestamps remain authoritative.");
      }
    }, QUOTE_REFRESH_INTERVAL_MS);
  }

  document.querySelectorAll("[data-current-year]").forEach(function (node) {
    node.textContent = String(new Date().getFullYear());
  });

  root.QSFDemoPortal = {
    deriveCurrent: function () { return state.view ? JSON.parse(JSON.stringify(state.view)) : null; },
    version: "1.1.0"
  };

  if (config.mode !== "public-demo") return;
  if (page === "login") initLogin().catch(function () {
    setStatus("error", "The public demo could not be loaded. Please refresh and try again.");
    setMessage("login-message", "Public test data is temporarily unavailable.", "error");
  });
  if (page === "dashboard") initDashboard().catch(function () {
    setStatus("error", "The public demo portfolio could not be loaded.");
  });
  if (page === "admin") initAdmin().catch(function () {
    setStatus("error", "The local scenario editor could not be loaded.");
  });
})(window);
