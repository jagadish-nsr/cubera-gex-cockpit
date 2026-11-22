/**
 * CryptoCubera cockpit client — snapshot + GEX canvas + flag toggles.
 * Advisory UI only; no order placement.
 */

const $ = (sel, root = document) => root.querySelector(sel);

/** @type {object|null} */
let lastGex = null;
/** @type {ReturnType<typeof setInterval>|null} */
let validTimer = null;
let resizeTimer = null;
/** @type {boolean} */
let assetSwitchInFlight = false;
/** @type {ReturnType<typeof setTimeout>|null} */
let assetFollowupTimer = null;
/** @type {Record<string, true>} */
const seenFlowAlertIds = Object.create(null);
/** @type {boolean} */
let flowAlertsSeeded = false;
/** @type {number} */
let lastFlowAlertSoundAt = 0;

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

function fmt(n, digits = 0) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(n) {
  if (n == null) return "—";
  return `${(Number(n) * 100).toFixed(3)}%`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Turn snake/kebab slugs into short display labels. */
function humanize(s) {
  if (s == null || s === "") return "—";
  return String(s)
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^\w/, (c) => c.toUpperCase());
}

function riskReward(entryZone, target, stop) {
  const mid = Array.isArray(entryZone)
    ? (Number(entryZone[0]) + Number(entryZone[1] ?? entryZone[0])) / 2
    : Number(entryZone);
  const t = Number(target);
  const s = Number(stop);
  if (![mid, t, s].every((x) => Number.isFinite(x)) || mid === s) return null;
  const reward = Math.abs(t - mid);
  const risk = Math.abs(mid - s);
  if (risk <= 0) return null;
  return reward / risk;
}

