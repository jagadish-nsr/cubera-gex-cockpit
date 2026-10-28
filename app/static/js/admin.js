/**
 * Admin & Data tab. Loaded before cockpit.js so refreshAdminChrome exists at startup.
 * The repair buttons plan, then confirm. They never call the database cleanse script.
 */
(function () {
  "use strict";

  let pollTimer = null;
  let activeTask = null;

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

  async function refreshAdminChrome() {
    try {
      const res = await fetch("/api/admin/status");
      if (!res.ok) return;
      const data = await res.json();
      paintStatus(data);
      loadOps();
    } catch (err) {
      /* status is retried on the next poll */
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

  async function loadOps() {
    const processes = document.getElementById("admin-ops-processes");
    const workflows = document.getElementById("admin-ops-workflows");
    const events = document.getElementById("admin-ops-events");
    if (!processes || !workflows || !events) return;
    try {
      const res = await fetch("/api/admin/ops?limit=40");
      if (!res.ok) return;
      const data = await res.json();
      const http = data.http || {};
      const by = http.by_class || {};
      const httpEl = document.getElementById("admin-ops-http");
      if (httpEl) {
        httpEl.textContent = "HTTP " + (http.count || 0)
          + "  2xx " + (by["2xx"] || 0)
          + "  4xx " + (by["4xx"] || 0)
          + "  5xx " + (by["5xx"] || 0)
          + "  slow " + (http.slow || 0);
      }
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
        return [row.ts, row.level, row.process, row.event, row.symbol || "", row.message || ""].join("  ");
      });
      events.textContent = lines.length ? lines.join("\n") : "No workflow events yet.";
    } catch (err) {
      /* the next admin refresh tries again */
    }
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
