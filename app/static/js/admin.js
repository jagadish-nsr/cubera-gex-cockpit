/**
 * Admin & Data tab. Loaded before cockpit.js so refreshAdminChrome exists at startup.
 * The repair buttons plan, then confirm. They never call the database cleanse script.
 */
(function () {
  "use strict";

  let pollTimer = null;
  let activeTask = null;
  let obsWindow = "15m";

  function isoDate(day) {
    const z = function (n) { return String(n).padStart(2, "0"); };
    return day.getFullYear() + "-" + z(day.getMonth() + 1) + "-" + z(day.getDate());
  }

  function ensureDates() {
    const end = document.getElementById("admin-end");
    const start = document.getElementById("admin-start");
    if (!end || end.value) return;
    const today = new Date();
    const from = new Date(today.getTime() - 6 * 24 * 60 * 60 * 1000);
    end.value = isoDate(today);
    start.value = isoDate(from);
  }

  function paintStatus(data) {
    const tokenEl = document.getElementById("admin-kpi-token");
    if (!tokenEl) return;
    const token = data.token_status || {};
    if (!token.present) tokenEl.textContent = "Missing";
    else if (token.expired) tokenEl.textContent = "Expired";
    else if (token.hours_left == null) tokenEl.textContent = "Unknown";
    else tokenEl.textContent = Number(token.hours_left).toFixed(1) + "h";
    const src = document.getElementById("admin-kpi-token-src");
    if (src) src.textContent = token.source || "source";
    const feed = data.feed_status || {};
    const feedEl = document.getElementById("admin-kpi-feed");
    if (feedEl) {
      feedEl.textContent = feed.last_tick_age_s == null ? "not started" : (feed.last_tick_age_s + "s");
    }
    const wal = document.getElementById("admin-kpi-wal");
    if (wal && data.wal_sizes_mb) wal.textContent = data.wal_sizes_mb.sentinel + " MB";
    const gaps = document.getElementById("admin-kpi-gaps");
    if (gaps) gaps.textContent = String(data.active_gaps_count || 0);
    const alerts = document.getElementById("admin-kpi-alerts");
    if (alerts) alerts.textContent = "Alerts " + (data.active_alerts_count || 0);
  }

  function paintSessionChecklist(data) {
    const host = document.getElementById("admin-session-checklist");
    const verdict = document.getElementById("admin-checklist-verdict");
    if (!host) return;
    const checks = data.checks || [];
    host.innerHTML = checks.map(function (c) {
      const mark = c.skipped ? "·" : (c.ok ? "✓" : "✗");
      const color = c.skipped ? "var(--ink-mute)" : (c.ok ? "var(--go)" : "var(--stop)");
      return "<div style=\"color:" + color + "\">" + mark + " <b>" + (c.label || c.id) + "</b>"
        + " <span style=\"color:var(--ink-mute)\">— " + (c.detail || "") + "</span></div>";
    }).join("");
    if (verdict) {
      if (data.pass) {
        verdict.textContent = "PASS";
        verdict.style.color = "var(--go)";
        verdict.style.background = "var(--go-bg)";
      } else {
        verdict.textContent = "FAIL · " + ((data.failed || []).join(",") || "checks");
        verdict.style.color = "var(--stop)";
        verdict.style.background = "var(--stop-bg, rgba(239,68,68,0.12))";
      }
    }
  }

  async function loadSessionChecklist() {
    try {
      const res = await fetch("/api/admin/session-checklist");
      if (!res.ok) return;
      const data = await res.json();
      paintSessionChecklist(data);
    } catch (err) {
      /* retried with admin chrome poll */
    }
  }

  async function runSessionSmoke() {
    const out = document.getElementById("admin-smoke-result");
    const btn = document.getElementById("admin-run-smoke");
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/admin/session-smoke");
      const data = await res.json().catch(function () { return {}; });
      if (out) {
        out.style.display = "block";
        const status = data.pass ? "PASS" : "FAIL";
        const color = data.pass ? "var(--go)" : "var(--stop)";
        const failed = (data.failed || []).join(",") || "—";
        out.style.color = color;
        out.textContent = "Smoke " + status + " · failed=[" + failed + "]";
      }
      loadSessionChecklist();
    } catch (err) {
      if (out) {
        out.style.display = "block";
        out.style.color = "var(--stop)";
        out.textContent = "Smoke unreachable";
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    const smokeBtn = document.getElementById("admin-run-smoke");
    if (smokeBtn && !smokeBtn._bound) {
      smokeBtn._bound = true;
      smokeBtn.addEventListener("click", function () { runSessionSmoke(); });
    }
  });

  async function refreshAdminChrome() {
    try {
      const res = await fetch("/api/admin/status");
      if (!res.ok) {
        const alerts = document.getElementById("admin-kpi-alerts");
        if (alerts) alerts.textContent = "Status HTTP " + res.status;
        return;
      }
      const data = await res.json();
      paintStatus(data);
      loadOps();
      loadSessionChecklist();
    } catch (err) {
      const alerts = document.getElementById("admin-kpi-alerts");
      if (alerts) alerts.textContent = "Status unreachable";
    }
  }

  function opsCell(text) {
    const td = document.createElement("td");
    td.textContent = text == null || text === "" ? "—" : String(text);
    return td;
  }

  function opsEmpty(body, cols, text) {
    const tr = document.createElement("tr");
    const td = opsCell(text);
    td.colSpan = cols;
    tr.appendChild(td);
    body.appendChild(tr);
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value == null || value === "" ? "—" : String(value);
  }

  function fmtMs(v) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (!isFinite(n)) return "—";
    if (n >= 1000) return (n / 1000).toFixed(1) + "s";
    return Math.round(n) + "ms";
  }

  function countMap(obj) {
    const parts = [];
    Object.keys(obj || {}).forEach(function (key) {
      const n = obj[key] || 0;
      if (n) parts.push(key + " " + n);
    });
    return parts.length ? parts.join(" · ") : "none";
  }

  function paintObs(data) {
    const http = data.http || {};
    const infra = data.infra || {};
    const external = data.external || {};
    const summary = data.events_summary || {};
    const failures = summary.failures || {};
    const volume = summary.volume || {};
    const gate = (external.rate_gate || {}).chain || {};
    const ws = external.spot_ws || {};
    const failTotal = Object.keys(failures).reduce(function (acc, k) { return acc + (failures[k] || 0); }, 0);

    setText("obs-kpi-rpm", http.rpm != null ? http.rpm : "—");
    setText("obs-kpi-err-rpm", http.error_rpm != null ? http.error_rpm : "—");
    setText("obs-kpi-p95", fmtMs(http.p95_ms));
    setText("obs-kpi-inflight", http.in_flight != null ? http.in_flight : "—");
    setText("obs-kpi-lag", fmtMs(infra.loop_lag_p95_ms != null ? infra.loop_lag_p95_ms : http.loop_lag_p95_ms));
    setText("obs-kpi-wd", infra.watchdog_restarts != null ? infra.watchdog_restarts : "—");
    setText("obs-kpi-gate", gate.headroom != null ? gate.headroom : "—");
    setText("obs-kpi-ws", ws.last_tick_age_s != null ? (ws.last_tick_age_s + "s") : "—");
    setText("obs-kpi-cb", infra.cb_open != null ? infra.cb_open : "—");
    setText("obs-kpi-fail", failTotal);

    const by = http.by_class || {};
    const httpEl = document.getElementById("admin-ops-http");
    if (httpEl) {
      httpEl.textContent = "Lifetime " + (data.window || obsWindow)
        + " · lifetime " + (http.count || 0)
        + "  2xx " + (by["2xx"] || 0)
        + "  4xx " + (by["4xx"] || 0)
        + "  5xx " + (by["5xx"] || 0)
        + "  slow " + (http.slow || 0)
        + "  p50 " + fmtMs(http.p50_ms)
        + "  p99 " + fmtMs(http.p99_ms);
    }

    const httpBody = document.getElementById("admin-obs-http-body");
    if (httpBody) {
      httpBody.replaceChildren();
      const routes = http.top_routes || [];
      if (!routes.length) opsEmpty(httpBody, 5, "No HTTP samples in window");
      routes.forEach(function (row) {
        const tr = document.createElement("tr");
        tr.appendChild(opsCell(row.route));
        tr.appendChild(opsCell(row.count));
        tr.appendChild(opsCell(fmtMs(row.p50_ms)));
        tr.appendChild(opsCell(fmtMs(row.p95_ms)));
        tr.appendChild(opsCell(row.error_count));
        httpBody.appendChild(tr);
      });
    }

    const token = external.token || {};
    const sync = external.sync || {};
    const misses = external.egress_misses || {};
    const quote = (external.rate_gate || {}).quote || {};
    setText("admin-obs-external",
      "Token " + (token.present ? ((token.hours_left != null ? Number(token.hours_left).toFixed(1) + "h" : "ok") + (token.expired ? " expired" : "")) : "missing")
      + "\nRate gate chain calls " + (gate.calls != null ? gate.calls : "—")
      + " headroom " + (gate.headroom != null ? gate.headroom : "—")
      + " wait_p95 " + fmtMs(gate.wait_p95_ms)
      + "\nRate gate quote calls " + (quote.calls != null ? quote.calls : "—")
      + " headroom " + (quote.headroom != null ? quote.headroom : "—")
      + "\nSpot WS " + (ws.ws_connected ? "connected" : "down")
      + (ws.ws_error ? (" err=" + ws.ws_error) : "")
      + " age " + (ws.last_tick_age_s != null ? ws.last_tick_age_s + "s" : "—")
      + "\nSync " + (sync.status || "—")
      + " trigger " + (sync.trigger || "—")
      + "\nEgress misses news " + (misses.news_miss || 0)
      + " sectors " + (misses.sectors_miss || 0)
      + " nse_fallback " + (misses.nse_fallback || 0)
    );

    const wal = infra.wal_mb || {};
    const journal = infra.journal_mb || {};
    const threads = infra.chain_threads || {};
    setText("admin-obs-infra",
      "RSS " + (infra.rss_mb != null ? infra.rss_mb + " MB" : "—")
      + " · threads " + (infra.threads != null ? infra.threads : "—")
      + " · in-flight " + (infra.http_in_flight != null ? infra.http_in_flight : "—")
      + "\nWAL sen " + (wal.sentinel != null ? wal.sentinel : "—")
      + " market " + (wal.market_snapshot != null ? wal.market_snapshot : "—")
      + " admin " + (wal.admin_ops != null ? wal.admin_ops : "—")
      + "\nJournal ts " + (journal.timeseries != null ? journal.timeseries : "—")
      + " blast " + (journal.gamma_blast != null ? journal.gamma_blast : "—")
      + " ticks " + (journal.blast_ticks != null ? journal.blast_ticks : "—")
      + "\nLoop lag p95 " + fmtMs(infra.loop_lag_p95_ms)
      + " · watchdog " + (infra.watchdog_armed ? "armed" : "off")
      + " restarts " + (infra.watchdog_restarts || 0)
      + "\nChain threads NIFTY " + (threads.NIFTY ? "alive" : "dark")
      + " BN " + (threads.BANKNIFTY ? "alive" : "dark")
      + " SX " + (threads.SENSEX ? "alive" : "dark")
    );

    const resBody = document.getElementById("admin-obs-resilience-body");
    if (resBody) {
      resBody.replaceChildren();
      const res = data.resilience || {};
      ["NIFTY", "BANKNIFTY", "SENSEX"].forEach(function (sym) {
        const row = res[sym] || {};
        const circuit = row.circuit || {};
        const adaptive = row.adaptive || {};
        const lkgs = row.lkgs || {};
        const tr = document.createElement("tr");
        tr.appendChild(opsCell(sym));
        tr.appendChild(opsCell(circuit.state || "—"));
        tr.appendChild(opsCell(fmtMs(adaptive.rolling_rtt_ms)));
        tr.appendChild(opsCell(lkgs.is_degraded ? ("yes " + Math.round(lkgs.staleness_seconds || 0) + "s") : "no"));
        resBody.appendChild(tr);
      });
    }

    setText("admin-obs-failures", countMap(failures));
    setText("admin-obs-volume", countMap(volume));

    const chainBody = document.getElementById("admin-obs-chain-body");
    if (chainBody) {
      chainBody.replaceChildren();
      const chain = data.chain || {};
      ["NIFTY", "BANKNIFTY", "SENSEX"].forEach(function (sym) {
        const row = chain[sym] || {};
        const tr = document.createElement("tr");
        if (!row.last_ok_at && (row.n || 0) === 0) tr.className = "admin-obs-warn";
        tr.appendChild(opsCell(sym));
        tr.appendChild(opsCell(row.n || 0));
        tr.appendChild(opsCell(fmtMs(row.p50_ms)));
        tr.appendChild(opsCell(fmtMs(row.p95_ms)));
        tr.appendChild(opsCell(fmtMs(row.network_avg_ms)));
        tr.appendChild(opsCell(fmtMs(row.compute_avg_ms)));
        tr.appendChild(opsCell(row.last_ok_at || "—"));
        chainBody.appendChild(tr);
      });
    }
  }

  async function loadOps() {
    const processes = document.getElementById("admin-ops-processes");
    const workflows = document.getElementById("admin-ops-workflows");
    const events = document.getElementById("admin-ops-events");
    if (!processes || !workflows || !events) return;
    try {
      const res = await fetch("/api/admin/ops?limit=40&window=" + encodeURIComponent(obsWindow));
      if (!res.ok) return;
      const data = await res.json();
      paintObs(data);
      processes.replaceChildren();
      const beats = data.heartbeats || [];
      if (!beats.length) opsEmpty(processes, 4, "No process has reported a heartbeat yet");
      beats.forEach(function (row) {
        const tr = document.createElement("tr");
        tr.appendChild(opsCell(row.process));
        tr.appendChild(opsCell(row.pid));
        tr.appendChild(opsCell(row.beat_at));
        tr.appendChild(opsCell(row.last_error_at));
        processes.appendChild(tr);
      });
      workflows.replaceChildren();
      const flows = data.workflows || [];
      if (!flows.length) opsEmpty(workflows, 7, "No workflow has reported yet");
      flows.forEach(function (row) {
        const tr = document.createElement("tr");
        tr.appendChild(opsCell(row.name));
        tr.appendChild(opsCell(row.process));
        tr.appendChild(opsCell(row.last_ok_at));
        tr.appendChild(opsCell(row.last_error_at));
        tr.appendChild(opsCell(row.ok_count));
        tr.appendChild(opsCell(row.error_count));
        tr.appendChild(opsCell(row.last_duration_ms));
        workflows.appendChild(tr);
      });
      const lines = (data.events || []).map(function (row) {
        const fields = row.fields || {};
        const bits = [row.ts, row.level, row.process, row.event, row.symbol || "", row.message || ""];
        if (row.duration_ms != null) bits.push(row.duration_ms + "ms");
        if (fields.network_ms != null) bits.push("net=" + fields.network_ms);
        if (fields.compute_ms != null) bits.push("cmp=" + fields.compute_ms);
        if (fields.lag_s != null) bits.push("lag_s=" + fields.lag_s);
        return bits.join("  ");
      });
      events.textContent = lines.length ? lines.join("\n") : "No workflow events yet.";
    } catch (err) {
      /* the next admin refresh tries again */
    }
  }

  function bindObsWindow() {
    const root = document.getElementById("admin-obs-window");
    if (!root || root._bound) return;
    root._bound = true;
    root.addEventListener("click", function (ev) {
      const btn = ev.target.closest("button[data-window]");
      if (!btn) return;
      obsWindow = btn.getAttribute("data-window") || "15m";
      root.querySelectorAll("button[data-window]").forEach(function (el) {
        el.classList.toggle("is-active", el === btn);
      });
      loadOps();
    });
  }

  function stateNode(status, extra) {
    const span = document.createElement("span");
    span.className = "admin-state admin-state-" + String(status || "none").toLowerCase();
    span.textContent = extra ? (status + " " + extra) : (status || "—");
    return span;
  }

  function addCell(tr, value) {
    const td = document.createElement("td");
    if (typeof value === "string") td.textContent = value;
    else td.appendChild(value);
    tr.appendChild(td);
  }

  async function inspectAudit() {
    ensureDates();
    const params = new URLSearchParams({
      start_date: document.getElementById("admin-start").value,
      end_date: document.getElementById("admin-end").value,
      symbol: document.getElementById("admin-symbol").value,
    });
    const body = document.getElementById("admin-audit-body");
    const note = document.getElementById("admin-calendar-note");
    body.replaceChildren();
    note.textContent = "Inspecting…";
    const res = await fetch("/api/admin/audit?" + params.toString());
    const data = await res.json();
    if (!res.ok) {
      note.textContent = (data && data.detail) || "Audit failed";
      return;
    }
    const summary = data.summary || {};
    note.textContent = (data.calendar_note ? data.calendar_note + ". " : "")
      + summary.trading_days + " trading days, "
      + summary.holidays + " holidays, "
      + summary.weekends + " weekends, "
      + summary.complete_days + " complete, "
      + summary.gapped_days + " partial, "
      + summary.missing_days + " missing, "
      + summary.unfillable_days + " unfillable.";
    (data.days || []).forEach(function (day) {
      const tr = document.createElement("tr");
      addCell(tr, day.date + " " + day.weekday);
      addCell(tr, day.session_kind + (day.holiday_name ? " · " + day.holiday_name : ""));
      const futExtra = day.futures_bars == null
        ? ""
        : (day.futures_bars + (day.futures_expected_bars ? "/" + day.futures_expected_bars : ""));
      addCell(tr, stateNode(day.futures_status, futExtra));
      const hole = (day.missing_segments || [])[0] || "";
      addCell(tr, stateNode(day.options_matrix_status, day.options_matrix_status === "PARTIAL" ? hole : ""));
      addCell(tr, stateNode(day.sentinel_status, day.sentinel_cvd_present ? "cvd" : ""));
      addCell(tr, stateNode(day.bhavcopy_status));
      addCell(tr, stateNode(day.participant_status));
      addCell(tr, repairNode(day));
      body.appendChild(tr);
    });
    syncRepairButtons(data.days || []);
  }

  function repairNode(day) {
    const span = document.createElement("span");
    span.className = "admin-repair";
    const repairs = day.repairs || [];
    if (!repairs.length) {
      span.textContent = "—";
      return span;
    }
    span.textContent = repairs.map(function (item) {
      const name = item.task_type === "backfill_futures" ? "futures" : "options";
      if (item.available) {
        return name + (item.needs_live_confirm ? " (open session)" : "");
      }
      return item.block_reason ? (name + ": " + item.block_reason) : "";
    }).filter(Boolean).join("; ") || "—";
    return span;
  }

  function repairHits(days, taskType) {
    const dates = [];
    let block = "";
    days.forEach(function (day) {
      (day.repairs || []).forEach(function (item) {
        if (item.task_type !== taskType) return;
        if (item.available) dates.push(day.date);
        else if (!block && item.block_reason) block = item.block_reason;
      });
    });
    return { dates: dates, block: block };
  }

  function setRepairButtons(prefix, hit) {
    const plan = document.getElementById("admin-plan-" + prefix);
    const run = document.getElementById("admin-run-" + prefix);
    const enabled = hit.dates.length > 0;
    [plan, run].forEach(function (button) {
      if (!button) return;
      button.disabled = !enabled;
      button.title = enabled
        ? hit.dates.join(", ")
        : (hit.block || "No repairable session in this range");
    });
    return hit;
  }

  function syncRepairButtons(days) {
    const options = setRepairButtons("options", repairHits(days, "backfill_options"));
    const futures = setRepairButtons("futures", repairHits(days, "backfill_futures"));
    const note = document.getElementById("admin-repair-note");
    if (!note) return;
    const lines = [];
    lines.push(options.dates.length
      ? ("Options can fill " + options.dates.join(", ") + ".")
      : ("Options: " + (options.block || "nothing to fill in this range") + "."));
    lines.push(futures.dates.length
      ? ("Futures can fill " + futures.dates.join(", ") + ".")
      : ("Futures: " + (futures.block || "nothing to fill in this range") + "."));
    note.textContent = lines.join(" ");
  }

  async function loadAlerts() {
    const box = document.getElementById("admin-alert-list");
    if (!box) return;
    const res = await fetch("/api/admin/alerts");
    const data = await res.json();
    box.replaceChildren();
    const title = document.createElement("h3");
    title.style.fontSize = "1rem";
    title.style.margin = "0 0 8px";
    title.textContent = "Alerts (" + (data.badge_count || 0) + ")";
    box.appendChild(title);
    const alerts = data.alerts || [];
    if (!alerts.length) {
      const empty = document.createElement("p");
      empty.style.margin = "0";
      empty.textContent = "No open alerts.";
      box.appendChild(empty);
      return;
    }
    alerts.forEach(function (alert) {
      const row = document.createElement("div");
      row.className = "admin-alert-row";
      const sev = document.createElement("b");
      sev.textContent = alert.severity;
      const text = document.createElement("span");
      text.textContent = alert.title + " — " + alert.description;
      row.appendChild(sev);
      row.appendChild(text);
      if (alert.session_date) {
        const inspect = document.createElement("button");
        inspect.type = "button";
        inspect.className = "btn btn-secondary";
        inspect.textContent = "Inspect " + alert.session_date;
        inspect.addEventListener("click", function () {
          document.getElementById("admin-start").value = alert.session_date;
          document.getElementById("admin-end").value = alert.session_date;
          inspectAudit();
        });
        row.appendChild(inspect);
      }
      const dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.className = "btn btn-secondary";
      dismiss.textContent = "Dismiss";
      dismiss.addEventListener("click", async function () {
        await fetch("/api/admin/alerts/ack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ alert_id: alert.alert_id, action: "dismiss" }),
        });
        loadAlerts();
        refreshAdminChrome();
      });
      row.appendChild(dismiss);
      box.appendChild(row);
    });
  }

  function showLog(meta, text) {
    const metaEl = document.getElementById("admin-task-meta");
    const logEl = document.getElementById("admin-task-log");
    if (metaEl) metaEl.textContent = meta;
    if (logEl) logEl.textContent = text;
  }

  async function pollTask() {
    if (!activeTask) return;
    const res = await fetch("/api/admin/tasks/" + encodeURIComponent(activeTask));
    const data = await res.json();
    if (!res.ok) {
      showLog("Task lookup failed", (data && data.detail) || "");
      return;
    }
    showLog(data.task_id + "  " + data.status, (data.log_tail || []).join("\n"));
    if (data.status === "QUEUED" || data.status === "RUNNING") {
      pollTimer = setTimeout(pollTask, 1000);
    } else {
      activeTask = null;
    }
  }

  async function runTask(taskType, dryRun) {
    ensureDates();
    if (pollTimer) clearTimeout(pollTimer);
    const payload = {
      task_type: taskType,
      symbol: document.getElementById("admin-symbol").value,
      start_date: document.getElementById("admin-start").value,
      end_date: document.getElementById("admin-end").value,
      dry_run: dryRun,
      confirm: !dryRun,
      confirm_live_session: document.getElementById("admin-live-confirm").checked,
    };
    const res = await fetch("/api/admin/tasks/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      showLog("Refused", (data && data.detail) || "Request failed");
      return;
    }
    if (dryRun) {
      activeTask = null;
      showLog("Dry run. Nothing was started.", JSON.stringify(data, null, 2));
      return;
    }
    activeTask = data.task_id;
    showLog(data.task_id + "  queued", data.message || "");
    pollTask();
  }

  async function cancelTask() {
    if (!activeTask) {
      showLog("No task to cancel", "");
      return;
    }
    const res = await fetch("/api/admin/tasks/" + encodeURIComponent(activeTask) + "/cancel", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      showLog("Cancel refused", (data && data.detail) || "");
      return;
    }
    pollTask();
  }

  function loadAdmin() {
    ensureDates();
    refreshAdminChrome();
    loadAlerts();
  }

  function bind() {
    bindObsWindow();
    const inspect = document.getElementById("admin-inspect");
    if (inspect) inspect.addEventListener("click", inspectAudit);
    const planOpt = document.getElementById("admin-plan-options");
    const runOpt = document.getElementById("admin-run-options");
    const planFut = document.getElementById("admin-plan-futures");
    const runFut = document.getElementById("admin-run-futures");
    const cancel = document.getElementById("admin-cancel-task");
    if (planOpt) planOpt.addEventListener("click", function () { runTask("backfill_options", true); });
    if (runOpt) runOpt.addEventListener("click", function () { runTask("backfill_options", false); });
    if (planFut) planFut.addEventListener("click", function () { runTask("backfill_futures", true); });
    if (runFut) runFut.addEventListener("click", function () { runTask("backfill_futures", false); });
    if (cancel) cancel.addEventListener("click", cancelTask);
  }

  window.refreshAdminChrome = refreshAdminChrome;
  window.loadAdmin = loadAdmin;
  window.inspectAudit = inspectAudit;
  window.runAdminTask = runTask;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