function drawGex(canvas, gex) {
  if (!canvas || !gex?.by_strike?.length) return;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 900;
  const cssH = Math.min(420, Math.max(280, cssW * 0.38));
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  canvas.style.height = `${cssH}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const rows = gex.by_strike;
  const pad = { t: 28, r: 18, b: 40, l: 18 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  const xs = rows.map((r) => r.strike);
  const nets = rows.map((r) => r.net_gex);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const maxAbs = Math.max(...nets.map((v) => Math.abs(v)), 1);

  const xOf = (strike) => pad.l + ((strike - minX) / (maxX - minX || 1)) * w;
  const yOf = (net) => pad.t + h / 2 - (net / maxAbs) * (h / 2) * 0.92;

  // Soft zero band
  ctx.fillStyle = "rgba(232, 238, 244, 0.03)";
  ctx.fillRect(pad.l, pad.t + h / 2 - 1, w, 2);

  ctx.strokeStyle = "rgba(232, 238, 244, 0.12)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t + h / 2);
  ctx.lineTo(pad.l + w, pad.t + h / 2);
  ctx.stroke();

  const bw = Math.max(5, (w / rows.length) * 0.55);
  rows.forEach((r) => {
    const x = xOf(r.strike);
    const y0 = pad.t + h / 2;
    const y1 = yOf(r.net_gex);
    const pos = r.net_gex >= 0;
    ctx.fillStyle = pos ? "rgba(46, 196, 182, 0.78)" : "rgba(217, 119, 58, 0.78)";
    ctx.fillRect(x - bw / 2, Math.min(y0, y1), bw, Math.abs(y1 - y0) || 1);
  });

  const markers = [
    { k: gex.spot, c: "#e8eef4", label: "spot", dash: [] },
    { k: gex.flip, c: "#e6b84d", label: "flip", dash: [4, 4] },
    { k: gex.call_wall, c: "#2ec4b6", label: "call", dash: [4, 4] },
    { k: gex.put_wall, c: "#d9773a", label: "put", dash: [4, 4] },
  ].filter((m) => m.k != null);

  markers.forEach((m) => {
    const x = xOf(m.k);
    ctx.strokeStyle = m.c;
    ctx.globalAlpha = 0.85;
    ctx.setLineDash(m.dash);
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    ctx.fillStyle = m.c;
    ctx.font = "600 11px JetBrains Mono, monospace";
    ctx.fillText(m.label, x + 4, pad.t + 12);
  });

  ctx.fillStyle = "rgba(143, 163, 181, 0.95)";
  ctx.font = "500 11px JetBrains Mono, monospace";
  ctx.fillText(fmt(minX), pad.l, cssH - 12);
  const maxLabel = fmt(maxX);
  ctx.fillText(maxLabel, pad.l + w - ctx.measureText(maxLabel).width, cssH - 12);
}

function renderFlow(orderflow, gex) {
  const grid = $("#flow-grid");
  if (!grid || !orderflow) return;
  const agree = gex?.models_agree;
  const signCls = (s) =>
    s === "positive" ? "agree" : s === "negative" ? "disagree" : "neutral";
  const cells = [
    { k: "Convention", v: humanize(gex?.convention_sign || "—"), cls: signCls(gex?.convention_sign) },
    { k: "Flow model", v: humanize(gex?.flow_sign || "—"), cls: signCls(gex?.flow_sign) },
    {
      k: "Agreement",
      v: agree == null ? "—" : agree ? "agree" : "disagree",
      cls: agree ? "agree" : agree === false ? "disagree" : "neutral",
    },
    {
      k: "CVD 5m",
      v: fmt(orderflow.cvd_5m, 1),
      cls: orderflow.cvd_5m >= 0 ? "agree" : "disagree",
    },
    {
      k: "CVD 15m",
      v: fmt(orderflow.cvd_15m, 1),
      cls: orderflow.cvd_15m >= 0 ? "agree" : "disagree",
    },
    {
      k: "Book imb.",
      v: fmt(orderflow.book_imbalance, 2),
      cls: orderflow.book_imbalance >= 0 ? "agree" : "disagree",
    },
    { k: "Funding", v: fmtPct(orderflow.funding_rate), cls: "neutral" },
    { k: "Perp mark", v: fmt(orderflow.mark, 1), cls: "neutral" },
  ];
  grid.innerHTML = cells
    .map(
      (c) =>
        `<div class="flow-tile"><span class="flow-k">${escapeHtml(c.k)}</span><span class="flow-v ${c.cls}">${escapeHtml(c.v)}</span></div>`
    )
    .join("");
}

/**
 * Mode badge: Advisory / Paper / Manual Decision.
 * Never show Live Auto / Live Confirm as a trading mode — Manual Decision is
 * decision support for hand placement (no exchange POST from Confirm).
 */
function resolveModeDisplay(data, exec) {
  const mode = (data && data.mode) || "advisory";
  if (mode === "manual_decision") {
    return { text: "Manual Decision", mode: "manual_decision", isManual: true, isPaper: false };
  }
  if (mode === "paper_confirm") {
    return { text: "Paper", mode: "paper_confirm", isManual: false, isPaper: true };
  }
  const server = (data && data.mode_badge) || "Advisory";
  // Demote legacy Live* badges — never imply Live Auto.
  if (/^live\b/i.test(String(server).trim())) {
    return { text: "Manual Decision", mode: "manual_decision", isManual: true, isPaper: false };
  }
  if (/^paper\b/i.test(String(server).trim())) {
    return { text: "Paper", mode: "paper_confirm", isManual: false, isPaper: true };
  }
  return {
    text: server || "Advisory",
    mode: mode === "live_confirm" ? "manual_decision" : mode,
    isManual: mode === "live_confirm",
    isPaper: false,
  };
}

function paperStatusLine(signal, exec) {
  const ps = signal.paper_status || {};
  const disp = resolveModeDisplay(
    { mode: signal.exec_mode, mode_badge: "" },
    exec
  );
  const label = disp.isManual ? "Manual" : disp.isPaper ? "Paper" : "Advisory";
  const latest = ps.latest_open;
  if (latest) {
    return `${label} · ${latest.status} · ${latest.instrument} · $${fmt(latest.size_usd, 0)} · ${latest.adapter || "sim"}`;
  }
  if (signal.can_confirm) {
    if (disp.isManual) {
      return "Manual Decision ready · copy levels / export JSON · place by hand on Delta";
    }
    return `Paper Confirm ready · adapter ${ps.adapter || "sim"} · open ${ps.open_intents ?? 0}`;
  }
  if (ps.open_intents) {
    return `${label} · ${ps.open_intents} open intent(s) · Confirm off`;
  }
  return "Advisory · no open intent";
}

function decisionLevelsText(signal) {
  const entry = Array.isArray(signal.entry_zone)
    ? signal.entry_zone.map((x) => fmt(x, 0)).join(" – ")
    : fmt(signal.entry_zone, 0);
  const side = humanize(signal.side || "—").toUpperCase();
  const lines = [
    `${signal.instrument || "—"} ${side}`,
    `Entry: ${entry}`,
    `Target: ${signal.target != null ? fmt(signal.target, 0) : "—"}`,
    `Stop: ${signal.stop != null ? fmt(signal.stop, 0) : "—"}`,
    `Size cap: $${fmt(signal.size_cap_usd, 0)}`,
    `Score: ${signal.score ?? 0}`,
  ];
  if (signal.valid_until) lines.push(`Valid until: ${signal.valid_until}`);
  if (signal.invalidation) lines.push(`Invalidation: ${signal.invalidation}`);
  const reasons = (signal.reasons || []).filter(Boolean).slice(0, 3);
  if (reasons.length) {
    lines.push("Reasons:");
    reasons.forEach((r, i) => lines.push(`  ${i + 1}. ${r}`));
  }
  lines.push("");
  lines.push(
    "Manual Decision — place by hand on Delta. CryptoCubera does not auto-execute."
  );
  return lines.join("\n");
}

function decisionExportObject(signal) {
  return {
    mode: "manual_decision",
    status: signal.status,
    setup: signal.setup || "",
    instrument: signal.instrument || "",
    side: (signal.side || "").toLowerCase(),
    entry_zone: signal.entry_zone,
    target: signal.target,
    stop: signal.stop,
    size_cap_usd: signal.size_cap_usd,
    score: signal.score,
    reasons: (signal.reasons || []).slice(0, 3),
    invalidation: signal.invalidation || "",
    valid_until: signal.valid_until,
    note: "Manual Decision — place by hand on Delta. CryptoCubera does not auto-execute.",
  };
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
}

function downloadJson(filename, obj) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function validUntilState(iso) {
  if (!iso) return { label: "—", cls: "" };
  const end = new Date(iso).getTime();
  if (!Number.isFinite(end)) return { label: String(iso), cls: "" };
  const ms = end - Date.now();
  if (ms <= 0) return { label: "Expired", cls: "expired" };
  const mins = Math.floor(ms / 60000);
  const secs = Math.floor((ms % 60000) / 1000);
  const label =
    mins >= 60
      ? `${Math.floor(mins / 60)}h ${mins % 60}m left`
      : mins > 0
        ? `${mins}m ${secs}s left`
        : `${secs}s left`;
  return { label, cls: mins < 5 ? "expiring" : "" };
}

function renderCard(signal, exec) {
  const mount = $("#go-card-mount");
  if (!mount || !signal) return;
  const isGo = signal.status === "GO";
  const canConfirm = !!signal.can_confirm && isGo;
  const entry = Array.isArray(signal.entry_zone)
    ? signal.entry_zone.map((x) => fmt(x, 0)).join(" – ")
    : fmt(signal.entry_zone, 0);
  const modeDisp = resolveModeDisplay(
    {
      mode: signal.exec_mode || exec?.exec_mode?.mode,
      mode_badge: exec?.exec_mode?.badge || "",
    },
    exec
  );
  const isManual = !!modeDisp.isManual;
  const confirmLabel = canConfirm
    ? isManual
      ? "Log decision"
      : "Confirm paper"
    : "Confirm disabled";
  const confirmTitle = canConfirm
    ? isManual
      ? "Log Manual Decision to the ledger — does not place on Delta"
      : "Submit paper/sim bracket (demo/sim only)"
    : "Enable exec.paper + exec.confirm for Paper, or exec.live + exec.confirm for Manual Decision";
  const rr = riskReward(signal.entry_zone, signal.target, signal.stop);
  const rrPct = rr == null ? 0 : Math.min(100, Math.round((rr / 3) * 100));
  const until = validUntilState(signal.valid_until);
  const vetoes = (signal.reasons || []).filter(Boolean);
  const showVetoes = !isGo && vetoes.length;
  const reasonsGo = isGo ? vetoes.slice(0, 3) : [];

  mount.innerHTML = `
    <article class="go-card ${isGo ? "" : "is-nogo"}${isManual ? " is-manual" : ""}">
      <div class="go-card-top">
        <div>
          <span class="go-badge ${isGo ? "go" : "nogo"}">${escapeHtml(signal.status)}</span>
          ${isManual ? `<span class="go-mode-chip">Manual Decision</span>` : ""}
          <h3 class="go-name">${escapeHtml(humanize(signal.setup || "No setup"))}</h3>
          <p class="go-thesis">${escapeHtml(signal.instrument || "—")} · ${escapeHtml(humanize(signal.side || "—"))} · score ${escapeHtml(signal.score ?? 0)}</p>
        </div>
        <div class="go-valid ${until.cls}" id="go-valid">
          Valid until
          <strong>${escapeHtml(until.label)}</strong>
        </div>
      </div>
      <p class="paper-status" id="paper-status">${escapeHtml(paperStatusLine(signal, exec))}</p>
      <div class="go-legs">
        <div class="go-leg entry"><span class="go-leg-k">Entry</span><span class="go-leg-v">${escapeHtml(entry)}</span></div>
        <div class="go-leg stop"><span class="go-leg-k">Stop</span><span class="go-leg-v">${fmt(signal.stop, 0)}</span></div>
        <div class="go-leg target"><span class="go-leg-k">Target</span><span class="go-leg-v">${fmt(signal.target, 0)}</span></div>
      </div>
      <div class="go-rr">
        <div>
          <div class="go-rr-k">Risk : reward</div>
          <div class="go-rr-v">${rr == null ? "—" : `1 : ${rr.toFixed(2)}`}</div>
        </div>
        <div class="go-rr-bar" aria-hidden="true"><div class="go-rr-fill" style="width:${rrPct}%"></div></div>
        <div>
          <div class="go-rr-k">Size</div>
          <div class="go-rr-v">$${fmt(signal.size_cap_usd, 0)}</div>
        </div>
      </div>
      ${
        showVetoes
          ? `<ul class="go-vetoes">${vetoes.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}${
              signal.invalidation
                ? `<li>Invalidation: ${escapeHtml(signal.invalidation)}</li>`
                : ""
            }</ul>`
          : signal.invalidation
            ? `<ul class="go-vetoes"><li>Invalidation: ${escapeHtml(signal.invalidation)}</li></ul>`
            : reasonsGo.length
              ? `<ul class="go-reasons">${reasonsGo
                  .map((r) => `<li>${escapeHtml(r)}</li>`)
                  .join("")}</ul>`
              : ""
      }
      <div class="go-actions">
        <button class="btn" type="button" id="copy-levels" title="Copy instrument, side, entry, target, stop, size, score, reasons">Copy levels</button>
        <button class="btn" type="button" id="export-decision-json" title="Download Manual Decision JSON for hand placement">Export JSON</button>
        <button class="btn btn-primary" type="button" id="confirm-paper" ${canConfirm ? "" : "disabled"} title="${escapeHtml(confirmTitle)}">${escapeHtml(confirmLabel)}</button>
        <button class="btn" type="button" id="dismiss-card">Dismiss</button>
      </div>
      <p class="confirm-feedback" id="confirm-feedback" hidden></p>
    </article>
  `;

  // Animate RR fill after paint
  requestAnimationFrame(() => {
    const fill = mount.querySelector(".go-rr-fill");
    if (fill) fill.style.width = `${rrPct}%`;
  });

  $("#dismiss-card")?.addEventListener("click", () => {
    mount.innerHTML = `<p class="section-sub">Card dismissed. Refresh to reload the latest signal.</p>`;
    if (validTimer) {
      clearInterval(validTimer);
      validTimer = null;
    }
  });

  $("#copy-levels")?.addEventListener("click", async () => {
    const fb = $("#confirm-feedback");
    try {
      await copyText(decisionLevelsText(signal));
      if (fb) {
        fb.hidden = false;
        fb.textContent = "Levels copied — paste into Delta when placing by hand.";
      }
    } catch (err) {
      if (fb) {
        fb.hidden = false;
        fb.textContent = String(err.message || err);
      }
    }
  });

  $("#export-decision-json")?.addEventListener("click", () => {
    const fb = $("#confirm-feedback");
    const obj = decisionExportObject(signal);
    const name = `manual-decision-${(signal.instrument || "card").replace(/\W+/g, "")}.json`;
    downloadJson(name, obj);
    if (fb) {
      fb.hidden = false;
      fb.textContent = `Exported ${name} — place by hand on Delta; no auto-execute.`;
    }
  });

  $("#confirm-paper")?.addEventListener("click", async () => {
    const fb = $("#confirm-feedback");
    try {
      const result = await api("/api/exec/confirm", {
        method: "POST",
        body: JSON.stringify({ actor: "cockpit-ui" }),
      });
      if (fb) {
        fb.hidden = false;
        if (result.exchange_post === false || result.decision) {
          const t = result.trade || {};
          fb.textContent = `Decision logged · ${t.client_order_id || ""} · $${fmt(t.size_usd, 0)} · place by hand (no exchange order)`;
        } else {
          const t = result.trade || {};
          fb.textContent = `Paper ${t.status || "submitted"} · ${t.client_order_id || ""} · $${fmt(t.size_usd, 0)} · ${t.adapter || "sim"}`;
        }
      }
      await loadSnapshot();
    } catch (err) {
      if (fb) {
        fb.hidden = false;
        fb.textContent = String(err.message || err);
      }
      console.error(err);
    }
  });
  void exec;

  if (validTimer) clearInterval(validTimer);
  if (signal.valid_until) {
    validTimer = setInterval(() => {
      const el = $("#go-valid");
      if (!el) return;
      const next = validUntilState(signal.valid_until);
      el.className = `go-valid ${next.cls}`;
      const strong = el.querySelector("strong");
      if (strong) strong.textContent = next.label;
    }, 1000);
  }
}

function renderBlackout(intelMeta) {
  const banner = $("#blackout-banner");
  const copy = $("#blackout-copy");
  if (!banner) return;
  const blackout = intelMeta?.blackout;
  if (blackout?.active) {
    banner.hidden = false;
    if (copy) {
      copy.textContent =
        (blackout.reasons || []).slice(0, 2).join(" · ") || "IMMINENT window — signal veto active";
    }
  } else {
    banner.hidden = true;
  }
}

function renderNews(news) {
  const list = $("#intel-list");
  if (!list) return;
  if (!news?.length) {
    list.innerHTML = `<li class="intel-empty">No scored events in the current window.</li>`;
    return;
  }
  const tierClass = (t) => {
    const u = String(t || "WATCH").toUpperCase();
    if (u === "IMMINENT") return "imminent";
    if (u === "BREAKING") return "breaking";
    return "watch";
  };
  list.innerHTML = news
    .map((e, i) => {
      const tier = String(e.tier || "WATCH").toUpperCase();
      const url = typeof e.url === "string" && e.url.startsWith("http") ? e.url : "";
      const headline = url
        ? `<a class="intel-headline-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(e.title)}</a>`
        : `<p class="intel-headline">${escapeHtml(e.title)}</p>`;
      const readMore = url
        ? `<a class="intel-read" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Read</a>`
        : "";
      return `
      <li class="intel-item" style="animation-delay:${0.02 + i * 0.04}s">
        <span class="intel-tier ${tierClass(tier)}">${escapeHtml(tier)}</span>
        <div class="intel-body">
          ${headline}
          <p class="intel-meta">${escapeHtml(e.event_type || "event")} · ${escapeHtml(
            e.impact_note || e.source || "—"
          )} · score ${Number(e.score || 0).toFixed(2)}</p>
        </div>
        <div class="intel-action">${readMore || escapeHtml(e.engine_action || "")}</div>
      </li>`;
    })
    .join("");
}

