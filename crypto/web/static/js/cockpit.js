/**
 * CryptoCubera cockpit client — snapshot + GEX canvas + flag toggles.
 */

const $ = (sel, root = document) => root.querySelector(sel);

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
  const pad = { t: 24, r: 16, b: 36, l: 16 };
  const w = cssW - pad.l - pad.r;
  const h = cssH - pad.t - pad.b;
  const xs = rows.map((r) => r.strike);
  const nets = rows.map((r) => r.net_gex);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const maxAbs = Math.max(...nets.map((v) => Math.abs(v)), 1);

  const xOf = (strike) => pad.l + ((strike - minX) / (maxX - minX || 1)) * w;
  const yOf = (net) => pad.t + h / 2 - (net / maxAbs) * (h / 2) * 0.92;

  // Zero line
  ctx.strokeStyle = "rgba(20,32,42,0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t + h / 2);
  ctx.lineTo(pad.l + w, pad.t + h / 2);
  ctx.stroke();

  // Bars
  const bw = Math.max(6, (w / rows.length) * 0.55);
  rows.forEach((r) => {
    const x = xOf(r.strike);
    const y0 = pad.t + h / 2;
    const y1 = yOf(r.net_gex);
    ctx.fillStyle = r.net_gex >= 0 ? "rgba(13,124,124,0.72)" : "rgba(196,92,38,0.72)";
    ctx.fillRect(x - bw / 2, Math.min(y0, y1), bw, Math.abs(y1 - y0) || 1);
  });

  // Spot / walls / flip markers
  const markers = [
    { k: gex.spot, c: "#14202a", label: "spot" },
    { k: gex.flip, c: "#0d7c7c", label: "flip" },
    { k: gex.call_wall, c: "#065858", label: "call" },
    { k: gex.put_wall, c: "#c45c26", label: "put" },
  ].filter((m) => m.k != null);

  markers.forEach((m) => {
    const x = xOf(m.k);
    ctx.strokeStyle = m.c;
    ctx.setLineDash(m.label === "spot" ? [] : [4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, pad.t);
    ctx.lineTo(x, pad.t + h);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = m.c;
    ctx.font = "600 11px JetBrains Mono, monospace";
    ctx.fillText(m.label, x + 4, pad.t + 12);
  });

  // Axis labels
  ctx.fillStyle = "rgba(58,74,86,0.9)";
  ctx.font = "500 11px JetBrains Mono, monospace";
  ctx.fillText(fmt(minX), pad.l, cssH - 10);
  ctx.fillText(fmt(maxX), pad.l + w - 48, cssH - 10);
}

function renderFlow(orderflow, gex) {
  const grid = $("#flow-grid");
  if (!grid || !orderflow) return;
  const agree = gex?.models_agree;
  const cells = [
    { k: "Convention", v: gex?.convention_sign || "—", cls: gex?.convention_sign === "positive" ? "pos" : gex?.convention_sign === "negative" ? "neg" : "" },
    { k: "Flow model", v: gex?.flow_sign || "—", cls: gex?.flow_sign === "positive" ? "pos" : gex?.flow_sign === "negative" ? "neg" : "" },
    { k: "Agreement", v: agree ? "agree" : "disagree", cls: agree ? "agree" : "neg" },
    { k: "CVD 5m", v: fmt(orderflow.cvd_5m, 1), cls: orderflow.cvd_5m >= 0 ? "pos" : "neg" },
    { k: "CVD 15m", v: fmt(orderflow.cvd_15m, 1), cls: orderflow.cvd_15m >= 0 ? "pos" : "neg" },
    { k: "Book imb.", v: fmt(orderflow.book_imbalance, 2), cls: orderflow.book_imbalance >= 0 ? "pos" : "neg" },
    { k: "Funding", v: fmtPct(orderflow.funding_rate), cls: "" },
    { k: "Perp mark", v: fmt(orderflow.mark, 1), cls: "" },
  ];
  grid.innerHTML = cells
    .map(
      (c) => `<div class="flow-cell"><span class="k">${c.k}</span><span class="v ${c.cls}">${c.v}</span></div>`
    )
    .join("");
}

function paperStatusLine(signal) {
  const ps = signal.paper_status || {};
  const latest = ps.latest_open;
  if (latest) {
    return `Paper · ${latest.status} · ${latest.instrument} · $${fmt(latest.size_usd, 0)} · ${latest.adapter || "sim"}`;
  }
  if (signal.can_confirm) {
    return `Paper Confirm ready · adapter ${ps.adapter || "sim"} · open ${ps.open_intents ?? 0}`;
  }
  if (ps.open_intents) {
    return `Paper · ${ps.open_intents} open intent(s) · Confirm off`;
  }
  return "Advisory · no paper intent";
}

