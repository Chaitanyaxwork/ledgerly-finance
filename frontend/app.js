(function () {
  "use strict";

  // Where the backend lives. When the page is served by FastAPI (http://127.0.0.1:8000)
  // this is the same origin, "/api". When the page is opened any other way
  // (VS Code "Go Live" on :5500, file://, another dev server) it falls back to the
  // backend on port 8000. resolveApiBase() below picks the right one at startup.
  // To force a specific backend, set window.LEDGERLY_API_URL before app.js loads.
  let API_BASE = "/api";
  const BACKEND_CANDIDATES = ["http://127.0.0.1:8000/api", "http://localhost:8000/api"];
  const TOKEN_KEY = "ledgerly_token";
  let authToken = null;
  try { authToken = localStorage.getItem(TOKEN_KEY); } catch (_) { /* private-browsing storage block */ }
  let currentUser = null;
  let people = [];
  let transactions = [];
  let currentPersonId = null;
  let prefillPersonName = null;
  const openPersonCards = new Set();
  const openWaPanels = new Set();
  const charts = {};

  // ============================================================
  // API layer
  // ============================================================
  function setToken(token) {
    authToken = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (_) { /* private-browsing storage block — session just won't survive a reload */ }
  }

  /** For /api/auth/signup and /api/auth/login: never attaches a token, never triggers logout-on-401 (401 there just means "wrong password"). */
  async function publicFetch(path, options = {}) {
    const res = await fetch(API_BASE + path, { headers: { "Content-Type": "application/json" }, ...options });
    let body = null;
    try { body = await res.json(); } catch (_) {}
    if (!res.ok) {
      const detail = body && body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText;
      throw new Error(detail);
    }
    return body;
  }

  /** For every protected endpoint: attaches the bearer token; an expired/invalid session bounces to the sign-in screen instead of surfacing as a generic error. */
  async function apiFetch(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
    const res = await fetch(API_BASE + path, { ...options, headers });
    if (res.status === 401) {
      showAuthScreen("Your session has expired. Please sign in again.");
      throw new Error("Session expired.");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : JSON.stringify(body);
      } catch (_) {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  const authApi = {
    signup: (email, password) => publicFetch("/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) }),
    login: (email, password) => publicFetch("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
    me: () => apiFetch("/auth/me"),
    deleteAccount: (password) => apiFetch("/auth/me", { method: "DELETE", body: JSON.stringify({ password }) }),
  };

  // ============================================================
  // Auth screen (sign in / create account)
  // ============================================================
  let authMode = "signin";

  function showAuthScreen(message) {
    setToken(null);
    currentUser = null;
    document.body.classList.remove("is-authed");
    document.body.classList.add("pre-auth");
    const errEl = document.getElementById("authError");
    if (message) { errEl.textContent = message; errEl.classList.remove("hidden"); }
    else { errEl.classList.add("hidden"); }
    document.getElementById("auth-password").value = "";
  }

  function showApp(user) {
    currentUser = user;
    document.body.classList.remove("pre-auth");
    document.body.classList.add("is-authed");
    document.getElementById("accountEmail").textContent = user.email;
    document.getElementById("authError").classList.add("hidden");
  }

  function setAuthMode(mode) {
    authMode = mode;
    document.getElementById("tabSignin").classList.toggle("is-active", mode === "signin");
    document.getElementById("tabSignup").classList.toggle("is-active", mode === "signup");
    document.getElementById("authSub").textContent = mode === "signin" ? "Sign in to your ledger." : "Create your own private ledger.";
    document.getElementById("authSubmit").textContent = mode === "signin" ? "Sign in" : "Create account";
    document.getElementById("auth-password").setAttribute("autocomplete", mode === "signin" ? "current-password" : "new-password");
    document.getElementById("authPwHint").classList.toggle("hidden", mode === "signin");
    document.getElementById("authError").classList.add("hidden");
  }

  function initAuth() {
    setAuthMode("signin");
    document.getElementById("tabSignin").addEventListener("click", () => setAuthMode("signin"));
    document.getElementById("tabSignup").addEventListener("click", () => setAuthMode("signup"));

    document.getElementById("authForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = document.getElementById("auth-email").value.trim();
      const password = document.getElementById("auth-password").value;
      const errEl = document.getElementById("authError");
      const submitBtn = document.getElementById("authSubmit");
      errEl.classList.add("hidden");
      submitBtn.disabled = true;
      try {
        const result = authMode === "signin" ? await authApi.login(email, password) : await authApi.signup(email, password);
        setToken(result.access_token);
        showApp(result.user);
        await refreshAll();
      } catch (err) {
        errEl.textContent = err.message || "Something went wrong. Please try again.";
        errEl.classList.remove("hidden");
      } finally {
        submitBtn.disabled = false;
      }
    });

    document.getElementById("signOutBtn").addEventListener("click", () => showAuthScreen());

    const modal = document.getElementById("deleteAccountModal");
    document.getElementById("deleteAccountBtn").addEventListener("click", () => {
      document.getElementById("da-password").value = "";
      document.getElementById("deleteAccountError").classList.add("hidden");
      modal.classList.remove("hidden");
    });
    document.getElementById("deleteAccountCancel").addEventListener("click", () => modal.classList.add("hidden"));
    document.getElementById("deleteAccountForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const errEl = document.getElementById("deleteAccountError");
      try {
        await authApi.deleteAccount(document.getElementById("da-password").value);
        modal.classList.add("hidden");
        showAuthScreen("Your account has been deleted.");
      } catch (err) {
        errEl.textContent = err.message || "Could not delete account.";
        errEl.classList.remove("hidden");
      }
    });
  }

  /** Runs once at startup: if a saved token still works, go straight to the app; otherwise show sign-in. */
  async function bootstrapSession() {
    if (!authToken) { showAuthScreen(); return; }
    try {
      const user = await authApi.me();
      showApp(user);
      await refreshAll();
    } catch (_) {
      // apiFetch already calls showAuthScreen() on a 401; this catches any other failure too.
      showAuthScreen();
    }
  }

  const api = {
    listPeople: (includeArchived = false) => apiFetch(`/people${includeArchived ? "?include_archived=true" : ""}`),
    createPerson: (payload) => apiFetch("/people", { method: "POST", body: JSON.stringify(payload) }),
    getPerson: (id) => apiFetch(`/people/${id}`),
    updatePerson: (id, payload) => apiFetch(`/people/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    deletePerson: (id) => apiFetch(`/people/${id}`, { method: "DELETE" }),
    listTransactions: () => apiFetch("/transactions"),
    createTransaction: (payload) => apiFetch("/transactions", { method: "POST", body: JSON.stringify(payload) }),
    setStatus: (id, status) => apiFetch(`/transactions/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    setDueDate: (id, due_date, grace_period_days) => apiFetch(`/transactions/${id}/due-date`, { method: "PATCH", body: JSON.stringify({ due_date, grace_period_days }) }),
    deleteTransaction: (id) => apiFetch(`/transactions/${id}`, { method: "DELETE" }),
    createRepayment: (payload) => apiFetch("/repayments", { method: "POST", body: JSON.stringify(payload) }),
    monthlyReport: () => apiFetch("/reports/monthly"),
    resetAll: () => apiFetch("/reset", { method: "DELETE" }),
  };

  async function probe(base) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 2500);
      const res = await fetch(base + "/health", { signal: ctrl.signal, cache: "no-store" });
      clearTimeout(timer);
      if (!res.ok) return false;
      const body = await res.json();
      return body && body.status === "ok";
    } catch (_) { return false; }
  }

  async function resolveApiBase() {
    const candidates = [];
    if (window.LEDGERLY_API_URL) candidates.push(String(window.LEDGERLY_API_URL).replace(/\/$/, ""));
    if (location.protocol.startsWith("http")) candidates.push("/api");
    candidates.push(...BACKEND_CANDIDATES);
    for (const base of candidates) {
      if (await probe(base)) { API_BASE = base; return true; }
    }
    return false;
  }

  function showBackendDownBanner() {
    if (document.getElementById("backendDownBanner")) return;
    const el = document.createElement("div");
    el.id = "backendDownBanner";
    el.style.cssText = "position:sticky;top:0;z-index:9999;background:#B23A2E;color:#fff;padding:10px 16px;font:500 14px/1.4 Inter,system-ui,sans-serif;text-align:center";
    el.innerHTML = "Can't reach the Ledgerly backend. Start it with <code style=\"background:rgba(0,0,0,.25);padding:1px 6px;border-radius:4px\">python run.py</code> (or start.sh / start.bat), then <a href=\"\" style=\"color:#fff;text-decoration:underline\">reload</a>.";
    document.body.prepend(el);
  }

  // ============================================================
  // Formatting helpers
  // ============================================================
  const currencyFmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
  const compactCurrencyFmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });
  function fmt(v) { return currencyFmt.format(Number(v) || 0); }
  function fmtCompact(v) { return compactCurrencyFmt.format(Number(v) || 0); }
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "T00:00:00");
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
  }
  function trimNum(n) { return Number(n).toString(); }
  function todayISO() { return new Date().toISOString().slice(0, 10); }
  function daysDiffFromToday(iso) {
    const due = new Date(iso + "T00:00:00");
    const today = new Date(todayISO() + "T00:00:00");
    return Math.round((due - today) / 86400000);
  }
  function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function rateLabel(t) { return `${trimNum(t.interest_rate)}% / ${t.rate_period === "Monthly" ? "month" : "year"}`; }

  function dueBadge(t) {
    if (t.due_status === "paid") return `<span class="due-badge due-badge--paid">🟢 Paid</span>`;
    if (t.due_status === "none") return `<span class="due-badge due-badge--none">No due date</span>`;
    if (t.due_status === "upcoming") return `<span class="due-badge due-badge--upcoming">🔵 Upcoming · ${fmtDate(t.due_date)}</span>`;
    if (t.due_status === "due_today") return `<span class="due-badge due-badge--due-today">🟡 Due Today</span>`;
    if (t.due_status === "grace_period") {
      const graceEnd = addDays(t.due_date, t.grace_period_days);
      return `<span class="due-badge due-badge--grace">🟠 Grace period · ends ${fmtDate(graceEnd)}</span>`;
    }
    if (t.due_status === "overdue") return `<span class="due-badge due-badge--overdue">🔴 Overdue by ${t.days_overdue}d</span>`;
    return `<span class="due-badge due-badge--none">—</span>`;
  }
  function addDays(iso, days) {
    const d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + Number(days || 0));
    return d.toISOString().slice(0, 10);
  }
  const PHASE_LABEL = { paid: "Paid", upcoming: "Upcoming", due_today: "Due Today", grace_period: "Grace Period", overdue: "Overdue", none: "No due date" };

  // ============================================================
  // Toast
  // ============================================================
  let toastTimer = null;
  function showToast(message, isError) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.classList.toggle("is-error", !!isError);
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  // ============================================================
  // Navigation
  // ============================================================
  function setActiveView(view) {
    document.querySelectorAll(".view").forEach((el) => el.classList.toggle("is-active", el.id === `view-${view}`));
    document.querySelectorAll(".nav-item").forEach((el) => el.classList.toggle("is-active", el.dataset.view === view));
    document.querySelectorAll(".bottom-nav-item").forEach((el) => el.classList.toggle("is-active", el.dataset.view === view));
    closeSidebar();
    window.scrollTo(0, 0);
  }
  function openSidebar() { document.getElementById("sidebar").classList.add("is-open"); document.getElementById("scrim").classList.add("is-visible"); }
  function closeSidebar() { document.getElementById("sidebar").classList.remove("is-open"); document.getElementById("scrim").classList.remove("is-visible"); }

  function initNav() {
    document.getElementById("nav").addEventListener("click", (e) => {
      const btn = e.target.closest(".nav-item");
      if (btn) setActiveView(btn.dataset.view);
    });
    document.querySelector(".main").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-view]");
      if (btn && !btn.classList.contains("nav-item")) setActiveView(btn.dataset.view);
    });
    document.getElementById("navToggle").addEventListener("click", openSidebar);
    document.getElementById("scrim").addEventListener("click", closeSidebar);
    document.getElementById("topSearchBtn").addEventListener("click", () => {
      openSidebar();
      document.getElementById("globalSearch").focus();
    });
    document.getElementById("bellBtn").addEventListener("click", () => setActiveView("dashboard"));

    const bottomNav = document.getElementById("bottomNav");
    if (bottomNav) {
      bottomNav.addEventListener("click", (e) => {
        const btn = e.target.closest(".bottom-nav-item");
        if (!btn) return;
        if (btn.dataset.action === "more") { openSidebar(); return; }
        setActiveView(btn.dataset.view);
      });
    }
  }

  // ============================================================
  // Data refresh
  // ============================================================
  async function refreshAll() {
    try {
      [people, transactions] = await Promise.all([api.listPeople(), api.listTransactions()]);
    } catch (err) {
      showToast("Could not load data: " + err.message, true);
      people = []; transactions = [];
    }
    renderDashboard();
    renderPeople();
    renderLedger();
    renderRecords();
    renderPersonDatalist();
    if (currentPersonId) renderPersonDetail(currentPersonId);
  }

  function computeNotifications() {
    const items = [];
    transactions.forEach((t) => {
      if (t.status === "Settled") return;
      if (!t.due_date) return;
      const daysToDue = daysDiffFromToday(t.due_date);
      if (t.due_status === "upcoming" && daysToDue === 2) {
        items.push({ level: "info", txn: t, message: `Due in 2 days — ${escapeHtml(t.person_name)}, ${fmt(t.balance)}` });
      } else if (t.due_status === "due_today") {
        items.push({ level: "warning", txn: t, message: `Due today — ${escapeHtml(t.person_name)}, ${fmt(t.balance)}` });
      } else if (t.due_status === "grace_period") {
        items.push({ level: "warning", txn: t, message: `In grace period — ${escapeHtml(t.person_name)}, ${fmt(t.balance)}` });
      } else if (t.due_status === "overdue") {
        items.push({ level: "danger", txn: t, message: `Overdue by ${t.days_overdue}d — ${escapeHtml(t.person_name)}, ${fmt(t.balance)}` });
      }
    });
    const order = { danger: 0, warning: 1, info: 2 };
    items.sort((a, b) => order[a.level] - order[b.level]);
    return items;
  }

  function renderNotifications() {
    const items = computeNotifications();
    const badge = document.getElementById("bellBadge");
    badge.textContent = String(items.length);
    badge.classList.toggle("hidden", items.length === 0);

    const panel = document.getElementById("notifPanel");
    const list = document.getElementById("notifList");
    if (items.length === 0) { panel.classList.add("hidden"); list.innerHTML = ""; return; }
    panel.classList.remove("hidden");
    list.innerHTML = items.map((n) => `<div class="notif-item notif-item--${n.level}">${n.message}</div>`).join("");
  }

  // ============================================================
  // Dashboard
  // ============================================================
  function renderDashboard() {
    const empty = document.getElementById("dashboardEmpty");
    const metricRow = document.getElementById("metricRow");

    if (transactions.length === 0) {
      empty.classList.remove("hidden");
      metricRow.innerHTML = "";
      document.querySelectorAll("#view-dashboard .panel-row").forEach((el) => el.classList.add("hidden"));
      renderNotifications();
      return;
    }
    empty.classList.add("hidden");
    document.querySelectorAll("#view-dashboard .panel-row").forEach((el) => el.classList.remove("hidden"));
    renderNotifications();

    const active = transactions.filter((t) => t.status === "Active");
    const given = active.filter((t) => t.txn_type === "Given");
    const taken = active.filter((t) => t.txn_type === "Taken");
    const sum = (arr, key) => arr.reduce((s, t) => s + t[key], 0);

    const totalReceivable = sum(given, "balance");
    const totalPayable = sum(taken, "balance");
    const outstanding = totalReceivable - totalPayable;
    const totalInterest = sum(active, "accrued_interest");

    metricRow.innerHTML = `
      <div class="metric-card metric-card--given"><p class="metric-label">Total Receivable</p><p class="metric-value">${fmt(totalReceivable)}</p></div>
      <div class="metric-card metric-card--taken"><p class="metric-label">Total Payable</p><p class="metric-value">${fmt(totalPayable)}</p></div>
      <div class="metric-card metric-card--net"><p class="metric-label">Outstanding</p><p class="metric-value">${fmt(outstanding)}</p></div>
      <div class="metric-card"><p class="metric-label">Total Interest</p><p class="metric-value">${fmt(totalInterest)}</p></div>
    `;

    renderOutstandingChart(active);
    renderFlowChart(active);
    renderStatusChart();
    renderDueSoonWidget();
  }

  function ensureChart(id, config) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(canvas.getContext("2d"), config);
  }

  function renderOutstandingChart(active) {
    const byPerson = {};
    active.forEach((t) => {
      const sign = t.txn_type === "Given" ? 1 : -1;
      byPerson[t.person_name] = (byPerson[t.person_name] || 0) + sign * t.balance;
    });
    const entries = Object.entries(byPerson).filter(([, v]) => Math.abs(v) > 0.01).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 8);
    const empty = document.getElementById("chartOutstandingEmpty");
    if (entries.length === 0) {
      empty.classList.remove("hidden");
      if (charts.chartOutstanding) charts.chartOutstanding.destroy();
      return;
    }
    empty.classList.add("hidden");
    ensureChart("chartOutstanding", {
      type: "bar",
      data: {
        labels: entries.map(([name]) => name),
        datasets: [{
          data: entries.map(([, v]) => v),
          backgroundColor: entries.map(([, v]) => (v >= 0 ? "#1D7A5F" : "#B23A2E")),
          borderRadius: 4,
        }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => fmt(ctx.raw) } } },
        scales: { x: { ticks: { callback: (v) => fmtCompact(v) } } },
      },
    });
  }

  function renderFlowChart(active) {
    const totalGiven = active.filter((t) => t.txn_type === "Given").reduce((s, t) => s + t.principal, 0);
    const totalTaken = active.filter((t) => t.txn_type === "Taken").reduce((s, t) => s + t.principal, 0);
    const totalRepaid = active.reduce((s, t) => s + t.repaid, 0);
    ensureChart("chartFlow", {
      type: "bar",
      data: {
        labels: ["Given", "Taken", "Repaid"],
        datasets: [{ data: [totalGiven, totalTaken, totalRepaid], backgroundColor: ["#1D7A5F", "#B23A2E", "#A6782C"], borderRadius: 4 }],
      },
      options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => fmt(ctx.raw) } } }, scales: { y: { ticks: { callback: (v) => fmtCompact(v) } } } },
    });
  }

  function renderStatusChart() {
    const settled = transactions.filter((t) => t.status === "Settled").length;
    const overdue = transactions.filter((t) => t.due_status === "overdue").length;
    const grace = transactions.filter((t) => t.due_status === "grace_period" || t.due_status === "due_today").length;
    const activeOk = transactions.filter((t) => t.status === "Active" && !["overdue", "grace_period", "due_today"].includes(t.due_status)).length;
    ensureChart("chartStatus", {
      type: "doughnut",
      data: { labels: ["Active", "Grace/Due Today", "Overdue", "Paid"], datasets: [{ data: [activeOk, grace, overdue, settled], backgroundColor: ["#A6782C", "#E0A526", "#B23A2E", "#1D7A5F"] }] },
      options: { plugins: { legend: { position: "bottom" } } },
    });
  }

  function renderDueSoonWidget() {
    const body = document.getElementById("dueSoonBody");
    const empty = document.getElementById("dueSoonEmpty");
    const priority = { overdue: 0, grace_period: 1, due_today: 2, upcoming: 3 };
    const items = transactions
      .filter((t) => ["overdue", "grace_period", "due_today"].includes(t.due_status))
      .sort((a, b) => (priority[a.due_status] ?? 9) - (priority[b.due_status] ?? 9));
    if (items.length === 0) {
      body.innerHTML = ""; empty.classList.remove("hidden"); return;
    }
    empty.classList.add("hidden");
    body.innerHTML = items.slice(0, 8).map((t) => `
      <tr><td>${escapeHtml(t.person_name)}</td><td class="num">${fmt(t.balance)}</td><td>${dueBadge(t)}</td></tr>
    `).join("");
  }

  // ============================================================
  // People
  // ============================================================
  function renderPeople() {
    const grid = document.getElementById("peopleGrid");
    const empty = document.getElementById("peopleEmpty");
    if (people.length === 0) { grid.innerHTML = ""; empty.classList.remove("hidden"); return; }
    empty.classList.add("hidden");
    grid.innerHTML = people.map((p) => `
      <div class="person-tile" data-person-id="${p.id}">
        <div class="person-tile-head">
          <div class="person-name">${escapeHtml(p.name)}</div>
          <div class="person-net ${p.outstanding >= 0 ? "is-positive" : "is-negative"}">${fmt(p.outstanding)}</div>
        </div>
        <div class="person-tile-meta">${escapeHtml(p.phone || "No phone")} · ${p.transaction_count} txn${p.transaction_count === 1 ? "" : "s"}</div>
        <div class="person-tile-stats">
          <span>Given <strong>${fmt(p.total_given)}</strong></span>
          <span>Paid <strong>${fmt(p.total_repaid)}</strong></span>
          <span>Interest <strong>${fmt(p.total_interest)}</strong></span>
        </div>
        <div class="person-tile-actions">
          <button class="btn btn-sm btn-primary" data-view-person="${p.id}">View Details</button>
        </div>
      </div>
    `).join("");
  }

  function renderPersonDatalist() {
    document.getElementById("personList").innerHTML = people.map((p) => `<option value="${escapeHtml(p.name)}">`).join("");
  }

  function initPeopleEvents() {
    document.getElementById("peopleGrid").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-view-person]");
      if (btn) openPersonDetail(Number(btn.dataset.viewPerson));
    });
    document.getElementById("openAddPersonBtn").addEventListener("click", () => document.getElementById("addPersonCard").classList.remove("hidden"));
    document.getElementById("emptyAddPersonBtn").addEventListener("click", () => document.getElementById("addPersonCard").classList.remove("hidden"));
    document.getElementById("cancelAddPersonBtn").addEventListener("click", () => document.getElementById("addPersonCard").classList.add("hidden"));

    document.getElementById("addPersonForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = document.getElementById("p-name").value.trim();
      if (!name) return showToast("Name is required.", true);
      try {
        await api.createPerson({ name, phone: document.getElementById("p-phone").value.trim(), notes: document.getElementById("p-notes").value.trim() });
        showToast(`${name} added.`);
        document.getElementById("addPersonForm").reset();
        document.getElementById("addPersonCard").classList.add("hidden");
        await refreshAll();
      } catch (err) { showToast("Could not add person: " + err.message, true); }
    });
  }

  // ============================================================
  // Person Detail
  // ============================================================
  function openPersonDetail(personId) {
    currentPersonId = personId;
    setActiveView("person-detail");
    renderPersonDetail(personId);
  }

  function renderPersonDetail(personId) {
    const person = people.find((p) => p.id === personId);
    if (!person) { setActiveView("people"); return; }

    document.getElementById("pdName").textContent = person.name;
    document.getElementById("pdPhone").textContent = person.phone || "No phone number on file";
    document.getElementById("pdQuickAddHint").textContent = `Adds a new Given/Taken entry for ${person.name}.`;

    document.getElementById("pdMetricRow").innerHTML = `
      <div class="metric-card metric-card--given"><p class="metric-label">Given</p><p class="metric-value">${fmt(person.total_given)}</p></div>
      <div class="metric-card metric-card--taken"><p class="metric-label">Taken</p><p class="metric-value">${fmt(person.total_taken)}</p></div>
      <div class="metric-card"><p class="metric-label">Interest</p><p class="metric-value">${fmt(person.total_interest)}</p></div>
      <div class="metric-card metric-card--net"><p class="metric-label">Outstanding</p><p class="metric-value">${fmt(person.outstanding)}</p></div>
    `;

    const mine = transactions.filter((t) => t.person_id === personId).sort((a, b) => (a.txn_date < b.txn_date ? 1 : -1));
    const list = document.getElementById("pdTxnList");
    if (mine.length === 0) {
      list.innerHTML = `<p class="empty-note">No transactions yet for ${escapeHtml(person.name)}.</p>`;
    } else {
      list.innerHTML = mine.map(renderTxnBlock).join("");
    }
  }

  function initPersonDetailEvents() {
    document.getElementById("backToPeopleBtn").addEventListener("click", () => { currentPersonId = null; setActiveView("people"); });
    document.getElementById("pdQuickAddBtn").addEventListener("click", () => {
      const person = people.find((p) => p.id === currentPersonId);
      if (person) prefillPersonName = person.name;
      setActiveView("add");
      applyPrefill();
    });
    document.getElementById("pdArchiveBtn").addEventListener("click", async () => {
      if (!currentPersonId) return;
      if (!window.confirm("Archive this person? They'll be hidden from People and the dashboard, but their records are kept.")) return;
      try {
        await api.updatePerson(currentPersonId, { archived: true });
        showToast("Archived.");
        currentPersonId = null;
        setActiveView("people");
        await refreshAll();
      } catch (err) { showToast("Could not archive: " + err.message, true); }
    });
    document.getElementById("pdEditBtn").addEventListener("click", () => {
      const person = people.find((p) => p.id === currentPersonId);
      if (!person) return;
      document.getElementById("pd-e-name").value = person.name;
      document.getElementById("pd-e-phone").value = person.phone;
      document.getElementById("pd-e-notes").value = person.notes;
      document.getElementById("pdEditCard").classList.remove("hidden");
    });
    document.getElementById("pdCancelEditBtn").addEventListener("click", () => document.getElementById("pdEditCard").classList.add("hidden"));
    document.getElementById("pdEditForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api.updatePerson(currentPersonId, {
          name: document.getElementById("pd-e-name").value.trim(),
          phone: document.getElementById("pd-e-phone").value.trim(),
          notes: document.getElementById("pd-e-notes").value.trim(),
        });
        showToast("Saved.");
        document.getElementById("pdEditCard").classList.add("hidden");
        await refreshAll();
      } catch (err) { showToast("Could not save: " + err.message, true); }
    });
    document.getElementById("pdWhatsappBtn").addEventListener("click", () => {
      const person = people.find((p) => p.id === currentPersonId);
      if (!person) return;
      const message = [
        `Hi ${person.name}`, "", "Quick reminder about our account:", "",
        `Given: ${fmt(person.total_given)}`, `Paid: ${fmt(person.total_repaid)}`, `Interest: ${fmt(person.total_interest)}`,
        `Outstanding: ${fmt(person.outstanding)}`, "", "Please let me know when this can be settled. Thank you!",
      ].join("\n");
      const digits = (person.phone || "").replace(/\D/g, "");
      if (digits) window.open(`https://wa.me/${digits}?text=${encodeURIComponent(message)}`, "_blank");
      else { navigator.clipboard?.writeText(message); showToast("No phone on file — message copied instead."); }
    });
  }

  function applyPrefill() {
    if (!prefillPersonName) return;
    const input = document.getElementById("f-person");
    if (input) input.value = prefillPersonName;
    prefillPersonName = null;
    updateInterestPreview();
  }

  // ============================================================
  // Ledger & Settlements (shared transaction-block renderer)
  // ============================================================
  function renderTxnBlock(t) {
    const showSettledHint = t.status === "Active" && t.balance <= 0;
    return `
      <div class="txn-block" data-txn-id="${t.id}">
        <div class="txn-block-head">
          <span class="badge badge--${t.txn_type.toLowerCase()}">${t.txn_type}</span>
          <span class="txn-line">${fmtDate(t.txn_date)}</span>
          <span class="txn-line">Principal <strong>${fmt(t.principal)}</strong></span>
          <span class="txn-line">${rateLabel(t)}, ${t.interest_type}</span>
          <span class="txn-line">Interest <strong>${fmt(t.accrued_interest)}</strong></span>
          <span class="txn-line">Balance <strong>${fmt(t.balance)}</strong></span>
          ${dueBadge(t)}
        </div>
        ${showSettledHint ? `<p class="settled-note">This looks fully repaid — it will auto-settle, or mark it manually below.</p>` : ""}

        <div class="due-edit-row">
          <label>Due date</label>
          <input type="date" class="due-edit-input" value="${t.due_date || ""}" data-due-id="${t.id}" />
          <label>Grace (days)</label>
          <input type="number" min="0" max="90" class="grace-edit-input" value="${t.grace_period_days}" data-grace-id="${t.id}" style="max-width:70px;" />
          <button type="button" class="btn btn-sm btn-ghost" data-save-due="${t.id}">Save</button>
        </div>

        <form class="settle-row repay-form" data-repay-id="${t.id}">
          <div class="field"><label>Repayment amount (₹)</label><input type="number" min="0.01" step="0.01" name="amount" required /></div>
          <div class="field"><label>Date</label><input type="date" name="repay_date" min="${t.txn_date}" max="${todayISO()}" value="${todayISO()}" required /></div>
          <div class="field"><button type="submit" class="btn btn-primary btn-sm btn-block">Log repayment</button></div>
        </form>

        <div class="settle-actions">
          <button type="button" class="btn btn-sm" data-toggle-status="${t.id}" data-current-status="${t.status}">Mark ${t.status === "Active" ? "Settled" : "Active"}</button>
          <button type="button" class="btn btn-sm btn-ghost" data-wa-toggle="${t.id}">📲 WhatsApp</button>
          <button type="button" class="btn btn-sm btn-ghost" data-receipt="${t.id}">🧾 Receipt</button>
        </div>
        ${openWaPanels.has(t.id) ? renderWaPanel(t) : ""}
      </div>
    `;
  }

  function generateWhatsAppMessage(t) {
    const relationship = t.txn_type === "Given" ? "you owe me" : "I owe you";
    return [
      `Hi ${t.person_name}`, "", `Quick reminder about our account (${relationship}):`, "",
      `Date: ${fmtDate(t.txn_date)}`, `Original amount: ${fmt(t.principal)}`,
      `Interest: ${rateLabel(t)}, ${t.interest_type} -> ${fmt(t.accrued_interest)}`,
      `Paid: ${fmt(t.repaid)}`, `Remaining: ${fmt(t.balance)}`,
      t.due_date ? `Due date: ${fmtDate(t.due_date)}.` : "",
      "", "Please let me know when this can be settled. Thank you!",
    ].filter(Boolean).join("\n");
  }

  function renderWaPanel(t) {
    const message = generateWhatsAppMessage(t);
    const digits = (t.phone || "").replace(/\D/g, "");
    const waUrl = digits ? `https://wa.me/${digits}?text=${encodeURIComponent(message)}` : "";
    return `
      <div class="wa-panel">
        <pre>${escapeHtml(message)}</pre>
        <div class="wa-panel-actions">
          ${waUrl ? `<a class="btn btn-primary btn-sm" href="${waUrl}" target="_blank" rel="noopener">Open in WhatsApp</a>` : `<p class="field-hint">No phone on file.</p>`}
          <button type="button" class="btn btn-sm btn-ghost" data-copy-wa="${t.id}">Copy message</button>
        </div>
      </div>
    `;
  }

  function openReceipt(t) {
    document.getElementById("rcNumber").textContent = `UDR-${new Date().getFullYear()}-${String(t.id).padStart(5, "0")}`;
    document.getElementById("rcDate").textContent = fmtDate(todayISO());
    document.getElementById("rcPerson").textContent = t.person_name;
    document.getElementById("rcPrincipal").textContent = fmt(t.principal);
    document.getElementById("rcInterest").textContent = fmt(t.accrued_interest);
    document.getElementById("rcPayment").textContent = fmt(t.repaid);
    document.getElementById("rcRemaining").textContent = fmt(t.balance);
    document.getElementById("receiptView").classList.remove("hidden");
    window.print();
    document.getElementById("receiptView").classList.add("hidden");
  }

  function bindTxnBlockEvents(container) {
    container.addEventListener("click", async (e) => {
      const statusBtn = e.target.closest("[data-toggle-status]");
      if (statusBtn) {
        const id = Number(statusBtn.dataset.toggleStatus);
        const next = statusBtn.dataset.currentStatus === "Active" ? "Settled" : "Active";
        try { await api.setStatus(id, next); showToast(`Marked ${next.toLowerCase()}.`); await refreshAll(); }
        catch (err) { showToast("Could not update status: " + err.message, true); }
        return;
      }
      const waBtn = e.target.closest("[data-wa-toggle]");
      if (waBtn) {
        const id = Number(waBtn.dataset.waToggle);
        if (openWaPanels.has(id)) openWaPanels.delete(id); else openWaPanels.add(id);
        renderLedger(); if (currentPersonId) renderPersonDetail(currentPersonId);
        return;
      }
      const copyBtn = e.target.closest("[data-copy-wa]");
      if (copyBtn) {
        const id = Number(copyBtn.dataset.copyWa);
        const t = transactions.find((x) => x.id === id);
        if (t) { navigator.clipboard?.writeText(generateWhatsAppMessage(t)); showToast("Copied."); }
        return;
      }
      const receiptBtn = e.target.closest("[data-receipt]");
      if (receiptBtn) {
        const t = transactions.find((x) => x.id === Number(receiptBtn.dataset.receipt));
        if (t) openReceipt(t);
        return;
      }
      const saveDueBtn = e.target.closest("[data-save-due]");
      if (saveDueBtn) {
        const id = Number(saveDueBtn.dataset.saveDue);
        const dueInput = container.querySelector(`.due-edit-input[data-due-id="${id}"]`);
        const graceInput = container.querySelector(`.grace-edit-input[data-grace-id="${id}"]`);
        try {
          await api.setDueDate(id, dueInput.value || null, graceInput ? Number(graceInput.value) : undefined);
          showToast("Due date updated.");
          await refreshAll();
        }
        catch (err) { showToast("Could not update due date: " + err.message, true); }
        return;
      }
    });

    container.addEventListener("submit", async (e) => {
      const form = e.target.closest(".repay-form");
      if (!form) return;
      e.preventDefault();
      const id = Number(form.dataset.repayId);
      const amount = parseFloat(form.amount.value);
      const repay_date = form.repay_date.value;
      if (!(amount > 0)) return showToast("Enter an amount greater than zero.", true);
      try {
        await api.createRepayment({ transaction_id: id, amount, repay_date });
        showToast("Repayment logged.");
        await refreshAll();
      } catch (err) { showToast("Could not log repayment: " + err.message, true); }
    });
  }

  function renderLedger() {
    const statusFilter = document.getElementById("ledgerStatusFilter").value;
    const dueFilter = document.getElementById("ledgerDueFilter").value;
    const personSelect = document.getElementById("ledgerPersonFilter");
    const prevPerson = personSelect.value;
    const allPeople = [...new Set(transactions.map((t) => t.person_name))].sort();
    personSelect.innerHTML = `<option value="All">All</option>` + allPeople.map((p) => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join("");
    personSelect.value = allPeople.includes(prevPerson) || prevPerson === "All" ? prevPerson : "All";
    const personFilter = personSelect.value;

    let view = transactions;
    if (statusFilter !== "All") view = view.filter((t) => t.status === statusFilter);
    if (dueFilter !== "All") view = view.filter((t) => t.due_status === dueFilter);
    if (personFilter !== "All") view = view.filter((t) => t.person_name === personFilter);

    const list = document.getElementById("ledgerList");
    const empty = document.getElementById("ledgerEmpty");
    if (view.length === 0) { list.innerHTML = ""; empty.classList.remove("hidden"); return; }
    empty.classList.add("hidden");

    const people_ = [...new Set(view.map((t) => t.person_name))];
    list.innerHTML = people_.map((person) => {
      const rows = view.filter((t) => t.person_name === person);
      const given = rows.filter((t) => t.txn_type === "Given").reduce((s, t) => s + t.balance, 0);
      const taken = rows.filter((t) => t.txn_type === "Taken").reduce((s, t) => s + t.balance, 0);
      const net = given - taken;
      const isOpen = openPersonCards.has(person);
      return `
        <div class="person-card ${isOpen ? "is-open" : ""}">
          <button type="button" class="person-card-header" data-toggle-person="${escapeHtml(person)}">
            <div class="person-name-row">
              <svg class="chevron" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <div><div class="person-name">${escapeHtml(person)}</div><div class="person-meta">${rows.length} ${rows.length === 1 ? "entry" : "entries"}</div></div>
            </div>
            <div class="person-net ${net >= 0 ? "is-positive" : "is-negative"}">${fmt(net)}</div>
          </button>
          <div class="person-body">${rows.map(renderTxnBlock).join("")}</div>
        </div>
      `;
    }).join("");
  }

  function initLedgerEvents() {
    const list = document.getElementById("ledgerList");
    bindTxnBlockEvents(list);
    list.addEventListener("click", (e) => {
      const toggleBtn = e.target.closest("[data-toggle-person]");
      if (toggleBtn) {
        const person = toggleBtn.dataset.togglePerson;
        if (openPersonCards.has(person)) openPersonCards.delete(person); else openPersonCards.add(person);
        renderLedger();
      }
    });
    document.getElementById("ledgerStatusFilter").addEventListener("change", renderLedger);
    document.getElementById("ledgerDueFilter").addEventListener("change", renderLedger);
    document.getElementById("ledgerPersonFilter").addEventListener("change", renderLedger);
    bindTxnBlockEvents(document.getElementById("pdTxnList"));
  }

  // ============================================================
  // All Records
  // ============================================================
  function filteredRecords() {
    const search = document.getElementById("recordsSearch").value.trim().toLowerCase();
    const typeFilter = document.getElementById("recordsTypeFilter").value;
    const statusFilter = document.getElementById("recordsStatusFilter").value;
    let view = transactions;
    if (search) view = view.filter((t) => t.person_name.toLowerCase().includes(search));
    if (typeFilter !== "All") view = view.filter((t) => t.txn_type === typeFilter);
    if (statusFilter !== "All") view = view.filter((t) => t.status === statusFilter);
    return view;
  }

  function renderRecords() {
    const view = filteredRecords();
    const body = document.getElementById("recordsBody");
    const empty = document.getElementById("recordsEmpty");
    if (transactions.length === 0) { empty.textContent = "No transactions yet."; empty.classList.remove("hidden"); body.innerHTML = ""; }
    else if (view.length === 0) { empty.textContent = "No records match these filters."; empty.classList.remove("hidden"); body.innerHTML = ""; }
    else {
      empty.classList.add("hidden");
      body.innerHTML = view.map((t) => `
        <tr>
          <td>${escapeHtml(t.person_name)}</td>
          <td><span class="badge badge--${t.txn_type.toLowerCase()}">${t.txn_type}</span></td>
          <td>${fmtDate(t.txn_date)}</td>
          <td>${dueBadge(t)}</td>
          <td class="num">${fmt(t.principal)}</td>
          <td>${trimNum(t.interest_rate)}% / ${t.rate_period === "Monthly" ? "mo" : "yr"}</td>
          <td class="num">${fmt(t.accrued_interest)}</td>
          <td class="num">${fmt(t.total_due)}</td>
          <td class="num">${fmt(t.repaid)}</td>
          <td class="num">${fmt(t.balance)}</td>
          <td><span class="badge badge--${t.status.toLowerCase()}">${t.status}</span></td>
        </tr>`).join("");
    }
    const deleteSelect = document.getElementById("deleteSelect");
    const prevValue = deleteSelect.value;
    deleteSelect.innerHTML = transactions.map((t) => `<option value="${t.id}">${escapeHtml(t.person_name)} • ${t.txn_type} • ${fmtDate(t.txn_date)} • ${fmt(t.principal)} (ID ${t.id})</option>`).join("");
    if ([...deleteSelect.options].some((o) => o.value === prevValue)) deleteSelect.value = prevValue;
    document.getElementById("deletePanel").classList.toggle("hidden", transactions.length === 0);
  }

  function exportCsv() {
    const view = filteredRecords();
    const headers = ["Person", "Phone", "Type", "Date", "DueDate", "DaysElapsed", "Principal", "InterestRate", "RatePeriod", "InterestType", "AccruedInterest", "TotalDue", "Repaid", "Balance", "Status"];
    const rows = view.map((t) => [t.person_name, t.phone, t.txn_type, t.txn_date, t.due_date, t.days_elapsed, t.principal, t.interest_rate, t.rate_period, t.interest_type, t.accrued_interest, t.total_due, t.repaid, t.balance, t.status]);
    const csvEscape = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const csv = [headers, ...rows].map((r) => r.map(csvEscape).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "ledgerly_export.csv";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function initRecordsEvents() {
    document.getElementById("recordsSearch").addEventListener("input", renderRecords);
    document.getElementById("recordsTypeFilter").addEventListener("change", renderRecords);
    document.getElementById("recordsStatusFilter").addEventListener("change", renderRecords);
    document.getElementById("exportCsvBtn").addEventListener("click", exportCsv);
    const confirmBox = document.getElementById("deleteConfirm");
    const deleteBtn = document.getElementById("deleteBtn");
    confirmBox.addEventListener("change", () => { deleteBtn.disabled = !confirmBox.checked; });
    deleteBtn.addEventListener("click", async () => {
      const id = Number(document.getElementById("deleteSelect").value);
      if (!id) return;
      try {
        await api.deleteTransaction(id);
        showToast("Deleted."); confirmBox.checked = false; deleteBtn.disabled = true;
        await refreshAll();
      } catch (err) { showToast("Could not delete: " + err.message, true); }
    });
  }

  // ============================================================
  // Add Entry (with interest live preview)
  // ============================================================
  function initSegmented() {
    document.querySelectorAll(".segmented").forEach((group) => {
      group.addEventListener("click", (e) => {
        const btn = e.target.closest(".segmented-opt");
        if (!btn) return;
        group.querySelectorAll(".segmented-opt").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        updateInterestPreview();
      });
    });
  }
  function segmentedValue(name) {
    const active = document.querySelector(`.segmented[data-name="${name}"] .segmented-opt.is-active`);
    return active ? active.dataset.value : null;
  }
  function resetSegmented() {
    document.querySelectorAll(".segmented").forEach((g) => g.querySelectorAll(".segmented-opt").forEach((b, i) => b.classList.toggle("is-active", i === 0)));
  }

  const DAYS_PER_MONTH = 30.4368, DAYS_PER_YEAR = 365.25;
  function previewInterest(principal, ratePct, ratePeriod, interestType, txnDateStr, dueDateStr) {
    if (!principal || !txnDateStr || !dueDateStr) return { days: 0, interest: 0 };
    const txnDate = new Date(txnDateStr + "T00:00:00");
    const dueDate = new Date(dueDateStr + "T00:00:00");
    if (Number.isNaN(txnDate.getTime()) || Number.isNaN(dueDate.getTime())) return { days: 0, interest: 0 };
    const days = Math.max(Math.round((dueDate - txnDate) / 86400000), 0);
    const months = days / DAYS_PER_MONTH, years = days / DAYS_PER_YEAR;
    const t = ratePeriod === "Monthly" ? months : years;
    const r = (ratePct || 0) / 100;
    let interest = 0;
    if (r > 0 && t > 0) interest = interestType === "Compound" ? principal * ((1 + r) ** t - 1) : principal * r * t;
    return { days, interest: Math.round(interest * 100) / 100 };
  }

  function updateInterestPreview() {
    const amount = parseFloat(document.getElementById("f-amount").value) || 0;
    const rate = parseFloat(document.getElementById("f-rate").value) || 0;
    const ratePeriod = segmentedValue("rate_period");
    const interestType = segmentedValue("interest_type");
    const txnDate = document.getElementById("f-date").value;
    const dueDate = document.getElementById("f-due").value;
    const { days, interest } = previewInterest(amount, rate, ratePeriod, interestType, txnDate, dueDate);
    const rateLabel = `${rate}% / ${ratePeriod === "Monthly" ? "month" : "year"}`;
    const roundedPrincipal = Math.round(amount * 100) / 100;
    document.getElementById("ipPrincipal").textContent = fmt(roundedPrincipal);
    document.getElementById("ipRate").textContent = rateLabel;
    document.getElementById("ipDuration").textContent = dueDate ? `${days} day${days === 1 ? "" : "s"}` : "Set a due date";
    document.getElementById("ipInterest").textContent = fmt(interest);
    document.getElementById("ipTotal").textContent = fmt(Math.round((roundedPrincipal + interest) * 100) / 100);

    // Signature entry preview — keeps the visual card live without changing the API contract.
    const name = document.getElementById("f-person")?.value.trim();
    const due = dueDate;
    const type = segmentedValue("txn_type");
    const previewName = document.getElementById("entryPreviewName");
    const previewAmount = document.getElementById("entryPreviewAmount");
    const previewDirection = document.getElementById("entryPreviewDirection");
    const previewDue = document.getElementById("entryPreviewDue");
    const previewRate = document.getElementById("entryPreviewRate");
    const avatar = document.querySelector(".preview-avatar");
    if (previewName) previewName.textContent = name || "New person";
    if (previewAmount) previewAmount.textContent = fmt(amount);
    if (previewDirection) previewDirection.textContent = type === "Taken" ? "You owe them" : "They owe you";
    if (previewDue) previewDue.textContent = due ? fmtDate(due) : "No due date";
    if (previewRate) previewRate.textContent = `${rate}% / ${ratePeriod === "Monthly" ? "month" : "year"}`;
    if (avatar) avatar.textContent = name ? name.charAt(0).toUpperCase() : "?";
  }

  function initAddForm() {
    const form = document.getElementById("addForm");
    const dateInput = document.getElementById("f-date");
    dateInput.value = todayISO();
    dateInput.max = todayISO();

    ["f-person", "f-amount", "f-rate", "f-date", "f-due", "f-grace"].forEach((id) => {
      document.getElementById(id).addEventListener("input", updateInterestPreview);
      document.getElementById(id).addEventListener("change", updateInterestPreview);
    });
    form.querySelectorAll(".segmented").forEach((group) => {
      group.addEventListener("click", () => requestAnimationFrame(updateInterestPreview));
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const personName = document.getElementById("f-person").value.trim();
      const amount = parseFloat(document.getElementById("f-amount").value);
      if (!personName) return showToast("Person is required.", true);
      if (!(amount > 0)) return showToast("Amount must be greater than zero.", true);

      const existing = people.find((p) => p.name.toLowerCase() === personName.toLowerCase());
      const payload = {
        person_id: existing ? existing.id : undefined,
        person_name: existing ? undefined : personName,
        phone: document.getElementById("f-phone").value.trim(),
        amount, txn_type: segmentedValue("txn_type"), txn_date: document.getElementById("f-date").value,
        due_date: document.getElementById("f-due").value || null,
        grace_period_days: parseInt(document.getElementById("f-grace").value || "2", 10),
        interest_rate: parseFloat(document.getElementById("f-rate").value || "0"),
        rate_period: segmentedValue("rate_period"), interest_type: segmentedValue("interest_type"),
        notes: document.getElementById("f-notes").value.trim(),
      };
      try {
        await api.createTransaction(payload);
        showToast(`Entry added for ${personName}.`);
        form.reset();
        dateInput.value = todayISO();
        resetSegmented();
        updateInterestPreview();
        await refreshAll();
      } catch (err) { showToast("Could not add entry: " + err.message, true); }
    });

    updateInterestPreview();
  }

  // ============================================================
  // Interest Calculator (standalone, hits /api/calculator — no persistence)
  // ============================================================
  function segmentedValueIn(form, name) {
    const active = form.querySelector(`.segmented[data-name="${name}"] .segmented-opt.is-active`);
    return active ? active.dataset.value : null;
  }

  function initCalculator() {
    const form = document.getElementById("calcForm");
    const loanDate = document.getElementById("c-loan-date");
    const dueDate = document.getElementById("c-due-date");
    const paymentDate = document.getElementById("c-payment-date");

    const today = todayISO();
    loanDate.value = addDays(today, -30);
    dueDate.value = today;
    paymentDate.value = today;

    form.querySelectorAll(".segmented").forEach((group) => {
      group.addEventListener("click", (e) => {
        const btn = e.target.closest(".segmented-opt");
        if (!btn) return;
        group.querySelectorAll(".segmented-opt").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        runCalculator();
      });
    });

    form.addEventListener("input", () => runCalculator());
    runCalculator();
  }

  let calcDebounce;
  function runCalculator() {
    clearTimeout(calcDebounce);
    calcDebounce = setTimeout(async () => {
      const form = document.getElementById("calcForm");
      const amount = parseFloat(document.getElementById("c-amount").value) || 0;
      const rate = parseFloat(document.getElementById("c-rate").value) || 0;
      const loan_date = document.getElementById("c-loan-date").value;
      const due_date = document.getElementById("c-due-date").value;
      const grace_period_days = parseInt(document.getElementById("c-grace").value || "0", 10);
      const payment_date = document.getElementById("c-payment-date").value;
      if (!amount || !loan_date || !payment_date) return;
      try {
        const result = await apiFetch("/calculator", {
          method: "POST",
          body: JSON.stringify({
            amount, interest_rate: rate,
            rate_period: segmentedValueIn(form, "c_rate_period"),
            interest_type: segmentedValueIn(form, "c_interest_type"),
            loan_date, due_date: due_date || null, grace_period_days, payment_date,
          }),
        });
        document.getElementById("crPhase").textContent = PHASE_LABEL[result.phase] || result.phase;
        document.getElementById("crPrincipal").textContent = fmt(result.principal);
        document.getElementById("crDays").textContent = `${result.days_elapsed} days`;
        document.getElementById("crInterest").textContent = fmt(result.interest);
        document.getElementById("crOverdue").textContent = `${result.days_overdue} days`;
        document.getElementById("crTotal").textContent = fmt(result.total_payable);
      } catch (err) {
        showToast("Calculator error: " + err.message, true);
      }
    }, 200);
  }

  // ============================================================
  // Reports
  // ============================================================
  async function renderReports() {
    let data;
    try { data = await api.monthlyReport(); } catch (err) { showToast("Could not load report: " + err.message, true); return; }
    const body = document.getElementById("reportsBody");
    const empty = document.getElementById("reportsEmpty");
    if (!data.months.length) { body.innerHTML = ""; empty.classList.remove("hidden"); if (charts.chartMonthly) charts.chartMonthly.destroy(); return; }
    empty.classList.add("hidden");
    body.innerHTML = data.months.map((m) => `
      <tr><td>${m.month}</td><td class="num">${fmt(m.money_given)}</td><td class="num">${fmt(m.money_taken)}</td><td class="num">${fmt(m.repayments)}</td><td class="num">${fmt(m.interest_earned)}</td><td class="num">${fmt(m.interest_paid)}</td></tr>
    `).join("");
    ensureChart("chartMonthly", {
      type: "bar",
      data: {
        labels: data.months.map((m) => m.month),
        datasets: [
          { label: "Given", data: data.months.map((m) => m.money_given), backgroundColor: "#1D7A5F" },
          { label: "Taken", data: data.months.map((m) => m.money_taken), backgroundColor: "#B23A2E" },
          { label: "Repayments", data: data.months.map((m) => m.repayments), backgroundColor: "#A6782C" },
        ],
      },
      options: { plugins: { legend: { position: "bottom" } }, scales: { y: { ticks: { callback: (v) => fmtCompact(v) } } } },
    });
  }

  // ============================================================
  // Global search
  // ============================================================
  function runSearch(query) {
    const q = query.trim().toLowerCase();
    setActiveView("search");
    if (!q) { document.getElementById("searchResults").innerHTML = ""; document.getElementById("searchSummary").textContent = "Type to search."; return; }
    const matchedPeople = people.filter((p) => p.name.toLowerCase().includes(q) || (p.phone || "").includes(q));
    const matchedTxns = transactions.filter((t) => t.person_name.toLowerCase().includes(q) || String(t.principal).includes(q) || (t.notes || "").toLowerCase().includes(q));
    document.getElementById("searchSummary").textContent = `${matchedPeople.length} people, ${matchedTxns.length} transactions matching "${query}"`;
    document.getElementById("searchResults").innerHTML = `
      ${matchedPeople.length ? `<h2>People</h2>` + matchedPeople.map((p) => `
        <div class="person-tile" data-person-id="${p.id}">
          <div class="person-tile-head"><div class="person-name">${escapeHtml(p.name)}</div><div class="person-net">${fmt(p.outstanding)}</div></div>
          <div class="person-tile-meta">${escapeHtml(p.phone || "")}</div>
          <div class="person-tile-actions"><button class="btn btn-sm btn-primary" data-view-person="${p.id}">View Details</button></div>
        </div>`).join("") : ""}
      ${matchedTxns.length ? `<h2 style="margin-top:20px;">Transactions</h2>` + matchedTxns.map(renderTxnBlock).join("") : ""}
      ${!matchedPeople.length && !matchedTxns.length ? `<p class="empty-note">No matches.</p>` : ""}
    `;
  }

  function initSearch() {
    const input = document.getElementById("globalSearch");
    let debounce;
    input.addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => runSearch(input.value), 250); });
    const results = document.getElementById("searchResults");
    bindTxnBlockEvents(results);
    results.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-view-person]");
      if (btn) openPersonDetail(Number(btn.dataset.viewPerson));
    });
  }

  // ============================================================
  // Danger zone
  // ============================================================
  function initDangerZone() {
    document.getElementById("wipeAllBtn").addEventListener("click", async () => {
      if (!window.confirm("This permanently deletes ALL people, transactions and repayments. Continue?")) return;
      try { await api.resetAll(); showToast("All data wiped."); currentPersonId = null; await refreshAll(); }
      catch (err) { showToast("Could not wipe data: " + err.message, true); }
    });
  }

  // ============================================================
  // Init
  // ============================================================
  document.addEventListener("DOMContentLoaded", () => {
    initAuth();
    initNav();
    initSegmented();
    initAddForm();
    initPeopleEvents();
    initPersonDetailEvents();
    initLedgerEvents();
    initRecordsEvents();
    initSearch();
    initDangerZone();
    initCalculator();

    document.getElementById("nav").addEventListener("click", (e) => {
      if (e.target.closest('[data-view="reports"]')) renderReports();
      if (e.target.closest('[data-view="calculator"]')) runCalculator();
    });
    const bottomNav = document.getElementById("bottomNav");
    if (bottomNav) bottomNav.addEventListener("click", (e) => { if (e.target.closest('[data-view="reports"]')) renderReports(); });

    resolveApiBase().then((ok) => {
      if (!ok) { showBackendDownBanner(); return; }
      return bootstrapSession();
    });
  });
})();