function renderFurtherReading(intel) {
  const wrap = $("#further-reading");
  const list = $("#further-reading-list");
  if (!wrap || !list) return;
  const links = Array.isArray(intel?.further_reading) ? intel.further_reading : [];
  if (!links.length) {
    wrap.hidden = true;
    list.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  list.innerHTML = links
    .map((row) => {
      const url = String(row.url || "");
      if (!url.startsWith("http")) return "";
      const note = row.note ? `<span class="further-reading-note">${escapeHtml(row.note)}</span>` : "";
      return `<li><a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(
        row.title || url
      )}</a>${note}</li>`;
    })
    .filter(Boolean)
    .join("");
}

function showToast(message, tone = "neutral") {
  const host = $("#toast-host");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast toast-${tone}`;
  el.textContent = message;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 280);
  }, 4200);
}

function playFlowAlertTone(bullish) {
  const now = Date.now();
  if (now - lastFlowAlertSoundAt < 2500) return;
  lastFlowAlertSoundAt = now;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = bullish ? 880 : 420;
    gain.gain.value = 0.0001;
    osc.connect(gain);
    gain.connect(ctx.destination);
    const t0 = ctx.currentTime;
    gain.gain.exponentialRampToValueAtTime(0.05, t0 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.28);
    osc.start(t0);
    osc.stop(t0 + 0.3);
    osc.onended = () => ctx.close().catch(() => {});
  } catch {
    /* audio optional */
  }
}

function renderFlowAlerts(alerts, underlying) {
  const list = $("#flow-alert-list");
  if (!list) return;
  const rows = Array.isArray(alerts) ? alerts : [];
  if (!rows.length) {
    list.innerHTML = `<li class="flow-alert-empty">No crypto tape alerts for ${escapeHtml(
      underlying || "—"
    )} right now.</li>`;
    return;
  }

  if (!flowAlertsSeeded) {
    rows.forEach((a) => {
      if (a?.alert_id) seenFlowAlertIds[a.alert_id] = true;
    });
    flowAlertsSeeded = true;
  } else {
    for (const a of rows) {
      const id = a?.alert_id;
      if (!id || seenFlowAlertIds[id]) continue;
      seenFlowAlertIds[id] = true;
      const tone = a.bullish ? "agree" : a.bearish ? "disagree" : "neutral";
      showToast(`${a.title || a.type} · score ${a.score}`, tone);
      playFlowAlertTone(!!a.bullish);
    }
  }

  list.innerHTML = rows
    .map((a, i) => {
      const sideClass = a.bullish ? "bull" : a.bearish ? "bear" : "neutral";
      const drivers = (a.drivers || []).slice(0, 3).join(" · ");
      return `
      <li class="flow-alert-item ${sideClass}" style="animation-delay:${0.02 + i * 0.04}s">
        <span class="flow-alert-type">${escapeHtml(a.type || "ALERT")}</span>
        <div class="flow-alert-body">
          <p class="flow-alert-title">${escapeHtml(a.title || a.type)}</p>
          <p class="flow-alert-detail">${escapeHtml(a.detail || "")}</p>
          <p class="flow-alert-meta">${escapeHtml(a.underlying || "")} · score ${escapeHtml(
            String(a.score ?? "—")
          )}${drivers ? ` · ${escapeHtml(drivers)}` : ""}</p>
        </div>
      </li>`;
    })
    .join("");
}

const FLAG_GROUPS = [
  { id: "assets", title: "Assets", match: (k) => k.startsWith("assets.") },
  { id: "feeds", title: "Feeds", match: (k) => k.startsWith("feeds.") },
  { id: "engine", title: "Engine", match: (k) => k.startsWith("engine.") },
  { id: "intel", title: "Intelligence", match: (k) => k.startsWith("intel.") },
  { id: "ui", title: "Interface", match: (k) => k.startsWith("ui.") },
  {
    id: "exec",
    title: "Execution",
    match: (k) => k.startsWith("exec.") || k.startsWith("paper."),
  },
  {
    id: "other",
    title: "Other",
    match: (k) =>
      !k.startsWith("assets.") &&
      !k.startsWith("feeds.") &&
      !k.startsWith("engine.") &&
      !k.startsWith("intel.") &&
      !k.startsWith("ui.") &&
      !k.startsWith("exec.") &&
      !k.startsWith("paper."),
  },
];

function spotDigits(underlying) {
  const u = String(underlying || "BTC").toUpperCase();
  if (u === "SOL") return 2;
  if (u === "ETH") return 1;
  return 1;
}

function setAssetSwitcherBusy(mount, busy, pendingAsset) {
  if (!mount) return;
  mount.classList.toggle("is-switching", !!busy);
  mount.setAttribute("aria-busy", busy ? "true" : "false");
  if (busy && pendingAsset) {
    mount.dataset.pending = pendingAsset;
  } else {
    delete mount.dataset.pending;
  }
}

function renderAssetSwitcher(data) {
  const mount = $("#asset-switcher");
  if (!mount) return;
  const flags = data.flags || {};
  if (!flags["ui.asset_switcher"]) {
    mount.hidden = true;
    mount.innerHTML = "";
    return;
  }
  const assets = Array.isArray(data.assets) ? data.assets : [];
  if (!assets.length) {
    mount.hidden = true;
    return;
  }
  const active = data.underlying || "BTC";
  const pending = mount.dataset.pending || "";
  const busy = assetSwitchInFlight;
  mount.hidden = false;
  setAssetSwitcherBusy(mount, busy, pending || null);
  mount.innerHTML = assets
    .map((a) => {
      const sym = a.symbol || "";
      const on = !!a.enabled;
      const pressed = busy && pending ? sym === pending : sym === active;
      const disable = !on || busy;
      return `<button type="button" class="asset-btn" data-asset="${escapeHtml(sym)}"
        aria-pressed="${pressed}" ${disable ? "disabled" : ""}
        title="${
          busy
            ? `Switching to ${escapeHtml(pending || active)}…`
            : on
              ? `Switch to ${escapeHtml(sym)}`
              : `${escapeHtml(sym)} disabled (assets.${String(sym).toLowerCase()})`
        }">${escapeHtml(sym)}</button>`;
    })
    .join("");

  mount.querySelectorAll(".asset-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (assetSwitchInFlight) return;
      if (btn.disabled || btn.getAttribute("aria-pressed") === "true") return;
      const underlying = btn.getAttribute("data-asset");
      if (!underlying) return;
      assetSwitchInFlight = true;
      setAssetSwitcherBusy(mount, true, underlying);
      // Optimistic hero labels so the desk sees the flip immediately.
      const ul = $("#underlying-label");
      const spot = $("#spot-value");
      if (ul) ul.textContent = underlying;
      if (spot) spot.textContent = "…";
      mount.querySelectorAll(".asset-btn").forEach((b) => {
        b.disabled = true;
        b.setAttribute("aria-pressed", b.getAttribute("data-asset") === underlying ? "true" : "false");
      });
      try {
        const switched = await api("/api/underlying", {
          method: "PATCH",
          body: JSON.stringify({ underlying, actor: "cockpit-ui" }),
        });
        if (switched?.spot != null && spot) {
          spot.textContent = fmt(switched.spot, spotDigits(underlying));
        }
        // Clear busy before render so the switcher re-enables.
        assetSwitchInFlight = false;
        setAssetSwitcherBusy(mount, false, null);
        // Reseed flow-alert toast IDs per underlying (crypto desk only).
        Object.keys(seenFlowAlertIds).forEach((k) => delete seenFlowAlertIds[k]);
        flowAlertsSeeded = false;
        // Prefer embedded snapshot from PATCH (avoids a second HTTP RTT).
        if (switched?.snapshot) {
          applySnapshot(switched.snapshot);
        } else {
          await loadSnapshot();
        }
        // Background feed realign may upgrade fixture → live; quiet follow-up.
        if (assetFollowupTimer) clearTimeout(assetFollowupTimer);
        assetFollowupTimer = setTimeout(() => {
          loadSnapshot().catch(() => {});
        }, 1800);
      } catch (err) {
        console.error(err);
        assetSwitchInFlight = false;
        setAssetSwitcherBusy(mount, false, null);
        await loadSnapshot().catch(() => {});
      } finally {
        assetSwitchInFlight = false;
        setAssetSwitcherBusy($("#asset-switcher"), false, null);
      }
    });
  });
}