function renderCard(signal, exec) {
  const mount = $("#go-card-mount");
  if (!mount || !signal) return;
  const isGo = signal.status === "GO";
  const canConfirm = !!signal.can_confirm && isGo;
  const entry = Array.isArray(signal.entry_zone)
    ? signal.entry_zone.map((x) => fmt(x, 0)).join(" – ")
    : "—";
  const confirmLabel = canConfirm ? "Confirm paper" : "Confirm disabled";
  const confirmTitle = canConfirm
    ? "Submit paper/sim bracket (never live capital)"
    : "Enable exec.paper + exec.confirm (and stay inside daily loss)";
  mount.innerHTML = `
    <article class="go-card ${isGo ? "" : "is-nogo"}">
      <div class="go-card-top">
        <span class="go-pill ${isGo ? "go" : "nogo"}">${signal.status}</span>
        <span class="go-score">${signal.setup || "none"} · score ${signal.score ?? 0}</span>
      </div>
      <p class="paper-status" id="paper-status">${paperStatusLine(signal)}</p>
      <div class="go-grid">
        <div><span class="k">Instrument</span><div class="v">${signal.instrument || "—"}</div></div>
        <div><span class="k">Side</span><div class="v">${signal.side || "—"}</div></div>
        <div><span class="k">Entry</span><div class="v">${entry}</div></div>
        <div><span class="k">Target</span><div class="v">${fmt(signal.target, 0)}</div></div>
        <div><span class="k">Stop</span><div class="v">${fmt(signal.stop, 0)}</div></div>
        <div><span class="k">Size cap</span><div class="v">$${fmt(signal.size_cap_usd, 0)}</div></div>
      </div>
      <ul class="go-reasons">
        ${(signal.reasons || []).map((r) => `<li>${r}</li>`).join("")}
        ${signal.invalidation ? `<li>Invalidation: ${signal.invalidation}</li>` : ""}
        ${signal.valid_until ? `<li>Valid until ${new Date(signal.valid_until).toLocaleString()}</li>` : ""}
      </ul>
      <div class="go-actions">
        <button class="btn" type="button" id="confirm-paper" ${canConfirm ? "" : "disabled"} title="${confirmTitle}">${confirmLabel}</button>
        <button class="btn ghost" type="button" id="dismiss-card">Dismiss</button>
      </div>
      <p class="confirm-feedback" id="confirm-feedback" hidden></p>
    </article>
  `;
  $("#dismiss-card")?.addEventListener("click", () => {
    mount.innerHTML = `<p class="section-sub">Card dismissed. Refresh to reload the latest signal.</p>`;
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
        const t = result.trade || {};
        fb.textContent = `Paper ${t.status || "submitted"} · ${t.client_order_id || ""} · $${fmt(t.size_usd, 0)} · ${t.adapter || "sim"}`;
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
}

function renderNews(news, intelMeta) {
  const list = $("#intel-list");
  if (!list) return;
  const blackout = intelMeta?.blackout;
  const banner =
    blackout?.active
      ? `<li class="intel-blackout"><span class="tier IMMINENT">BLACKOUT</span><div><p class="intel-title">Signal veto active</p><p class="intel-note">${(blackout.reasons || []).slice(0, 2).join(" · ") || "IMMINENT window"}</p></div></li>`
      : "";
  if (!news?.length && !banner) {
    list.innerHTML = `<li><p class="intel-title">No events</p></li>`;
    return;
  }
  const rows = (news || [])
    .map(
      (e) => `
      <li>
        <span class="tier ${e.tier}">${e.tier}</span>
        <div>
          <p class="intel-title">${e.title}</p>
          <p class="intel-note">${e.event_type || "event"} · ${e.impact_note || e.source} · score ${Number(e.score || 0).toFixed(2)}${e.engine_action ? ` · ${e.engine_action}` : ""}</p>
        </div>
      </li>`
    )
    .join("");
  list.innerHTML = banner + rows;
}

function renderFlags(flags) {
  const grid = $("#flag-grid");
  if (!grid || !flags) return;
  const keys = Object.keys(flags).sort();
  grid.innerHTML = keys
    .map((name) => {
      const on = !!flags[name];
      return `
        <label class="flag-row">
          <span>${name}</span>
          <span class="toggle">
            <input type="checkbox" data-flag="${name}" ${on ? "checked" : ""} />
            <span></span>
          </span>
        </label>`;
    })
    .join("");

  grid.querySelectorAll("input[data-flag]").forEach((el) => {
    el.addEventListener("change", async () => {
      const name = el.getAttribute("data-flag");
      try {
        await api("/api/flags", {
          method: "PATCH",
          body: JSON.stringify({ name, value: el.checked, actor: "operator-ui" }),
        });
        await loadSnapshot();
      } catch (err) {
        console.error(err);
        el.checked = !el.checked;
      }
    });
  });
}

function setSection(id, on) {
  const el = $(id);
  if (!el) return;
  el.hidden = !on;
}

function wireKillSwitch(exec) {
  const btn = $("#kill-btn");
  if (!btn) return;
  const enabled = !!exec?.kill_switch_enabled;
  btn.disabled = !enabled;
  btn.classList.toggle("is-armed", !!exec?.kill_switch_armed);
  if (!enabled) {
    btn.textContent = "Kill switch";
    btn.title = "Enable exec.paper to use kill switch";
    return;
  }
  if (exec.kill_switch_armed) {
    btn.textContent = "Confirm kill";
    btn.title = "Second tap — cancel open paper intents";
  } else {
    btn.textContent = "Kill switch";
    btn.title = "First tap arms; second tap cancels paper intents";
  }
}

async function loadSnapshot() {
  const data = await api("/api/snapshot");
  const flags = data.flags || {};
  const exec = data.execution || {};

  const badge = $("#mode-badge");
  const badgeText = data.mode_badge || (data.mode === "paper_confirm" ? "Paper Confirm" : "Advisory");
  badge.textContent = badgeText;
  badge.dataset.mode = data.mode || "advisory";

  $("#underlying-label").textContent = data.underlying || "BTC";
  $("#spot-value").textContent = fmt(data.spot, 1);
  $("#source-pill").textContent = data.chain?.source || data.providers?.option_chain || "—";

  const paperPill = $("#paper-pill");
  if (paperPill) {
    const paperOn = !!flags["exec.paper"];
    paperPill.hidden = !paperOn;
    if (paperOn) {
      const adapter = exec.adapter || "sim";
      paperPill.textContent = `paper · ${adapter} · open ${exec.open_intents ?? 0}`;
    }
  }

  const status = data.signal?.status || "NO-GO";
  const goEl = $("#go-status");
  goEl.dataset.status = status;
  $("#go-status-value").textContent = status;

  setSection("#gex-section", !!flags["ui.gex_map"] && !!data.gex);
  setSection("#flow-section", !!flags["ui.regime_strip"] && !!data.orderflow);
  setSection("#setups-section", !!flags["ui.go_cards"] && !!data.signal);
  setSection("#intel-section", !!flags["intel.news"]);
  setSection("#operator-section", !!flags["ui.operator_panel"]);

  if (data.gex) {
    const regime = data.gex.regime?.replaceAll("_", " ") || "—";
    $("#gex-regime-line").textContent = `${regime} · models ${data.gex.models_agree ? "agree" : "disagree"} · ${data.gex.source}`;
    drawGex($("#gex-canvas"), data.gex);
    $("#gex-levels").innerHTML = [
      ["Spot", fmt(data.gex.spot, 1)],
      ["Flip", fmt(data.gex.flip, 0)],
      ["Call wall", fmt(data.gex.call_wall, 0)],
      ["Put wall", fmt(data.gex.put_wall, 0)],
      ["Magnet", fmt(data.gex.magnet, 0)],
    ]
      .map(([k, v]) => `<span><strong>${k}</strong> ${v}</span>`)
      .join("");
  }

  if (data.orderflow) renderFlow(data.orderflow, data.gex);
  if (data.signal) renderCard(data.signal, exec);
  if (flags["intel.news"]) renderNews(data.news || [], data.intel || {});
  if (flags["ui.operator_panel"]) renderFlags(flags);

  const p = data.providers || {};
  $("#providers-line").textContent = `chain:${p.option_chain || "—"} · perp:${p.perp || "—"} · news:${p.news || "—"} · exec:${p.execution || "—"}`;
  $("#exec-note").textContent =
    exec.exec_mode?.reason || exec.note || "Advisory · no live orders";

  const ledgerRow = $("#ledger-row");
  if (ledgerRow) {
    ledgerRow.hidden = !flags["exec.paper"];
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

function boot() {
  $("#refresh-btn")?.addEventListener("click", () => loadSnapshot().catch(console.error));
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
    // redraw from last snapshot via soft refresh
  });
  loadSnapshot().catch((err) => {
    console.error(err);
    $("#go-status-value").textContent = "ERR";
    $("#spot-value").textContent = "offline";
  });
}

boot();
