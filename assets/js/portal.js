(function () {
  "use strict";

  const config = Object.assign({
    enabled: false,
    supabaseUrl: "",
    supabaseAnonKey: "",
    reportDownloadFunction: "",
    sessionStorageKey: "qsf.portal.session.v1"
  }, window.QSF_PORTAL_CONFIG || {});

  const page = document.body && document.body.dataset.page;
  const state = {
    session: null,
    user: null,
    profile: null,
    memberships: [],
    portfolio: null,
    latestNav: null,
    portfolioLoadVersion: 0
  };

  const currencyFormatterCache = new Map();
  const allocationColors = ["#15344f", "#c9a24f", "#3d6f8c", "#7b8793", "#967638", "#56816f", "#8b5f63", "#445467"];

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(target, value) {
    const node = typeof target === "string" ? byId(target) : target;
    if (node) node.textContent = value == null ? "" : String(value);
  }

  function setMessage(id, text, type) {
    const node = byId(id);
    if (!node) return;
    node.textContent = text || "";
    node.classList.toggle("is-error", type === "error");
    node.classList.toggle("is-success", type === "success");
  }

  function setStatus(kind, text) {
    const node = byId("portal-status");
    if (!node) return;
    node.classList.remove("is-pending", "is-ready", "is-error");
    node.classList.add(kind === "ready" ? "is-ready" : kind === "error" ? "is-error" : "is-pending");
    setText(node.querySelector("[data-status-text]"), text);
  }

  function setBusy(form, busy) {
    if (!form) return;
    form.setAttribute("aria-busy", busy ? "true" : "false");
    form.querySelectorAll("button, input, select, textarea").forEach(function (control) {
      if (busy) {
        control.dataset.wasDisabled = control.disabled ? "true" : "false";
        control.disabled = true;
      } else if (control.dataset.wasDisabled !== "true") {
        control.disabled = false;
      }
      if (!busy) delete control.dataset.wasDisabled;
    });
  }

  function configured() {
    if (config.enabled !== true) return false;
    if (typeof config.supabaseUrl !== "string" || typeof config.supabaseAnonKey !== "string") return false;
    if (config.supabaseAnonKey.trim().length < 20) return false;
    try {
      const url = new URL(config.supabaseUrl);
      const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
      return (url.protocol === "https:" || (local && url.protocol === "http:")) && !url.username && !url.password;
    } catch (_error) {
      return false;
    }
  }

  function baseUrl() {
    return config.supabaseUrl.replace(/\/+$/, "");
  }

  async function fetchJson(url, options) {
    const controller = new AbortController();
    const timer = window.setTimeout(function () { controller.abort(); }, 15000);
    try {
      const response = await fetch(url, Object.assign({}, options, { signal: controller.signal }));
      let payload = null;
      const body = await response.text();
      if (body) {
        try { payload = JSON.parse(body); } catch (_error) { payload = null; }
      }
      if (!response.ok) throw new Error("request_failed");
      return payload;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function authHeaders(accessToken) {
    const headers = {
      "apikey": config.supabaseAnonKey,
      "Content-Type": "application/json"
    };
    if (accessToken) headers.Authorization = "Bearer " + accessToken;
    return headers;
  }

  async function authRequest(path, body, accessToken) {
    return fetchJson(baseUrl() + "/auth/v1/" + path, {
      method: "POST",
      headers: authHeaders(accessToken),
      body: body == null ? undefined : JSON.stringify(body)
    });
  }

  function saveSession(session) {
    if (!session || typeof session.access_token !== "string" || typeof session.refresh_token !== "string") {
      throw new Error("invalid_session");
    }
    const normalized = {
      access_token: session.access_token,
      refresh_token: session.refresh_token,
      expires_at: Number(session.expires_at) || Math.floor(Date.now() / 1000) + Number(session.expires_in || 3600)
    };
    window.sessionStorage.setItem(config.sessionStorageKey, JSON.stringify(normalized));
    state.session = normalized;
    return normalized;
  }

  function clearSession() {
    try { window.sessionStorage.removeItem(config.sessionStorageKey); } catch (_error) { /* storage may be unavailable */ }
    state.session = null;
    state.user = null;
    state.profile = null;
  }

  function readSession() {
    try {
      const value = JSON.parse(window.sessionStorage.getItem(config.sessionStorageKey) || "null");
      if (!value || typeof value.access_token !== "string" || typeof value.refresh_token !== "string") return null;
      return value;
    } catch (_error) {
      return null;
    }
  }

  async function currentSession() {
    let session = state.session || readSession();
    if (!session) return null;
    if (Number(session.expires_at || 0) <= Math.floor(Date.now() / 1000) + 60) {
      try {
        session = saveSession(await authRequest("token?grant_type=refresh_token", { refresh_token: session.refresh_token }));
      } catch (_error) {
        clearSession();
        return null;
      }
    }
    state.session = session;
    return session;
  }

  async function loadUser(session) {
    const user = await fetchJson(baseUrl() + "/auth/v1/user", {
      method: "GET",
      headers: authHeaders(session.access_token)
    });
    if (!user || typeof user.id !== "string") throw new Error("invalid_user");
    state.user = user;
    return user;
  }

  function buildQuery(parameters) {
    const search = new URLSearchParams();
    Object.keys(parameters || {}).forEach(function (key) {
      const value = parameters[key];
      if (value !== undefined && value !== null && value !== "") search.append(key, String(value));
    });
    const query = search.toString();
    return query ? "?" + query : "";
  }

  async function restRequest(resource, parameters, options) {
    const session = await currentSession();
    if (!session) throw new Error("not_authenticated");
    const settings = Object.assign({ method: "GET", body: null, prefer: "" }, options || {});
    const headers = authHeaders(session.access_token);
    headers.Accept = "application/json";
    if (settings.prefer) headers.Prefer = settings.prefer;
    return fetchJson(baseUrl() + "/rest/v1/" + resource + buildQuery(parameters), {
      method: settings.method,
      headers: headers,
      body: settings.body == null ? undefined : JSON.stringify(settings.body)
    });
  }

  function enableAuthControls() {
    document.querySelectorAll("[data-auth-control]").forEach(function (control) { control.disabled = false; });
  }

  function routeToLogin() {
    const target = page === "admin" ? "../investor_login/" : "./index.html";
    window.location.replace(target);
  }

  function formatCurrency(value, currency) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const code = /^[A-Z]{3}$/.test(currency || "") ? currency : "USD";
    if (!currencyFormatterCache.has(code)) {
      currencyFormatterCache.set(code, new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: code,
        maximumFractionDigits: Math.abs(number) < 100 ? 2 : 0
      }));
    }
    return currencyFormatterCache.get(code).format(number);
  }

  function formatNumber(value, maximumFractionDigits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return new Intl.NumberFormat("en-US", { maximumFractionDigits: maximumFractionDigits == null ? 4 : maximumFractionDigits }).format(number);
  }

  function formatPercent(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    return new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number / 100);
  }

  function formatDate(value, withTime) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const options = withTime
      ? { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }
      : { month: "short", day: "numeric", year: "numeric" };
    return new Intl.DateTimeFormat("en-US", options).format(date);
  }

  function textCell(value, className) {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = value;
    return cell;
  }

  async function initLogin() {
    const loginForm = byId("login-form");

    if (!configured()) {
      setStatus("error", "Portal activation pending — secure sign in is not yet connected.");
      setMessage("login-message", "Investor access is not active yet. Please contact the fund administrator.", "error");
      return;
    }

    enableAuthControls();
    setStatus("ready", "Secure sign in is available.");

    const existing = await currentSession();
    if (existing) {
      try {
        await loadUser(existing);
        window.location.replace("./dashboard.html");
        return;
      } catch (_error) {
        clearSession();
      }
    }

    loginForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      setMessage("login-message", "");
      if (!loginForm.reportValidity()) return;
      setBusy(loginForm, true);
      try {
        const response = await authRequest("token?grant_type=password", {
          email: byId("login-email").value.trim(),
          password: byId("login-password").value
        });
        saveSession(response);
        window.location.assign("./dashboard.html");
      } catch (_error) {
        clearSession();
        setMessage("login-message", "We could not sign you in. Check your details and try again.", "error");
        setBusy(loginForm, false);
      }
    });

  }

  async function bootstrapProtectedPage() {
    if (!configured()) {
      setStatus("error", "Portal activation pending — live account data is not connected.");
      return false;
    }
    const session = await currentSession();
    if (!session) {
      routeToLogin();
      return false;
    }
    try {
      await loadUser(session);
      return true;
    } catch (_error) {
      clearSession();
      routeToLogin();
      return false;
    }
  }

  async function loadProfile() {
    const profiles = await restRequest("profiles", {
      select: "id,display_name,role",
      id: "eq." + state.user.id,
      limit: 1
    });
    state.profile = Array.isArray(profiles) && profiles.length ? profiles[0] : null;
    return state.profile;
  }

  async function loadMemberships() {
    const rows = await restRequest("portfolio_members", {
      select: "role,portfolios(id,code,name,base_currency,inception_date)",
      user_id: "eq." + state.user.id,
      order: "created_at.asc"
    });
    state.memberships = (Array.isArray(rows) ? rows : []).map(function (row) {
      const portfolio = Array.isArray(row.portfolios) ? row.portfolios[0] : row.portfolios;
      return portfolio ? { role: row.role, portfolio: portfolio } : null;
    }).filter(Boolean);
    return state.memberships;
  }

  async function signOut() {
    const session = await currentSession();
    try {
      if (session) await authRequest("logout", null, session.access_token);
    } catch (_error) {
      /* Local session removal still prevents reuse in this browser tab. */
    }
    clearSession();
    routeToLogin();
  }

  function attachSignOut() {
    const button = byId("sign-out");
    if (button) button.addEventListener("click", signOut);
  }

  function renderChart(history, currency) {
    const shell = byId("performance-chart");
    if (!shell || !Array.isArray(history) || history.length < 2) return;
    const points = history.map(function (item) {
      return { date: new Date(item.nav_date), value: Number(item.aum) };
    }).filter(function (item) {
      return !Number.isNaN(item.date.getTime()) && Number.isFinite(item.value);
    });
    if (points.length < 2) return;

    const width = 900;
    const height = 310;
    const padding = { top: 24, right: 24, bottom: 28, left: 24 };
    const values = points.map(function (item) { return item.value; });
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (min === max) { min *= 0.98; max *= 1.02; }
    const spread = max - min || 1;
    min -= spread * 0.08;
    max += spread * 0.08;

    function x(index) { return padding.left + index * ((width - padding.left - padding.right) / (points.length - 1)); }
    function y(value) { return padding.top + (max - value) * ((height - padding.top - padding.bottom) / (max - min)); }

    const lineData = points.map(function (item, index) { return (index ? "L" : "M") + x(index).toFixed(2) + " " + y(item.value).toFixed(2); }).join(" ");
    const areaData = lineData + " L" + x(points.length - 1).toFixed(2) + " " + (height - padding.bottom) + " L" + x(0).toFixed(2) + " " + (height - padding.bottom) + " Z";
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const area = document.createElementNS(ns, "path");
    area.setAttribute("d", areaData);
    area.setAttribute("fill", "rgba(201, 162, 79, .16)");
    const line = document.createElementNS(ns, "path");
    line.setAttribute("d", lineData);
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", "#15344f");
    line.setAttribute("stroke-width", "3");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("stroke-linejoin", "round");
    const finalDot = document.createElementNS(ns, "circle");
    finalDot.setAttribute("cx", x(points.length - 1));
    finalDot.setAttribute("cy", y(points[points.length - 1].value));
    finalDot.setAttribute("r", "5");
    finalDot.setAttribute("fill", "#c9a24f");
    finalDot.setAttribute("stroke", "#ffffff");
    finalDot.setAttribute("stroke-width", "3");
    svg.append(area, line, finalDot);
    shell.replaceChildren(svg);
    shell.setAttribute("aria-label", "Account value from " + formatDate(points[0].date) + " to " + formatDate(points[points.length - 1].date) + ", ending at " + formatCurrency(points[points.length - 1].value, currency));
    setText("chart-start", "Start " + formatDate(points[0].date));
    setText("chart-end", "Latest " + formatCurrency(points[points.length - 1].value, currency));
  }

  function renderAllocation(holdings, cashValue, currency) {
    const legend = byId("allocation-legend");
    const ring = document.querySelector(".allocation-ring");
    if (!legend || !ring) return;
    const groups = new Map();
    holdings.forEach(function (holding) {
      const category = holding.assetClass || "other";
      const exposure = Math.abs(Number(holding.marketValue));
      if (Number.isFinite(exposure) && exposure > 0) groups.set(category, (groups.get(category) || 0) + exposure);
    });
    if (Number(cashValue) > 0) groups.set("cash", (groups.get("cash") || 0) + Number(cashValue));
    const items = Array.from(groups.entries()).sort(function (a, b) { return b[1] - a[1]; });
    const total = items.reduce(function (sum, item) { return sum + item[1]; }, 0);
    if (!total) return;

    let cursor = 0;
    const segments = items.map(function (item, index) {
      const start = cursor;
      cursor += (item[1] / total) * 100;
      return allocationColors[index % allocationColors.length] + " " + start.toFixed(2) + "% " + cursor.toFixed(2) + "%";
    });
    ring.style.background = "conic-gradient(" + segments.join(",") + ")";
    setText(ring.querySelector("span"), "Gross mix");
    legend.replaceChildren();
    items.forEach(function (item, index) {
      const row = document.createElement("div");
      row.className = "legend-row";
      const swatch = document.createElement("span");
      swatch.className = "legend-swatch";
      swatch.style.backgroundColor = allocationColors[index % allocationColors.length];
      const name = document.createElement("span");
      name.textContent = item[0].replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
      const value = document.createElement("strong");
      value.textContent = ((item[1] / total) * 100).toFixed(1) + "%";
      value.title = formatCurrency(item[1], currency) + " gross exposure";
      row.append(swatch, name, value);
      legend.append(row);
    });
  }

  async function loadHoldings(portfolio, aum, loadVersion) {
    const rows = await restRequest("transaction_legs", {
      select: "quantity,instrument_id,transactions!inner(portfolio_id),instruments(id,symbol,name,asset_class,multiplier,currency)",
      "transactions.portfolio_id": "eq." + portfolio.id,
      quantity: "not.is.null"
    });
    if (loadVersion !== state.portfolioLoadVersion) return null;
    const positions = new Map();
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      if (!row.instrument_id || !row.instruments) return;
      const instrument = Array.isArray(row.instruments) ? row.instruments[0] : row.instruments;
      if (!instrument) return;
      const existing = positions.get(row.instrument_id) || { instrument: instrument, quantity: 0 };
      existing.quantity += Number(row.quantity) || 0;
      positions.set(row.instrument_id, existing);
    });
    const active = Array.from(positions.entries()).filter(function (entry) { return Math.abs(entry[1].quantity) > 0.00000001; });
    if (!active.length) {
      renderHoldings([], aum, portfolio.base_currency);
      return [];
    }
    const ids = active.map(function (entry) { return entry[0]; });
    const quoteRows = await restRequest("quotes", {
      select: "instrument_id,price,as_of",
      instrument_id: "in.(" + ids.join(",") + ")",
      order: "as_of.desc"
    });
    if (loadVersion !== state.portfolioLoadVersion) return null;
    const latestQuotes = new Map();
    (Array.isArray(quoteRows) ? quoteRows : []).forEach(function (quote) {
      if (!latestQuotes.has(quote.instrument_id)) latestQuotes.set(quote.instrument_id, quote);
    });
    const holdings = active.map(function (entry) {
      const instrument = entry[1].instrument;
      const quote = latestQuotes.get(entry[0]);
      const multiplier = Number(instrument.multiplier) || 1;
      const price = quote ? Number(quote.price) : null;
      return {
        id: entry[0],
        symbol: instrument.symbol,
        name: instrument.name,
        assetClass: instrument.asset_class,
        currency: instrument.currency || portfolio.base_currency,
        quantity: entry[1].quantity,
        price: Number.isFinite(price) ? price : null,
        quoteAsOf: quote && quote.as_of,
        marketValue: Number.isFinite(price) ? entry[1].quantity * price * multiplier : null
      };
    }).sort(function (a, b) { return Math.abs(Number(b.marketValue) || 0) - Math.abs(Number(a.marketValue) || 0); });
    renderHoldings(holdings, aum, portfolio.base_currency);
    return holdings;
  }

  function renderHoldings(holdings, aum, baseCurrency) {
    const body = byId("holdings-body");
    if (!body) return;
    body.replaceChildren();
    if (!holdings.length) {
      const row = document.createElement("tr");
      const cell = textCell("No posted positions are available for this portfolio.", "table-empty");
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
      return;
    }
    let missingQuotes = 0;
    holdings.forEach(function (holding) {
      const row = document.createElement("tr");
      const instrumentCell = document.createElement("td");
      const symbol = document.createElement("span");
      symbol.className = "holding-symbol";
      symbol.textContent = holding.symbol || "Instrument";
      const name = document.createElement("span");
      name.className = "holding-name";
      name.textContent = holding.name || "";
      instrumentCell.append(symbol, name);
      row.append(instrumentCell);
      row.append(textCell(formatNumber(holding.quantity, 6)));
      row.append(textCell(holding.price == null ? "Quote pending" : formatCurrency(holding.price, holding.currency)));
      row.append(textCell(holding.marketValue == null ? "—" : formatCurrency(holding.marketValue, baseCurrency)));
      row.append(textCell(holding.marketValue == null || !Number(aum) ? "—" : formatPercent((holding.marketValue / Number(aum)) * 100)));
      body.append(row);
      if (holding.price == null) missingQuotes += 1;
    });
    setText("holdings-note", missingQuotes
      ? missingQuotes + " holding" + (missingQuotes === 1 ? " is" : "s are") + " awaiting quote coverage; market values are incomplete."
      : "Market values use the most recent available quotes and may be delayed.");
  }

  function renderReportJobs(jobs) {
    const container = byId("report-jobs");
    if (!container) return;
    container.replaceChildren();
    if (!Array.isArray(jobs) || !jobs.length) {
      const message = document.createElement("p");
      message.className = "muted-copy";
      message.textContent = "No recent report requests.";
      container.append(message);
      return;
    }
    jobs.forEach(function (job) {
      const row = document.createElement("div");
      row.className = "report-job";
      const date = document.createElement("strong");
      date.textContent = formatDate(job.requested_at, true);
      const status = document.createElement("span");
      status.className = "job-state";
      status.textContent = String(job.status || "queued").replace(/_/g, " ");
      const note = document.createElement("span");
      note.textContent = job.completed_at ? "Completed " + formatDate(job.completed_at, true) : "Private report request";
      row.append(date, status, note);
      if (job.status === "ready" && config.reportDownloadFunction) {
        const download = document.createElement("button");
        download.type = "button";
        download.className = "text-button";
        download.textContent = "Download";
        download.addEventListener("click", function () { downloadReport(job.id, download); });
        row.append(download);
      }
      container.append(row);
    });
  }

  async function loadReportJobs(portfolioId, loadVersion) {
    const expectedVersion = loadVersion == null ? state.portfolioLoadVersion : loadVersion;
    const jobs = await restRequest("report_jobs", {
      select: "id,status,requested_at,completed_at",
      portfolio_id: "eq." + portfolioId,
      requested_by: "eq." + state.user.id,
      order: "requested_at.desc",
      limit: 5
    });
    if (expectedVersion !== state.portfolioLoadVersion) return false;
    renderReportJobs(jobs);
    return true;
  }

  async function downloadReport(jobId, button) {
    if (!config.reportDownloadFunction) return;
    button.disabled = true;
    try {
      const session = await currentSession();
      const result = await fetchJson(baseUrl() + "/functions/v1/" + encodeURIComponent(config.reportDownloadFunction), {
        method: "POST",
        headers: authHeaders(session.access_token),
        body: JSON.stringify({ job_id: jobId })
      });
      const url = result && result.url;
      const target = typeof url === "string" ? new URL(url) : null;
      const projectOrigin = new URL(baseUrl()).origin;
      if (!target || target.protocol !== "https:" || target.origin !== projectOrigin) throw new Error("invalid_download");
      window.location.assign(target.href);
    } catch (_error) {
      setMessage("report-message", "The report could not be downloaded. Please try again.", "error");
      button.disabled = false;
    }
  }

  function resetPortfolioView(portfolio) {
    state.latestNav = null;
    setText("metric-aum", "—");
    setText("metric-aum-note", "Awaiting verified valuation");
    setText("metric-return", "—");
    setText("metric-cash", "—");
    setText("metric-quote-age", "—");
    setText("portfolio-as-of", "Loading authorized portfolio records…");
    setText("chart-start", "Start —");
    setText("chart-end", "Latest —");
    setMessage("report-message", "");

    const chart = byId("performance-chart");
    if (chart) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const title = document.createElement("strong");
      title.textContent = "Loading performance history";
      const detail = document.createElement("span");
      detail.textContent = "Authorized daily valuations will appear here.";
      empty.append(title, detail);
      chart.replaceChildren(empty);
      chart.setAttribute("aria-label", "Loading account value history");
    }

    const ring = document.querySelector(".allocation-ring");
    if (ring) {
      ring.style.removeProperty("background");
      setText(ring.querySelector("span"), "—");
    }
    const legend = byId("allocation-legend");
    if (legend) {
      const message = document.createElement("p");
      message.className = "muted-copy";
      message.textContent = "Loading authorized allocation…";
      legend.replaceChildren(message);
    }

    const holdingsBody = byId("holdings-body");
    if (holdingsBody) {
      const row = document.createElement("tr");
      const cell = textCell("Loading authorized holdings…", "table-empty");
      cell.colSpan = 5;
      row.append(cell);
      holdingsBody.replaceChildren(row);
    }
    setText("holdings-note", "Market values require current quote coverage.");
    renderReportJobs([]);
    const reportButton = byId("request-report");
    if (reportButton) reportButton.disabled = true;
    setText("metric-inception", portfolio.inception_date ? "Since " + formatDate(portfolio.inception_date) : "Fund record");
  }

  async function loadPortfolio(portfolio) {
    const loadVersion = ++state.portfolioLoadVersion;
    state.portfolio = portfolio;
    setText("dashboard-title", portfolio.name || "Investor account");
    resetPortfolioView(portfolio);
    setStatus("pending", "Loading authorized portfolio records…");
    try {
      const history = await restRequest("daily_nav", {
        select: "nav_date,aum,nav_per_unit,cash_balance,return_since_inception,quote_as_of",
        portfolio_id: "eq." + portfolio.id,
        order: "nav_date.asc",
        limit: 7500
      });
      if (loadVersion !== state.portfolioLoadVersion) return;
      const rows = Array.isArray(history) ? history : [];
      const latest = rows.length ? rows[rows.length - 1] : null;
      state.latestNav = latest;
      if (latest) {
        setText("metric-aum", formatCurrency(latest.aum, portfolio.base_currency));
        setText("metric-aum-note", "Valuation date " + formatDate(latest.nav_date));
        setText("metric-return", formatPercent(latest.return_since_inception));
        setText("metric-cash", formatCurrency(latest.cash_balance, portfolio.base_currency));
        setText("metric-quote-age", latest.quote_as_of ? formatDate(latest.quote_as_of, true) : "Unavailable");
        setText("portfolio-as-of", "Portfolio valued " + formatDate(latest.nav_date) + ". Quotes may be delayed by at least 15 minutes.");
        renderChart(rows, portfolio.base_currency);
      } else {
        setText("portfolio-as-of", "No verified daily valuation has been published for this portfolio yet.");
      }
      const holdings = await loadHoldings(portfolio, latest && latest.aum, loadVersion);
      if (holdings === null || loadVersion !== state.portfolioLoadVersion) return;
      renderAllocation(holdings, latest && latest.cash_balance, portfolio.base_currency);
      if (!(await loadReportJobs(portfolio.id, loadVersion))) return;
      const reportButton = byId("request-report");
      if (reportButton) reportButton.disabled = false;
      setStatus("ready", "Authorized portfolio data loaded.");
    } catch (_error) {
      if (loadVersion === state.portfolioLoadVersion) handleDashboardFailure();
    }
  }

  async function requestReport() {
    if (!state.portfolio || !state.user) return;
    const portfolioId = state.portfolio.id;
    const loadVersion = state.portfolioLoadVersion;
    const button = byId("request-report");
    button.disabled = true;
    setMessage("report-message", "Submitting secure report request…");
    try {
      await restRequest("rpc/request_investor_report", {}, {
        method: "POST",
        prefer: "return=representation",
        body: { p_portfolio_id: portfolioId }
      });
      if (loadVersion !== state.portfolioLoadVersion) return;
      setMessage("report-message", "Report request queued. It will appear below when the protected renderer finishes.", "success");
      await loadReportJobs(portfolioId, loadVersion);
    } catch (_error) {
      setMessage("report-message", "The report could not be requested. Please try again.", "error");
    } finally {
      button.disabled = false;
    }
  }

  async function initDashboard() {
    attachSignOut();
    if (!(await bootstrapProtectedPage())) return;
    try {
      await Promise.all([loadProfile(), loadMemberships()]);
      setText("account-name", state.profile && state.profile.display_name ? state.profile.display_name : "Investor account");
      if (state.profile && (state.profile.role === "operations" || state.profile.role === "administrator")) byId("admin-link").classList.remove("is-hidden");
      if (!state.memberships.length) {
        setStatus("error", "Your account is active but no portfolio access has been assigned.");
        return;
      }
      const picker = byId("portfolio-picker");
      const pickerWrap = byId("portfolio-picker-wrap");
      state.memberships.forEach(function (membership) {
        const option = document.createElement("option");
        option.value = membership.portfolio.id;
        option.textContent = membership.portfolio.name;
        picker.append(option);
      });
      if (state.memberships.length > 1) pickerWrap.classList.remove("is-hidden");
      picker.addEventListener("change", function () {
        const membership = state.memberships.find(function (item) { return item.portfolio.id === picker.value; });
        if (membership) loadPortfolio(membership.portfolio);
      });
      byId("request-report").addEventListener("click", requestReport);
      await loadPortfolio(state.memberships[0].portfolio);
    } catch (_error) {
      handleDashboardFailure();
    }
  }

  function handleDashboardFailure() {
    setStatus("error", "Portfolio data is temporarily unavailable. Please sign in again or try later.");
  }

  function localDateTimeValue(date) {
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
  }

  function updateTradePreview() {
    const quantity = Number(byId("trade-quantity").value);
    const price = Number(byId("trade-price").value);
    const fees = Number(byId("trade-fees").value || 0);
    const side = byId("trade-side").value;
    const node = byId("trade-preview").querySelector("strong");
    if (!(quantity > 0) || !(price >= 0) || !(fees >= 0)) {
      node.textContent = "Enter quantity and price";
      return;
    }
    node.textContent = (side === "sell" ? "Sale" : "Purchase") + " input " + formatCurrency(quantity * price + fees, "USD") + " before contract multiplier";
  }

  async function submitTrade(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setMessage("trade-message", "");
    if (!form.reportValidity()) return;
    setBusy(form, true);
    try {
      const portfolioId = byId("trade-portfolio").value;
      const portfolio = state.adminPortfolios.find(function (item) { return item.id === portfolioId; });
      const symbol = byId("trade-symbol").value.trim().toUpperCase();
      const instruments = await restRequest("instruments", {
        select: "id,symbol,name,asset_class,multiplier,currency",
        symbol: "eq." + symbol,
        active: "eq.true",
        limit: 2
      });
      if (!Array.isArray(instruments) || instruments.length !== 1 || !portfolio) throw new Error("invalid_reference");
      const instrument = instruments[0];
      if (instrument.currency !== portfolio.base_currency) {
        setMessage("trade-message", "Cross-currency trades require the reconciled import workflow.", "error");
        return;
      }
      const quantity = Number(byId("trade-quantity").value);
      const price = Number(byId("trade-price").value);
      const fee = Number(byId("trade-fees").value || 0);
      const sideFactor = byId("trade-side").value === "sell" ? -1 : 1;
      const multiplier = Number(instrument.multiplier) || 1;
      const positionAmount = Math.round(sideFactor * quantity * price * multiplier * 100000000) / 100000000;
      const legs = [{
        instrument_id: instrument.id,
        leg_type: "position",
        quantity: sideFactor * quantity,
        unit_price: price,
        amount: positionAmount,
        currency: portfolio.base_currency
      }];
      if (fee > 0) legs.push({ instrument_id: null, leg_type: "fee", quantity: null, unit_price: null, amount: fee, currency: portfolio.base_currency });
      legs.push({ instrument_id: null, leg_type: "cash", quantity: null, unit_price: null, amount: -(positionAmount + fee), currency: portfolio.base_currency });

      await restRequest("rpc/post_ledger_transaction", {}, {
        method: "POST",
        prefer: "return=representation",
        body: {
          p_portfolio_id: portfolioId,
          p_trade_date: new Date(byId("trade-date").value).toISOString(),
          p_transaction_type: "trade",
          p_memo: byId("trade-memo").value.trim() || null,
          p_reversal_of: null,
          p_idempotency_key: byId("trade-idempotency").value.trim(),
          p_legs: legs
        }
      });
      form.reset();
      byId("trade-fees").value = "0";
      byId("trade-date").value = localDateTimeValue(new Date());
      updateTradePreview();
      setMessage("trade-message", "Ledger entry accepted. Reconciliation and valuation will run on the backend.", "success");
    } catch (_error) {
      setMessage("trade-message", "The ledger entry was not accepted. Verify the details and your authorization.", "error");
    } finally {
      setBusy(form, false);
    }
  }

  async function initAdmin() {
    attachSignOut();
    if (!(await bootstrapProtectedPage())) return;
    try {
      await loadProfile();
      const role = state.profile && state.profile.role;
      setText("operator-role", role ? role.replace(/_/g, " ") : "Role not verified");
      if (role !== "operations" && role !== "administrator") {
        setStatus("error", "Operations authorization was not granted for this account.");
        return;
      }
      const portfolios = await restRequest("portfolios", {
        select: "id,code,name,base_currency",
        order: "name.asc"
      });
      state.adminPortfolios = Array.isArray(portfolios) ? portfolios : [];
      const select = byId("trade-portfolio");
      state.adminPortfolios.forEach(function (portfolio) {
        const option = document.createElement("option");
        option.value = portfolio.id;
        option.textContent = portfolio.name + " (" + portfolio.code + ")";
        select.append(option);
      });
      byId("admin-denied").classList.add("is-hidden");
      byId("admin-console").classList.remove("is-hidden");
      byId("trade-date").value = localDateTimeValue(new Date());
      ["trade-quantity", "trade-price", "trade-fees", "trade-side"].forEach(function (id) { byId(id).addEventListener("input", updateTradePreview); });
      byId("trade-form").addEventListener("submit", submitTrade);
      setStatus("ready", "Operations role verified by the backend.");
    } catch (_error) {
      setStatus("error", "Operations authorization could not be verified.");
    }
  }

  document.querySelectorAll("[data-current-year]").forEach(function (node) { node.textContent = String(new Date().getFullYear()); });

  if (page === "login") initLogin().catch(function () { setStatus("error", "Secure sign in is temporarily unavailable."); });
  if (page === "dashboard") initDashboard();
  if (page === "admin") initAdmin();
})();