function renderFlags(flags) {
  const grid = $("#flag-grid");
  if (!grid || !flags) return;
  const keys = Object.keys(flags).sort();

  const groups = FLAG_GROUPS.map((g) => ({
    ...g,
    keys: keys.filter(g.match),
  })).filter((g) => g.keys.length);

  grid.innerHTML = groups
    .map((g, gi) => {
      const onCount = g.keys.filter((k) => flags[k]).length;
      return `
      <section class="flag-group" style="animation-delay:${gi * 0.05}s">
        <div class="flag-group-head">
          <h3 class="flag-group-title">${escapeHtml(g.title)}</h3>
          <span class="flag-group-count">${onCount}/${g.keys.length} on</span>
        </div>
        <div class="flag-group-body">
          ${g.keys
            .map((name) => {
              const on = !!flags[name];
              return `
              <div class="flag-row">
                <span class="flag-name">${escapeHtml(name)}</span>
                <button type="button" class="flag-toggle" role="switch"
                  aria-checked="${on}" aria-pressed="${on}"
                  data-flag="${escapeHtml(name)}"
                  title="Toggle ${escapeHtml(name)}"></button>
              </div>`;
            })
            .join("")}
        </div>
      </section>`;
    })
    .join("");

  grid.querySelectorAll(".flag-toggle").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.getAttribute("data-flag");
      const next = btn.getAttribute("aria-pressed") !== "true";
      btn.setAttribute("aria-pressed", String(next));
      btn.setAttribute("aria-checked", String(next));
      try {
        await api("/api/flags", {
          method: "PATCH",
          body: JSON.stringify({ name, value: next, actor: "operator-ui" }),
        });
        await loadSnapshot();
      } catch (err) {
        console.error(err);
        btn.setAttribute("aria-pressed", String(!next));
        btn.setAttribute("aria-checked", String(!next));
      }
    });
  });
}

