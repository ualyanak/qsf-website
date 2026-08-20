(function (root) {
  "use strict";

  const config = Object.assign({
    mode: "public-demo",
    demoDataUrl: "../data/demo-accounts.json",
    demoQuoteUrls: ["../data/demo-quotes.json"],
    demoHistoryUrls: ["../data/demo-portfolio-history.json"],
    demoSessionKey: "qsf.publicDemo.session.v1",
    demoStoragePrefix: "qsf.publicDemo.account."
  }, root.QSF_PORTAL_CONFIG || {});
  const QUOTE_REFRESH_INTERVAL_MS = 15 * 60 * 1000;
  const MAX_EXPECTED_QUOTE_AGE_MS = 4 * 24 * 60 * 60 * 1000;
  let chartResizeTimer = null;

  const comparisonOrder = ["spy", "gold-gld", "btc-usd"];
  const chartSeriesStyles = Object.freeze({
    qsf: { label: "QSF", color: "#15344f", width: 3.2, dash: "" },
    spy: { label: "SPY", color: "#3d6f8c", width: 2.2, dash: "" },
    "gold-gld": { label: "Gold", color: "#967638", width: 2.2, dash: "" },
    "btc-usd": { label: "Bitcoin", color: "#b45309", width: 2.2, dash: "7 5" }
  });

  const page = document.body && document.body.dataset.page;
  const colors = ["#15344f", "#c9a24f", "#3d6f8c", "#7b8793", "#967638", "#56816f", "#8b5f63", "#445467"];
  const state = {
    data: null,
    quotes: null,
    history: null,
    historyStatus: "idle",
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

  function normalizeHistoryPayload(payload) {
    if (!payload || payload.demo !== true || !payload.accounts || typeof payload.accounts !== "object") return null;
    const accounts = {};
    Object.entries(payload.accounts).forEach(function (entry) {
      const accountId = safeString(entry[0], 48).toLowerCase();
      const account = entry[1];
      const sourcePoints = Array.isArray(account)
        ? account
        : account && Array.isArray(account.points)
          ? account.points
          : account && Array.isArray(account.history)
            ? account.history
            : [];
      const points = sourcePoints.map(function (point) {
        const date = safeString(point && point.date, 10);
        const value = finite(point && (point.value != null ? point.value : point.nav));
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || value == null) return null;
        return {
          date: date,
          value: value,
          kind: safeString(point && point.kind || "nightly_close", 48),
          valuationAsOf: safeString(point && point.valuation_as_of, 40) || null,
          source: safeString(point && point.source || "Nightly published account value", 120),
          quality: safeString(point && point.quality || "historical_close", 48)
        };
      }).filter(Boolean);
      const comparisons = (account && Array.isArray(account.comparisons) ? account.comparisons : []).map(function (comparison) {
        const id = safeString(comparison && comparison.id, 32).toLowerCase();
        if (!comparisonOrder.includes(id)) return null;
        const comparisonPoints = (comparison && Array.isArray(comparison.points) ? comparison.points : []).map(function (point) {
          const date = safeString(point && point.date, 10);
          const value = finite(point && (point.value != null ? point.value : point.normalized_value));
          if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || value == null) return null;
          return {
            date: date,
            value: value,
            kind: safeString(point && point.kind || "nightly_benchmark", 48),
            sourceDate: safeString(point && (point.source_date || point.sourceDate), 10) || date,
            quality: safeString(point && point.quality || "historical_close", 48)
          };
        }).filter(Boolean);
        return {
          id: id,
          label: safeString(comparison && comparison.label, 64) || chartSeriesStyles[id].label,
          source: safeString(comparison && comparison.source, 160) || "Public benchmark closing prices",
          baselineDate: safeString(comparison && (comparison.baseline_date || comparison.baselineDate), 10) || null,
          points: comparisonPoints
        };
      }).filter(Boolean);
      if (accountId) accounts[accountId] = { points: points, comparisons: comparisons };
    });
    return {
      schema_version: Number(payload.schema_version || 1),
      demo: true,
      generated_at: safeString(payload.generated_at, 40) || null,
      accounts: accounts
    };
  }

  async function loadHistory(force) {
    if (state.history && !force) return state.history;
    state.historyStatus = "loading";
    const urls = Array.isArray(config.demoHistoryUrls) ? config.demoHistoryUrls : ["../data/demo-portfolio-history.json"];
    const candidates = state.history ? [state.history] : [];
    for (const url of urls) {
      try {
        const normalized = normalizeHistoryPayload(await fetchJson(url));
        if (normalized) candidates.push(normalized);
      } catch (_error) {
        /* Try the packaged fallback next, or retain the previous snapshot. */
      }
    }
    if (!candidates.length) {
      state.history = { schema_version: 1, demo: true, generated_at: null, accounts: {} };
      state.historyStatus = "formation-fallback";
      return state.history;
    }

    const ordered = candidates.slice().sort(function (left, right) {
      return timestampRank(left.generated_at) - timestampRank(right.generated_at);
    });
    const merged = {};
    ordered.forEach(function (snapshot) {
      Object.entries(snapshot.accounts || {}).forEach(function (entry) {
        const accountId = entry[0];
        const points = entry[1] && Array.isArray(entry[1].points) ? entry[1].points : [];
        const comparisons = entry[1] && Array.isArray(entry[1].comparisons) ? entry[1].comparisons : [];
        if (!merged[accountId]) merged[accountId] = { points: new Map(), comparisons: new Map() };
        points.forEach(function (point) { merged[accountId].points.set(point.date, point); });
        comparisons.forEach(function (comparison) {
          const previous = merged[accountId].comparisons.get(comparison.id);
          const comparisonPoints = previous ? previous.points : new Map();
          (comparison.points || []).forEach(function (point) { comparisonPoints.set(point.date, point); });
          merged[accountId].comparisons.set(comparison.id, Object.assign({}, previous || {}, comparison, { points: comparisonPoints }));
        });
      });
    });
    const accounts = {};
    Object.entries(merged).forEach(function (entry) {
      const comparisons = Array.from(entry[1].comparisons.values()).map(function (comparison) {
        return Object.assign({}, comparison, {
          points: Array.from(comparison.points.values()).sort(function (a, b) { return a.date.localeCompare(b.date); })
        });
      }).sort(function (a, b) { return comparisonOrder.indexOf(a.id) - comparisonOrder.indexOf(b.id); });
      accounts[entry[0]] = {
        points: Array.from(entry[1].points.values()).sort(function (a, b) { return a.date.localeCompare(b.date); }),
        comparisons: comparisons
      };
    });
    const newest = ordered[ordered.length - 1];
    state.history = {
      schema_version: 1,
      demo: true,
      generated_at: newest.generated_at,
      accounts: accounts
    };
    state.historyStatus = Object.values(accounts).some(function (account) { return account.points.length > 0; })
      ? "ready"
      : "formation-fallback";
    return state.history;
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
        cashEquivalent: instrument.cash_equivalent === true,
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
    const cashEquivalentMarketValue = holdings.reduce(function (sum, holding) {
      return sum + (holding.cashEquivalent ? holding.marketValue : 0);
    }, 0);
    const cashAndCashEquivalents = cash + cashEquivalentMarketValue;
    const nav = cash + positionsValue;
    const openingNav = Number(account.opening_nav);
    const returnPct = openingNav ? (nav / openingNav - 1) * 100 : null;
    const grossGroups = new Map();
    holdings.forEach(function (holding) {
      const group = holding.cashEquivalent ? "Cash & Cash Equivalents" : holding.assetClass;
      grossGroups.set(group, (grossGroups.get(group) || 0) + Math.abs(holding.marketValue));
    });
    if (cash > 0) grossGroups.set("Cash & Cash Equivalents", (grossGroups.get("Cash & Cash Equivalents") || 0) + cash);
    const allocationTotal = Array.from(grossGroups.values()).reduce(function (sum, value) { return sum + value; }, 0);
    const allocation = Array.from(grossGroups.entries()).map(function (entry) {
      return { name: entry[0], value: entry[1], percent: allocationTotal ? entry[1] / allocationTotal * 100 : 0 };
    }).sort(function (a, b) { return b.value - a.value; });

    const today = todayKey();
    const historyByDate = new Map();
    (account.history || []).forEach(function (point) {
      if (point && /^\d{4}-\d{2}-\d{2}$/.test(point.date) && point.date <= today && finite(point.value) != null) {
        historyByDate.set(point.date, { date: point.date, value: Number(point.value), kind: point.kind || "published_test" });
      }
    });
    const publishedHistory = state.history && state.history.accounts && state.history.accounts[accountId];
    (publishedHistory && publishedHistory.points || []).forEach(function (point) {
      if (point && point.date <= today && finite(point.value) != null) historyByDate.set(point.date, point);
    });
    const history = Array.from(historyByDate.values()).sort(function (a, b) { return a.date.localeCompare(b.date); });
    const historyDates = new Set(history.map(function (point) { return point.date; }));
    const qsfStyle = chartSeriesStyles.qsf;
    const comparisonSeries = history.length ? [{
      id: "qsf",
      label: qsfStyle.label,
      source: "QSF public demonstration nightly account value",
      points: history.map(function (point) {
        return {
          date: point.date,
          value: Number(point.value),
          kind: point.kind || "nightly_close",
          sourceDate: point.sourceDate || point.source_date || point.date,
          quality: point.quality || "historical_close"
        };
      })
    }] : [];
    (publishedHistory && publishedHistory.comparisons || []).forEach(function (comparison) {
      if (!comparisonOrder.includes(comparison.id)) return;
      const benchmarkPoints = (comparison.points || []).filter(function (point) {
        return historyDates.has(point.date) && point.date <= today && finite(point.value) != null;
      }).map(function (point) {
        return {
          date: point.date,
          value: Number(point.value),
          kind: point.kind || "nightly_benchmark",
          sourceDate: point.sourceDate || point.source_date || point.date,
          quality: point.quality || "historical_close"
        };
      }).sort(function (a, b) { return a.date.localeCompare(b.date); });
      if (benchmarkPoints.length < 2) return;
      comparisonSeries.push({
        id: comparison.id,
        label: chartSeriesStyles[comparison.id].label,
        source: comparison.source,
        baselineDate: comparison.baselineDate,
        points: benchmarkPoints
      });
    });
    comparisonSeries.sort(function (a, b) {
      const left = a.id === "qsf" ? -1 : comparisonOrder.indexOf(a.id);
      const right = b.id === "qsf" ? -1 : comparisonOrder.indexOf(b.id);
      return left - right;
    });
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
      cashEquivalentMarketValue: cashEquivalentMarketValue,
      cashAndCashEquivalents: cashAndCashEquivalents,
      nav: nav,
      returnPct: returnPct,
      positionsValue: positionsValue,
      holdings: holdings,
      allocation: allocation,
      history: history,
      comparisonSeries: comparisonSeries,
      historySnapshotGeneratedAt: state.history && state.history.generated_at,
      historyStatus: state.historyStatus,
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

  function chartTickIndexes(length, maximum) {
    if (length <= 0 || maximum <= 0) return [];
    const count = Math.min(length, maximum);
    if (count === 1) return [0];
    const indexes = [];
    for (let tick = 0; tick < count; tick += 1) {
      const index = Math.round(tick * (length - 1) / (count - 1));
      if (indexes[indexes.length - 1] !== index) indexes.push(index);
    }
    return indexes;
  }

  function niceChartStep(value) {
    if (!(value > 0) || !Number.isFinite(value)) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(value)));
    const scaled = value / power;
    const factors = [1, 2, 2.5, 5, 10];
    const factor = factors.find(function (candidate) { return candidate >= scaled - 1e-10; }) || 10;
    return factor * power;
  }

  function chartAxis(values) {
    const dataMin = Math.min.apply(null, values);
    const dataMax = Math.max.apply(null, values);
    const targetMin = Math.min(9000, dataMin);
    const targetMax = Math.max(9000, dataMax);
    const intervalCount = 5;
    let step = niceChartStep((targetMax - targetMin) / intervalCount || Math.max(1, Math.abs(targetMax) * 0.01));
    let min = dataMin >= 9000 ? 9000 : Math.floor(dataMin / step) * step;
    let max = min + intervalCount * step;
    while (max < targetMax - 1e-8) {
      step = niceChartStep(step * 1.000001);
      min = dataMin >= 9000 ? 9000 : Math.floor(dataMin / step) * step;
      max = min + intervalCount * step;
    }
    return { min: min, max: max, step: step, intervalCount: intervalCount };
  }

  function formatChartDate(date, includeYear) {
    const label = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(date);
    return includeYear ? label + " ’" + String(date.getFullYear()).slice(-2) : label;
  }

  function svgNode(name, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attributes || {}).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    return node;
  }

  function renderChartLegend(chartSeries, currency, openingValue) {
    const legend = byId("performance-legend");
    if (!legend) return;
    legend.replaceChildren();
    chartSeries.forEach(function (series) {
      const style = chartSeriesStyles[series.id];
      const latest = series.points[series.points.length - 1];
      if (!style || !latest) return;
      const item = document.createElement("div");
      item.className = "performance-legend-item";
      item.setAttribute("role", "listitem");
      item.dataset.seriesId = series.id;
      const swatch = document.createElement("span");
      swatch.className = "performance-legend-swatch" + (style.dash ? " is-dashed" : "");
      swatch.style.setProperty("--series-color", style.color);
      swatch.setAttribute("aria-hidden", "true");
      const name = document.createElement("span");
      name.className = "performance-legend-name";
      name.textContent = style.label;
      const value = document.createElement("strong");
      value.className = "performance-legend-value";
      value.textContent = formatCurrency(latest.value, currency);
      const change = openingValue ? (latest.value / openingValue - 1) * 100 : null;
      const changeText = document.createElement("span");
      changeText.className = "performance-legend-change";
      changeText.textContent = change == null
        ? "Change unavailable"
        : formatPercent(change) + " since " + formatCurrency(openingValue, currency, false);
      item.setAttribute("aria-label", style.label + ", latest nightly value " + value.textContent + ", " + changeText.textContent + ".");
      item.append(swatch, name, value, changeText);
      legend.append(item);
    });
  }

  function renderChart(comparisonSeries, currency, openingValue) {
    const shell = byId("performance-chart");
    if (!shell || !Array.isArray(comparisonSeries)) return;
    const chartSeries = comparisonSeries.map(function (series) {
      if (!series || !chartSeriesStyles[series.id] || !Array.isArray(series.points)) return null;
      const pointMap = new Map();
      series.points.forEach(function (item) {
        const date = parseDate(item && item.date);
        const value = Number(item && item.value);
        if (date && Number.isFinite(value)) {
          pointMap.set(String(item.date).slice(0, 10), {
            date: date,
            dateKey: String(item.date).slice(0, 10),
            value: value,
            kind: item.kind || "nightly_close",
            quality: item.quality || "historical_close"
          });
        }
      });
      const points = Array.from(pointMap.values()).sort(function (left, right) { return left.date - right.date; });
      return points.length ? { id: series.id, label: chartSeriesStyles[series.id].label, points: points } : null;
    }).filter(Boolean);
    const qsfSeries = chartSeries.find(function (series) { return series.id === "qsf"; });
    const points = qsfSeries ? qsfSeries.points : [];
    const baselineValue = finite(openingValue) || (points[0] && points[0].value) || 9900;
    renderChartLegend(chartSeries, currency, baselineValue);
    if (points.length < 2) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const title = document.createElement("strong");
      title.textContent = "Nightly comparison is not available yet";
      const note = document.createElement("span");
      note.textContent = "QSF and public benchmark values will appear after completed end-of-day history is available.";
      empty.append(title, note);
      shell.replaceChildren(empty);
      shell.setAttribute("aria-label", "The nightly QSF and public benchmark comparison is not yet available for this demonstration portfolio.");
      setText("chart-start", "Formation baseline pending");
      setText("chart-end", points.length ? "Latest night " + formatDate(points[0].date) : "Latest night —");
      return;
    }
    const box = shell.getBoundingClientRect();
    const width = Math.max(320, Math.round(box.width || shell.clientWidth || 900));
    const height = Math.max(240, Math.round(box.height || shell.clientHeight || (width < 520 ? 250 : 310)));
    const mobile = width < 520;
    const pad = mobile
      ? { top: 26, right: 10, bottom: 42, left: 56 }
      : { top: 28, right: 16, bottom: 44, left: 66 };
    const axisValues = [];
    chartSeries.forEach(function (series) {
      series.points.forEach(function (point) { axisValues.push(point.value); });
    });
    const axis = chartAxis(axisValues);
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const firstTime = points[0].date.getTime();
    const lastTime = points[points.length - 1].date.getTime();
    const timeSpan = Math.max(1, lastTime - firstTime);
    function x(index) { return pad.left + (points[index].date.getTime() - firstTime) / timeSpan * plotWidth; }
    function xDate(date) { return pad.left + (date.getTime() - firstTime) / timeSpan * plotWidth; }
    function y(value) { return pad.top + (axis.max - value) / (axis.max - axis.min) * plotHeight; }
    const svg = svgNode("svg", {
      viewBox: "0 0 " + width + " " + height,
      preserveAspectRatio: "xMidYMid meet",
      "aria-hidden": "true",
      focusable: "false"
    });
    svg.append(svgNode("rect", { x: "0", y: "0", width: width, height: height, fill: "#ffffff" }));
    const clipId = "qsf-performance-plot";
    const defs = svgNode("defs");
    const clipPath = svgNode("clipPath", { id: clipId });
    clipPath.append(svgNode("rect", { x: pad.left, y: pad.top, width: plotWidth, height: plotHeight }));
    defs.append(clipPath);
    svg.append(defs);

    const axisFont = "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    const yLabels = [];
    for (let tick = 0; tick <= axis.intervalCount; tick += 1) {
      const value = axis.min + tick * axis.step;
      const gy = y(value);
      svg.append(svgNode("line", {
        x1: pad.left,
        x2: width - pad.right,
        y1: gy,
        y2: gy,
        stroke: tick === 0 ? "#aeb9c3" : "#d7dee5",
        "stroke-width": tick === 0 ? "1" : ".8",
        "vector-effect": "non-scaling-stroke",
        "data-axis-grid": "y"
      }));
      const labelValue = formatCurrency(value, currency, false);
      yLabels.push(labelValue);
      const label = svgNode("text", {
        x: pad.left - 8,
        y: gy + 3.5,
        "text-anchor": "end",
        fill: "#5f6f7e",
        "font-family": axisFont,
        "font-size": mobile ? "9.5" : "10.5",
        "font-variant-numeric": "tabular-nums",
        "data-axis-label": "y"
      });
      label.textContent = labelValue;
      svg.append(label);
    }
    svg.append(svgNode("line", {
      x1: pad.left,
      x2: pad.left,
      y1: pad.top,
      y2: height - pad.bottom,
      stroke: "#8e9dab",
      "stroke-width": "1",
      "vector-effect": "non-scaling-stroke",
      "data-axis-line": "y"
    }));

    const includeYear = points[0].date.getFullYear() !== points[points.length - 1].date.getFullYear();
    const xIndexes = chartTickIndexes(points.length, mobile ? 6 : 7);
    xIndexes.forEach(function (index, tick) {
      const gx = x(index);
      svg.append(svgNode("line", {
        x1: gx,
        x2: gx,
        y1: height - pad.bottom,
        y2: height - pad.bottom + 5,
        stroke: "#8e9dab",
        "stroke-width": "1",
        "vector-effect": "non-scaling-stroke"
      }));
      const label = svgNode("text", {
        x: gx,
        y: height - 14,
        "text-anchor": tick === 0 ? "start" : tick === xIndexes.length - 1 ? "end" : "middle",
        fill: "#5f6f7e",
        "font-family": axisFont,
        "font-size": mobile ? "9.5" : "10.5",
        "data-axis-label": "x"
      });
      label.textContent = formatChartDate(points[index].date, includeYear);
      svg.append(label);
    });

    const unit = svgNode("text", {
      x: pad.left,
      y: 15,
      fill: "#415464",
      "font-family": axisFont,
      "font-size": mobile ? "9.5" : "10.5",
      "font-weight": "650",
      "letter-spacing": ".04em",
      "data-axis-title": "y"
    });
    unit.textContent = "GROWTH OF " + formatCurrency(baselineValue, currency, false) + " (" + (currency || "USD") + ")";
    svg.append(unit);

    const drawOrder = chartSeries.slice().sort(function (a, b) {
      return a.id === "qsf" ? 1 : b.id === "qsf" ? -1 : comparisonOrder.indexOf(a.id) - comparisonOrder.indexOf(b.id);
    });
    drawOrder.forEach(function (series) {
      const style = chartSeriesStyles[series.id];
      const linePath = series.points.map(function (point, index) {
        return (index ? "L" : "M") + xDate(point.date).toFixed(2) + " " + y(point.value).toFixed(2);
      }).join(" ");
      const line = svgNode("path", {
        "clip-path": "url(#" + clipId + ")",
        "data-chart-series": series.id,
        "data-series-id": series.id
      });
      line.setAttribute("d", linePath);
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", style.color);
      line.setAttribute("stroke-width", style.width);
      if (style.dash) line.setAttribute("stroke-dasharray", style.dash);
      line.setAttribute("vector-effect", "non-scaling-stroke");
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("stroke-linejoin", "round");
      svg.append(line);
    });
    drawOrder.forEach(function (series) {
      const style = chartSeriesStyles[series.id];
      const latestPoint = series.points[series.points.length - 1];
      const marker = svgNode("circle", {
        "data-chart-latest": series.id,
        "data-series-id": series.id
      });
      marker.setAttribute("cx", xDate(latestPoint.date));
      marker.setAttribute("cy", y(latestPoint.value));
      marker.setAttribute("r", series.id === "qsf" ? "5" : "4");
      marker.setAttribute("fill", style.color);
      marker.setAttribute("stroke", "#fff");
      marker.setAttribute("stroke-width", "2.5");
      marker.setAttribute("vector-effect", "non-scaling-stroke");
      svg.append(marker);
    });
    shell.replaceChildren(svg);
    const latestSummaries = chartSeries.map(function (series) {
      const latestPoint = series.points[series.points.length - 1];
      return chartSeriesStyles[series.id].label + " " + formatCurrency(latestPoint.value, currency);
    });
    shell.setAttribute("aria-label", "Growth of " + formatCurrency(baselineValue, currency, false) + " from " + formatDate(points[0].date) + " through " + formatDate(points[points.length - 1].date) + ", comparing QSF with available public benchmarks. The vertical axis runs from " + yLabels[0] + " to " + yLabels[yLabels.length - 1] + " in five intervals. Latest nightly values: " + latestSummaries.join(", ") + ".");
    setText("chart-start", "All series = " + formatCurrency(baselineValue, currency, false) + " · " + formatDate(points[0].date));
    setText("chart-end", "Latest night " + formatDate(points[points.length - 1].date));
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
    setText("metric-cash", formatCurrency(view.cashAndCashEquivalents, view.currency));
    setText("metric-quote-age", view.latestMarkAsOf ? formatDate(view.latestMarkAsOf, true) : "Fallback marks");
    setText("portfolio-as-of", "Calculated " + formatDate(view.generatedAt, true) + (view.staleCount
      ? " from the latest available automatic, manual, and fallback marks."
      : " from delayed public marks and automatic option model estimates."));
    setText("scenario-state", view.modifiedAt ? "Locally modified" : "Published sample");
    renderChart(view.comparisonSeries, view.currency, view.openingNav);
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
    await Promise.all([loadData(), loadQuotes(), loadHistory()]);
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
    setMessage("report-message", "Refreshing public marks and nightly history before building the demonstration PDF…");
    try {
      await Promise.all([loadQuotes(true), loadHistory(true)]);
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
    root.addEventListener("resize", function () {
      root.clearTimeout(chartResizeTimer);
      chartResizeTimer = root.setTimeout(function () {
        if (state.view) renderChart(state.view.comparisonSeries, state.view.currency, state.view.openingNav);
      }, 120);
    });
    root.setInterval(async function () {
      try {
        await Promise.all([loadQuotes(true), loadHistory(true)]);
        renderDashboard(derivePortfolio(state.accountId));
      } catch (_error) {
        setStatus("error", "The scheduled public-data check could not complete. Displayed timestamps remain authoritative.");
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
    node.textContent = (cashEffect >= 0 ? "+" : "") + formatCurrency(cashEffect, "USD") + " cash balance impact";
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
    setText("editor-cash", formatCurrency(view.cashAndCashEquivalents, view.currency));
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
        await Promise.all([loadQuotes(true), loadHistory(true)]);
        renderEditor();
      } catch (_error) {
        setStatus("error", "The scheduled public-data check could not complete. Displayed timestamps remain authoritative.");
      }
    }, QUOTE_REFRESH_INTERVAL_MS);
  }

  document.querySelectorAll("[data-current-year]").forEach(function (node) {
    node.textContent = String(new Date().getFullYear());
  });

  root.QSFDemoPortal = {
    deriveCurrent: function () { return state.view ? JSON.parse(JSON.stringify(state.view)) : null; },
    version: "1.2.0"
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