function setSection(id, on) {
  const el = $(id);
  if (!el) return;
  const wasHidden = el.hidden;
  el.hidden = !on;
  if (on && wasHidden) el.classList.add("reveal");
}

function setLivePill(source) {
  const pill = $("#source-pill");
  if (!pill) return;
  const label = source || "—";
  pill.textContent = label;
  const live = /live|ws|websocket|binance|bybit|okx/i.test(label) && !/fixture|mock|sample/i.test(label);
  pill.dataset.live = live ? "true" : "false";
}

function wireKillSwitch(exec) {
  const btn = $("#kill-btn");
  if (!btn) return;
  const enabled = !!exec?.kill_switch_enabled;
  btn.disabled = !enabled;
  btn.classList.toggle("is-armed", !!exec?.kill_switch_armed);
  if (!enabled) {
    btn.textContent = "Kill switch";
    btn.title =
      "Enable exec.paper (+ exec.confirm) for paper kill switch. Manual Decision does not place live orders.";
    return;
  }
  if (exec.kill_switch_armed) {
    btn.textContent = "Confirm kill";
    btn.title = "Second tap — cancel open intents";
  } else {
    btn.textContent = "Kill switch";
    btn.title = "First tap arms; second tap cancels open intents";
  }
}

function applySnapshot(data) {
  const flags = data.flags || {};
  const exec = data.execution || {};

  const modeDisp = resolveModeDisplay(data, exec);
  const badge = $("#mode-badge");
  if (badge) {
    badge.textContent = modeDisp.text;
    badge.dataset.mode = modeDisp.mode;
  }
  const underlying = data.underlying || "BTC";
  $("#underlying-label").textContent = underlying;
  $("#spot-value").textContent = fmt(data.spot, spotDigits(underlying));
  renderAssetSwitcher(data);
  setLivePill(data.chain?.source || data.providers?.option_chain || "—");

  const paperPill = $("#paper-pill");
  if (paperPill) {
    const paperOn = !!flags["exec.paper"];
    const liveOn = !!flags["exec.live"];
    paperPill.hidden = !(paperOn || liveOn);
    if (paperOn || liveOn) {
      const adapter = exec.adapter || (modeDisp.isManual ? "manual" : "sim");
      const kind = modeDisp.isManual ? "manual" : paperOn ? "paper" : "advisory";
      paperPill.textContent = `${kind} · ${adapter} · open ${exec.open_intents ?? 0}`;
    }
  }

  const status = data.signal?.status || "NO-GO";
  const goEl = $("#go-status");
  if (goEl.dataset.status !== status) {
    goEl.dataset.status = status;
  } else {
    goEl.dataset.status = status;
  }
  $("#go-status-value").textContent = status;

  const heroMeta = $("#hero-meta");
  const heroSetup = $("#hero-setup");
  if (data.signal?.setup && data.signal.setup !== "none") {
    heroMeta.hidden = false;
    heroSetup.textContent = `${humanize(data.signal.setup)} · ${humanize(data.signal.side || "—")}`;
  } else {
    heroMeta.hidden = true;
  }

  setSection("#gex-section", !!flags["ui.gex_map"] && !!data.gex);
  setSection("#flow-section", !!flags["ui.regime_strip"] && !!data.orderflow);
  setSection(
    "#flow-alerts-section",
    !!flags["ui.flow_alerts"] && Array.isArray(data.flow_alerts)
  );
  setSection("#setups-section", !!flags["ui.go_cards"] && !!data.signal);
  setSection("#intel-section", !!flags["intel.news"]);
  setSection("#operator-section", !!flags["ui.operator_panel"]);

  if (data.gex) {
    lastGex = data.gex;
    const regime = humanize(data.gex.regime || "—");
    $("#gex-regime-line").textContent = `${regime} · models ${
      data.gex.models_agree ? "agree" : "disagree"
    } · ${data.gex.source || "—"}`;
    drawGex($("#gex-canvas"), data.gex);
    $("#gex-levels").innerHTML = [
      ["Spot", fmt(data.gex.spot, 1), ""],
      ["Flip", fmt(data.gex.flip, 0), "level-flip"],
      ["Call wall", fmt(data.gex.call_wall, 0), "level-call"],
      ["Put wall", fmt(data.gex.put_wall, 0), "level-put"],
      ["Magnet", fmt(data.gex.magnet, 0), ""],
    ]
      .map(
        ([k, v, cls]) =>
          `<span class="${cls}"><strong>${k}</strong> ${v}</span>`
      )
      .join("");
  }

  if (data.orderflow) renderFlow(data.orderflow, data.gex);
  if (flags["ui.flow_alerts"]) {
    renderFlowAlerts(data.flow_alerts || [], data.underlying);
  }
  if (data.signal) renderCard(data.signal, exec);
  renderBlackout(data.intel || {});
  if (flags["intel.news"]) {
    renderNews(data.news || []);
    renderFurtherReading(data.intel || {});
  }
  if (flags["ui.operator_panel"]) renderFlags(flags);

  const p = data.providers || {};
  $("#providers-line").textContent = `chain:${p.option_chain || "—"} · perp:${p.perp || "—"} · news:${p.news || "—"} · exec:${p.execution || "—"}`;
  $("#exec-note").textContent =
    exec.exec_mode?.reason || exec.note || "Advisory · no live orders";

  const ledgerRow = $("#ledger-row");
  if (ledgerRow) {
    ledgerRow.hidden = !(flags["exec.paper"] || flags["exec.live"]);
    const ln = $("#ledger-note");
    if (ln) {
      const dl = exec.daily_loss || {};
      ln.textContent = dl.hit
        ? `Daily loss hit (${fmt(dl.realized_pnl_usd, 2)}) — Advisory`
        : `PnL today $${fmt(dl.realized_pnl_usd, 2)} / limit $${fmt(dl.limit_usd, 0)}`;
    }
  }

  wireKillSwitch(exec);
}

async function loadSnapshot() {
  const data = await api("/api/snapshot");
  applySnapshot(data);
  return data;
}

function boot() {
  $("#refresh-btn")?.addEventListener("click", () => {
    const btn = $("#refresh-btn");
    if (btn) btn.disabled = true;
    loadSnapshot()
      .catch(console.error)
      .finally(() => {
        if (btn) btn.disabled = false;
      });
  });

  $("#kill-btn")?.addEventListener("click", async () => {
    const btn = $("#kill-btn");
    if (!btn || btn.disabled) return;
    const armed = btn.classList.contains("is-armed");
    try {
      await api("/api/exec/kill", {
        method: "POST",
        body: JSON.stringify({ confirmed: armed, actor: "cockpit-ui" }),
      });
      await loadSnapshot();
    } catch (err) {
      console.error(err);
    }
  });

  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (lastGex) drawGex($("#gex-canvas"), lastGex);
    }, 120);
  });

  loadSnapshot().catch((err) => {
    console.error(err);
    $("#go-status-value").textContent = "ERR";
    $("#go-status").dataset.status = "NO-GO";
    $("#spot-value").textContent = "offline";
  });
}

boot();
