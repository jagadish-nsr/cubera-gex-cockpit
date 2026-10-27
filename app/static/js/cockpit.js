/**
 * Cubera GEX Cockpit — Institutional Frontend Application Engine.
 * Provides real-time WebSocket telemetry, sub-millisecond client state machine tickers,
 * interactive Black-76 repricing sliders, position sizing HUD, and Lightweight Charts.
 */

(function () {
  "use strict";

  // Application Global State
  const state = {
    symbol: "NIFTY",
    window: "current_week",
    capital: 500000,
    riskPct: 1.5,
    activeTab: "tab-cockpit",
    soundEnabled: true,
    theme: localStorage.getItem("cubera_theme") || (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"),
    alertBellEnabled: localStorage.getItem("cubera_alert_bell_muted") !== "true",
    lastAlertScenarios: {},
    lastAlertScenario: null,
    lastAlertSoundTime: 0,
    lastCockpitDash: null,
    tapeLatch: null,
    lastTapePaintSig: null,
    audioCtx: null,
    pendingAlertBell: false,
    pendingAlertBellTime: 0,
    spot: 24500.0,
    ws: null,
    replayMode: false,
    replayAsOf: null,
    replayLoadedMinute: "",
    sentinelChart: null,
    candlestickSeries: null,
    cvdSeries: null,
    speechSynth: window.speechSynthesis || null,
  };

  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function istChartStamp(ts) {
    if (typeof ts !== "number" || !Number.isFinite(ts)) return "";
    const d = new Date(ts * 1000);
    return `${pad2(d.getUTCDate())}-${pad2(d.getUTCMonth() + 1)}-${d.getUTCFullYear()} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} IST`;
  }

  function formatValidStamp(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    if (/^\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}/.test(s)) {
      return /\bIST\b/i.test(s) ? s : `${s} IST`;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      const parts = s.split("-");
      return `${parts[2]}-${parts[1]}-${parts[0]}`;
    }
    if (s.includes("T") || /[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
      const parsed = Date.parse(s);
      if (!Number.isNaN(parsed)) {
        const bits = new Intl.DateTimeFormat("en-GB", {
          timeZone: "Asia/Kolkata",
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }).formatToParts(new Date(parsed));
        const get = (type) => (bits.find((p) => p.type === type) || {}).value || "";
        if (get("year")) {
          return `${get("day")}-${get("month")}-${get("year")} ${get("hour")}:${get("minute")}:${get("second")} IST`;
        }
      }
    }
    return s;
  }

  function datedClock(raw, isoDate) {
    const shown = formatValidStamp(raw);
    if (/^\d{2}-\d{2}-\d{4}\b/.test(shown)) return shown;
    const hm = String(raw || "").trim().match(/^(\d{2}:\d{2}(?::\d{2})?)(?:\s*IST)?$/i);
    if (hm && /^\d{4}-\d{2}-\d{2}$/.test(String(isoDate || ""))) {
      const parts = String(isoDate).split("-");
      const hms = hm[1].length === 5 ? `${hm[1]}:00` : hm[1];
      return `${parts[2]}-${parts[1]}-${parts[0]} ${hms} IST`;
    }
    return shown;
  }

  // -------------------------------------------------------------------------
  // Web Audio Synthesizer & Auto-Unlock on First User Interaction
  // -------------------------------------------------------------------------
  function initAudioContext() {
    if (!state.audioCtx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        state.audioCtx = new AudioCtx();
      }
    }
    if (state.audioCtx && state.audioCtx.state === "suspended") {
      state.audioCtx.resume().catch(() => {});
    }
    return state.audioCtx;
  }

  // Auto-unlock Web Audio on first user interaction anywhere on the page
  const audioUnlockEvents = ["click", "keydown", "touchstart", "pointerdown"];
  function handleFirstInteractionUnlock() {
    const ctx = initAudioContext();
    if (ctx) {
      ctx.resume().then(() => {
        if (state.pendingAlertBell && (Date.now() - state.pendingAlertBellTime < 30000)) {
          state.pendingAlertBell = false;
          playTraderBellRing();
        }
      }).catch(() => {});

      if (ctx.state === "running") {
        audioUnlockEvents.forEach((evt) => {
          window.removeEventListener(evt, handleFirstInteractionUnlock, { capture: true });
        });
      }
    }
  }
  audioUnlockEvents.forEach((evt) => {
    window.addEventListener(evt, handleFirstInteractionUnlock, { capture: true, passive: true });
  });

  function playAudioTone(freq, type = "sine", duration = 0.25) {
    if (!state.soundEnabled) return;
    try {
      const audioCtx = initAudioContext();
      if (!audioCtx) return;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.12, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + duration);
    } catch (e) {}
  }

  function playSuccessChime() {
    playAudioTone(523.25, "sine", 0.15); // C5
    setTimeout(() => playAudioTone(659.25, "sine", 0.25), 120); // E5
  }

  function playCautionChime() {
    playAudioTone(370.0, "triangle", 0.2); // F#4
    setTimeout(() => playAudioTone(311.13, "triangle", 0.3), 150); // Eb4
  }

  // Unified 3.0-Second Louder Brass Desk Bell Synthesizer (Pure Web Audio)
  function playTraderBellRing() {
    if (!state.alertBellEnabled) return;
    try {
      const ctx = initAudioContext();
      if (!ctx) return;
      if (ctx.state === "suspended") {
        ctx.resume().catch(() => {});
      }

      const now = ctx.currentTime;
      const f0 = 987.77; // B5 authentic brass desk bell fundamental
      const partials = [
        { freq: f0, gain: 0.55, decay: 3.0 },
        { freq: f0 * 2.02, gain: 0.28, decay: 2.1 },
        { freq: f0 * 3.01, gain: 0.16, decay: 1.4 },
        { freq: f0 * 4.25, gain: 0.09, decay: 0.8 },
      ];

      partials.forEach((p) => {
        const osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(p.freq, now);

        g.gain.setValueAtTime(0.001, now);
        g.gain.linearRampToValueAtTime(p.gain, now + 0.002);
        g.gain.exponentialRampToValueAtTime(0.0001, now + p.decay);

        osc.connect(g);
        g.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + p.decay + 0.05);
      });
    } catch (e) {
      console.warn("Error synthesizing 3.0s alert bell:", e);
    }
  }

  window.toggleAlertBell = function () {
    state.alertBellEnabled = !state.alertBellEnabled;
    localStorage.setItem("cubera_alert_bell_muted", (!state.alertBellEnabled).toString());
    const btn = document.getElementById("alert-bell-toggle-btn");
    if (btn) {
      if (state.alertBellEnabled) {
        btn.textContent = "🔔 Bell: ON";
        btn.className = "tape-ctrl-btn active";
      } else {
        btn.textContent = "🔕 Bell: MUTE";
        btn.className = "tape-ctrl-btn muted";
      }
    }
  };

  window.testAlertBell = function () {
    initAudioContext();
    playTraderBellRing();
    showToast("Auditioning 3.0s Louder Brass Bell Alert", "🔔");
  };

  // -------------------------------------------------------------------------
  // Toast Notifications
  // -------------------------------------------------------------------------
  function showToast(message, icon = "⚡") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateX(100%)";
      toast.style.transition = "all 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // -------------------------------------------------------------------------
  // Dark / Light Theme Mode Management Engine
  // -------------------------------------------------------------------------
  function applyTheme(theme, showNotification = false) {
    const nextTheme = theme === "light" ? "light" : "dark";
    state.theme = nextTheme;
    try {
      localStorage.setItem("cubera_theme", nextTheme);
    } catch (e) {}

    document.documentElement.setAttribute("data-theme", nextTheme);
    if (document.body) {
      document.body.classList.toggle("theme-light", nextTheme === "light");
      document.body.classList.toggle("theme-dark", nextTheme === "dark");
    }

    // Top header toggle button
    const btnToggle = document.getElementById("btn-theme-toggle");
    const iconSpan = document.getElementById("theme-toggle-icon");
    const labelSpan = document.getElementById("theme-toggle-label");
    if (btnToggle) {
      if (iconSpan && labelSpan) {
        iconSpan.textContent = nextTheme === "light" ? "☀️" : "🌙";
        labelSpan.textContent = nextTheme === "light" ? "Light" : "Dark";
      } else {
        btnToggle.textContent = nextTheme === "light" ? "☀️ Theme: Light" : "🌙 Theme: Dark";
      }
      btnToggle.setAttribute("title", `Theme: ${nextTheme.toUpperCase()} (Click or Ctrl+Shift+D to toggle)`);
    }

    // Sidebar toggle button
    const sideBtn = document.getElementById("btn-sidebar-theme-toggle");
    const sideIcon = document.getElementById("sidebar-theme-toggle-icon");
    const sideLabel = document.getElementById("sidebar-theme-toggle-label");
    if (sideBtn) {
      if (sideIcon && sideLabel) {
        sideIcon.textContent = nextTheme === "light" ? "☀️" : "🌙";
        sideLabel.textContent = nextTheme === "light" ? "Theme Mode: Light" : "Theme Mode: Dark";
      } else {
        sideBtn.textContent = nextTheme === "light" ? "☀️ Theme: Light" : "🌙 Theme: Dark";
      }
    }

    // Update LightweightCharts dynamic theme if active
    const isLight = nextTheme === "light";
    const chartLayoutOptions = {
      layout: {
        background: { color: isLight ? "#ffffff" : "#0c1424" },
        textColor: isLight ? "#334155" : "#94a3b8"
      },
      grid: {
        vertLines: { color: isLight ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" },
        horzLines: { color: isLight ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }
      },
      rightPriceScale: {
        borderColor: isLight ? "rgba(203, 213, 225, 0.8)" : "rgba(51, 65, 85, 0.45)"
      }
    };

    if (cockpitGexChart) {
      try {
        cockpitGexChart.applyOptions(chartLayoutOptions);
      } catch (e) {}
    }
    if (state.sentinelChart) {
      try {
        state.sentinelChart.applyOptions(chartLayoutOptions);
      } catch (e) {}
    }

    // If TradingView widget is rendered, re-render it with matching theme
    if (cockpitChartState && cockpitChartState.engine === "tradingview") {
      try {
        renderTradingViewWidget(cockpitChartState.symbol, cockpitChartState.timeframe);
      } catch (e) {}
    }

    if (showNotification) {
      showToast(`${nextTheme === "light" ? "☀️ Light" : "🌙 Dark"} mode enabled`);
    }
  }

  function toggleTheme() {
    const next = state.theme === "light" ? "dark" : "light";
    applyTheme(next, true);
  }

  window.applyTheme = applyTheme;
  window.toggleTheme = toggleTheme;

  // -------------------------------------------------------------------------
  // Client-Side 1-Second State Machine Ticker
  // (Preserves exact logic from Streamlit _inject_rec_counter_ticker)
  // -------------------------------------------------------------------------
  function fmtSecs(s) {
    if (s < 0) s = 0;
    if (s < 60) return Math.floor(s) + "s";
    const m = Math.floor(s / 60);
    const rem = Math.floor(s % 60);
    if (m < 60) return m + "m " + String(rem).padStart(2, "0") + "s";
    const h = Math.floor(m / 3600);
    const remM = Math.floor((s % 3600) / 60);
    return h + "h " + String(remM).padStart(2, "0") + "m";
  }

  function updateRecCounters() {
    try {
      const now = Date.now() / 1000.0;
      const chips = document.querySelectorAll(".rec-counter-chip");
      chips.forEach((el) => {
        const st = el.getAttribute("data-latch-state");
        const fa = parseFloat(el.getAttribute("data-first-active") || "0");
        const la = parseFloat(el.getAttribute("data-last-active") || "0");
        if (st === "ACTIVE") {
          const dur = fa > 0 ? now - fa : la > 0 ? now - la : 0;
          el.textContent = "🟢 Active: " + fmtSecs(dur);
          el.style.color = "var(--go)";
          el.style.borderColor = "var(--go)";
        } else if (st === "TARGET_HIT" || st === "TGT1_HIT" || st === "TGT2_HIT") {
          const elap = la > 0 ? now - la : 0;
          el.textContent = "🎯 Target Hit (" + fmtSecs(elap) + " ago)";
          el.style.color = "var(--go)";
        } else if (st === "INVALIDATED") {
          const elap = la > 0 ? now - la : 0;
          el.textContent = "🛑 Inval (" + fmtSecs(elap) + " ago)";
          el.style.color = "var(--stop)";
          el.style.borderColor = "var(--stop)";
        } else if (st === "DEGRADED") {
          if (la > 0) {
            el.textContent = "⏱️ Last Active: " + fmtSecs(now - la) + " ago";
          } else {
            el.textContent = "⏱️ Pending Active";
          }
          el.style.color = "var(--ink-mute)";
        }
      });
    } catch (e) {}
  }

  setInterval(updateRecCounters, 1000);

  // -------------------------------------------------------------------------
  // WebSocket Telemetry Connection
  // -------------------------------------------------------------------------
  function initWebSocket() {
    if (state.wsReconnectTimer) {
      clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = null;
    }
    if (state.ws) {
      try {
        state.ws.onclose = null;
        state.ws.onerror = null;
        state.ws.close();
      } catch (e) {}
      state.ws = null;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/telemetry?symbol=${encodeURIComponent(state.symbol)}`;

    try {
      state.ws = new WebSocket(wsUrl);

      state.ws.onopen = () => {
        console.log(`WebSocket connected to /ws/telemetry for ${state.symbol}`);
        try {
          state.ws.send(JSON.stringify({ action: "subscribe", symbol: state.symbol }));
        } catch (e) {}
        const badge = document.getElementById("quote-source-badge");
        if (badge) {
          badge.textContent = "⚡ WS 0ms";
          badge.style.color = "var(--spark)";
        }
      };

      state.ws.onmessage = (event) => {
        try {
          const tick = JSON.parse(event.data);
          paintWallKpisFromTick(tick);
          if (tick.feed_mode === "REPLAY") {
            state.replayMode = true;
            if (Number.isFinite(Number(tick.timestamp))) state.replayAsOf = Number(tick.timestamp);
            const minute = String((tick.replay && tick.replay.hhmm) || "").slice(0, 5);
            if (minute && minute !== state.replayLoadedMinute && !(tick.replay && tick.replay.repeat)) {
              state.replayLoadedMinute = minute;
              loadCockpit();
            }
          }
          if (tick.type === "TICK" && tick.is_market_open) {
            handleLiveTick(tick);
          } else if (tick.type === "STATUS") {
            const clockEl = document.getElementById("header-clock");
            if (clockEl && tick.ist_time) {
              const cleanTime = String(tick.ist_time).replace(/\bIST\b/gi, "").trim();
              clockEl.textContent = `${cleanTime} IST`;
            }
          }
        } catch (err) {}
      };

      state.ws.onclose = () => {
        const badge = document.getElementById("quote-source-badge");
        if (badge) {
          badge.textContent = "⚡ MTM 0ms";
          badge.style.color = "var(--ink-mute)";
        }
        state.wsReconnectTimer = setTimeout(initWebSocket, 2500);
      };

      state.ws.onerror = () => {
        if (state.ws) state.ws.close();
      };
    } catch (e) {
      state.wsReconnectTimer = setTimeout(initWebSocket, 3000);
    }
  }

  function handleLiveTick(tick) {
    if (!tick) return;
    // Strict symbol matching: never allow ticks without a symbol or from a different index to contaminate the active display
    const tickSym = (tick.symbol || "").toUpperCase();
    const activeSym = (state.symbol || "").toUpperCase();
    if (!tickSym || !activeSym || tickSym !== activeSym) {
      return;
    }

    state.spot = tick.spot;
    const spotEl = document.getElementById("spot-price-display");
    if (spotEl) {
      spotEl.textContent = `₹${tick.spot.toLocaleString("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`;
    }
    const futEl = document.getElementById("futures-price-display");
    if (futEl) {
      futEl.textContent = `₹${tick.future_price.toLocaleString("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`;
    }
    const basisEl = document.getElementById("basis-display");
    if (basisEl) {
      const sign = tick.basis >= 0 ? "+" : "";
      basisEl.textContent = `${sign}${tick.basis.toFixed(1)} pts`;
      basisEl.style.color = tick.basis >= 0 ? "var(--go)" : "var(--stop)";
    }
    const clockEl = document.getElementById("header-clock");
    if (clockEl && tick.ist_time) {
      const cleanTime = String(tick.ist_time).replace(/\bIST\b/gi, "").trim();
      clockEl.textContent = `${cleanTime} IST`;
    }
    if (tick.india_vix && tick.india_vix.last != null) {
      const vixEl = document.getElementById("vix-display");
      if (vixEl) {
        const vixPct = Number(tick.india_vix.pct_change);
        const vixPctTxt = Number.isFinite(vixPct) ? vixPct.toFixed(2) : "0.00";
        vixEl.textContent = `${Number(tick.india_vix.last).toFixed(2)} (${vixPct > 0 ? "+" : ""}${vixPctTxt}%)`;
      }
    }

    // Feed Dhan live tick data into TradingView chart components
    feedDhanTickToTradingView(tick);
    paintRecLiveQuotes(tick.rec_quotes);
    patchCockpitDashFromTick(tick);
    if (state.lastCockpitDash) updateMarketAlertTape(state.lastCockpitDash);
  }

  function paintRecLiveQuotes(quotes) {
    if (!quotes || !quotes.length) return;
    quotes.forEach((q) => {
      const rid = q.rec_id;
      if (!rid) return;
      const ltpTxt = `₹${Number(q.ltp || 0).toFixed(1)}`;
      const t1Txt = `₹${Number(q.target_1 || 0).toFixed(1)}`;
      const t2Txt = `₹${Number(q.target_2 || 0).toFixed(1)}`;
      const slTxt = `₹${Number(q.stop_loss || 0).toFixed(1)}`;
      const pnlPts = Number(q.pnl_pts || 0);
      const pnlPct = Number(q.pnl_pct || 0);
      const pnlColor = pnlPts >= 0 ? "var(--go)" : "var(--stop)";
      const pnlTxt = `MTM: ${pnlPts >= 0 ? "+" : ""}₹${pnlPts.toFixed(1)} (${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%)`;

      const setTxt = (id, txt) => {
        const el = document.getElementById(id);
        if (el) el.textContent = txt;
      };
      setTxt(`rec-ltp-${rid}`, ltpTxt);
      setTxt(`rec-t1-${rid}`, `T1: ${t1Txt}`);
      setTxt(`rec-sl-${rid}`, `SL: ${slTxt}`);
      setTxt(`rec-t1-grid-${rid}`, t1Txt);
      setTxt(`rec-t2-grid-${rid}`, t2Txt);
      const mtm = document.getElementById(`rec-mtm-${rid}`);
      if (mtm) {
        mtm.textContent = pnlTxt;
        mtm.style.color = pnlColor;
      }
      const cockpitLtp = document.getElementById(`cockpit-rec-ltp-${rid}`);
      const cockpitT1 = document.getElementById(`cockpit-rec-t1-${rid}`);
      const cockpitSl = document.getElementById(`cockpit-rec-sl-${rid}`);
      if (cockpitLtp) cockpitLtp.textContent = ltpTxt;
      if (cockpitT1) cockpitT1.textContent = t1Txt;
      if (cockpitSl) cockpitSl.textContent = slTxt;
      const liveBadge = document.getElementById(`rec-live-badge-${rid}`);
      if (liveBadge) {
        liveBadge.textContent = q.is_ws_live ? "⚡ WS 0ms" : "⚡ MTM";
        liveBadge.style.color = q.is_ws_live ? "var(--spark)" : "var(--ink-mute)";
      }
    });
  }

  function feedDhanTickToTradingView(tick) {
    if (!tick) return;
    const px = Number(tick.spot || tick.future_price || 0);
    if (px <= 0) return;

    const tickSym = (tick.symbol || "").toUpperCase();
    const activeChartSym = (cockpitChartState.symbol || state.symbol || "").toUpperCase();
    if (!tickSym || !activeChartSym || tickSym !== activeChartSym) {
      return;
    }

    const tickTs = Math.floor(tick.timestamp || (Date.now() / 1000));
    // Rule 1 & Rule 8: IST Timestamp Shifting (+19,800s)
    const istTs = tickTs + 19800;

    // 1. Update Cockpit TradingView Chart (GEX Canvas) - only if tick strictly matches active chart index
    if (cockpitCandleSeries && cockpitChartState.engine === "gex_canvas") {
      const tfSecMap = { "1m": 60, "5m": 300, "15m": 900 };
      const intervalSec = tfSecMap[cockpitChartState.timeframe] || 300;
      const barTime = Math.floor(istTs / intervalSec) * intervalSec;

      if (!cockpitChartState.currentBar || cockpitChartState.currentBar.time !== barTime) {
        if (tick.latest_bar && tick.latest_bar.time === barTime) {
          cockpitChartState.currentBar = { ...tick.latest_bar };
        } else {
          cockpitChartState.currentBar = {
            time: barTime,
            open: px,
            high: px,
            low: px,
            close: px,
          };
        }
      } else {
        cockpitChartState.currentBar.high = Math.max(cockpitChartState.currentBar.high, px);
        cockpitChartState.currentBar.low = Math.min(cockpitChartState.currentBar.low, px);
        cockpitChartState.currentBar.close = px;
      }
      try {
        cockpitCandleSeries.update(cockpitChartState.currentBar);
      } catch (err) {}

      if (cockpitVwapSeries && tick.latest_bar && tick.latest_bar.vwap > 0) {
        try {
          cockpitVwapSeries.update({ time: barTime, value: tick.latest_bar.vwap });
        } catch (err) {}
      }

      // Live HUD Badges Update
      const chlOhlc = document.getElementById("chl-ohlc");
      if (chlOhlc && cockpitChartState.currentBar) {
        const cb = cockpitChartState.currentBar;
        chlOhlc.textContent = `O: ₹${cb.open.toFixed(1)} H: ₹${cb.high.toFixed(1)} L: ₹${cb.low.toFixed(1)} C: ₹${cb.close.toFixed(1)}`;
      }
      const tfBadge = document.getElementById("cockpit-chart-badge");
      if (tfBadge) {
        const tfLabel = cockpitChartState.timeframe === "5m" ? "5min" : cockpitChartState.timeframe;
        const replay = tick.feed_mode === "REPLAY";
        tfBadge.innerHTML = replay
          ? `${tfLabel} · <span style="color:#e0a100;font-weight:800">REPLAY ₹${px.toFixed(1)}</span>`
          : `${tfLabel} · <span style="color:var(--go);font-weight:800">🟢 DHAN TICK ₹${px.toFixed(1)}</span>`;
      }
    }

    // 2. Update Sentinel Tape TradingView Chart - only if tick matches active sentinel index
    if (state.candlestickSeries && tickSym === (state.symbol || "").toUpperCase()) {
      const sTfSecMap = { "1m": 60, "3m": 180, "5m": 300, "15m": 900 };
      const sIntervalSec = sTfSecMap[sentinelState.timeframe] || 60;
      const sBarTime = Math.floor(istTs / sIntervalSec) * sIntervalSec;

      if (!sentinelState.currentBar || sentinelState.currentBar.time !== sBarTime) {
        if (tick.latest_bar && tick.latest_bar.time === sBarTime) {
          sentinelState.currentBar = { ...tick.latest_bar };
        } else {
          sentinelState.currentBar = {
            time: sBarTime,
            open: px,
            high: px,
            low: px,
            close: px,
          };
        }
      } else {
        sentinelState.currentBar.high = Math.max(sentinelState.currentBar.high, px);
        sentinelState.currentBar.low = Math.min(sentinelState.currentBar.low, px);
        sentinelState.currentBar.close = px;
      }
      try {
        state.candlestickSeries.update(sentinelState.currentBar);
      } catch (err) {}

      const shlOhlc = document.getElementById("shl-ohlc");
      if (shlOhlc && sentinelState.currentBar) {
        const sb = sentinelState.currentBar;
        shlOhlc.textContent = `O: ₹${sb.open.toFixed(1)} H: ₹${sb.high.toFixed(1)} L: ₹${sb.low.toFixed(1)} C: ₹${sb.close.toFixed(1)}`;
      }
    }
  }

  // -------------------------------------------------------------------------
  // Data Loaders for All 11 Tabs
  // -------------------------------------------------------------------------
  function istMinutesNow() {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date());
    const hour = Number((parts.find((p) => p.type === "hour") || {}).value || 0);
    const minute = Number((parts.find((p) => p.type === "minute") || {}).value || 0);
    return hour * 60 + minute;
  }

  function isClientPreBellWindow() {
    const t = istMinutesNow();
    return t >= 8 * 60 + 45 && t <= 9 * 60 + 22;
  }

  function syncCockpitPreBellCard(serverFlag) {
    const el = document.getElementById("cockpit-pre-market-card");
    if (!el) return;
    const show = Boolean(serverFlag) && isClientPreBellWindow();
    el.style.display = show ? "block" : "none";
  }

  async function loadStatus() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/status?symbol=${encodeURIComponent(requestedSym)}&window=${state.window}`);
      const data = await res.json();
      // Discard stale in-flight response if user has switched index
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) {
        return;
      }
      const wsLive = state.ws && state.ws.readyState === 1 && data.is_market_open;
      if (!wsLive) {
        state.spot = data.spot;
        document.getElementById("spot-price-display").textContent = `₹${data.spot.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
        document.getElementById("futures-price-display").textContent = `₹${data.future_price.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
      }
      document.getElementById("header-index-label").textContent = data.label;
      if (document.getElementById("header-clock") && data.ist_time) {
        const cleanTime = String(data.ist_time).replace(/\bIST\b/gi, "").trim();
        document.getElementById("header-clock").textContent = `${cleanTime} IST`;
      }
      document.getElementById("active-expiry-target").textContent = data.expiry_target;
      document.getElementById("active-dte-badge").textContent = data.dte_badge;
      document.getElementById("active-mode-desc").textContent = `${data.mode_desc} · ${data.label}`;

      const ohlc = data.day_ohlc;
      if (ohlc) {
        document.getElementById("day-hl-display").textContent = `${ohlc.high.toLocaleString("en-IN")} / ${ohlc.low.toLocaleString("en-IN")}`;
      }
      if (data.india_vix && !wsLive) {
        const vixPct = Number(data.india_vix.pct_change);
        const vixPctTxt = Number.isFinite(vixPct) ? vixPct.toFixed(2) : "0.00";
        document.getElementById("vix-display").textContent = `${Number(data.india_vix.last).toFixed(2)} (${vixPct > 0 ? "+" : ""}${vixPctTxt}%)`;
      }

      // Market Hours & Feed Mode Handling
      state.isMarketOpen = Boolean(data.is_market_open);
      const quoteBoxLabel = document.getElementById("quote-box-label");
      const quoteStatusDot = document.getElementById("quote-status-dot");
      const feedBadge = document.getElementById("quote-source-badge");
      const sessionPhaseBadge = document.getElementById("session-phase-badge");
      const feedBanner = document.getElementById("feed-mode-banner");
      const feedStatusBadge = document.getElementById("feed-status-badge");
      const sessionTitleDisplay = document.getElementById("session-title-display");
      const sessionSubstateDisplay = document.getElementById("session-substate-display");

      if (!data.is_market_open) {
        if (quoteBoxLabel) quoteBoxLabel.textContent = "Spot Close (EOD)";
        if (quoteStatusDot) quoteStatusDot.className = "offline-dot";
        if (feedBadge) {
          feedBadge.textContent = "EOD Close";
          feedBadge.style.color = "var(--ink-mute)";
          feedBadge.style.background = "rgba(255,255,255,0.06)";
          feedBadge.style.borderColor = "rgba(255,255,255,0.1)";
        }
        if (sessionPhaseBadge) {
          const badgeText = (data.session_schedule && data.session_schedule.phase_badge) || data.session_label || "MARKET CLOSED";
          sessionPhaseBadge.textContent = badgeText.toUpperCase();
          sessionPhaseBadge.className = "badge-active";
          sessionPhaseBadge.style.background = "rgba(239,68,68,0.15)";
          sessionPhaseBadge.style.color = "#f87171";
          sessionPhaseBadge.style.border = "1px solid rgba(239,68,68,0.3)";
        }
        if (feedBanner) {
          feedBanner.className = "feed-mode-banner offline";
          feedBanner.style.borderColor = "rgba(239,68,68,0.3)";
          feedBanner.style.background = "rgba(239,68,68,0.04)";
        }
        if (feedStatusBadge) {
          feedStatusBadge.textContent = data.feed_badge || "🛑 Offline Snapshot";
          feedStatusBadge.style.color = "#f87171";
          feedStatusBadge.style.background = "rgba(239,68,68,0.15)";
        }
        if (sessionTitleDisplay) {
          sessionTitleDisplay.textContent = data.feed_title || "Ingestion: Offline Snapshot";
          sessionTitleDisplay.style.color = "var(--ink-mute)";
        }
        if (sessionSubstateDisplay) {
          sessionSubstateDisplay.textContent = data.feed_subtext || "Tick stream paused · Broker pollers idle";
        }
      } else if (data.replay) {
        if (quoteBoxLabel) quoteBoxLabel.textContent = "Stored session";
        if (quoteStatusDot) quoteStatusDot.className = "live-dot";
        if (feedBadge) {
          feedBadge.textContent = "REPLAY";
          feedBadge.style.color = "#e0a100";
          feedBadge.style.background = "rgba(224,161,0,0.12)";
          feedBadge.style.borderColor = "";
        }
        if (sessionPhaseBadge) {
          sessionPhaseBadge.textContent = "REPLAY";
        }
        if (feedBanner) {
          feedBanner.className = "feed-mode-banner live";
        }
        if (feedStatusBadge) {
          feedStatusBadge.textContent = data.feed_badge || "REPLAY";
          feedStatusBadge.style.color = "#e0a100";
          feedStatusBadge.style.background = "rgba(224,161,0,0.12)";
        }
        if (sessionTitleDisplay) {
          sessionTitleDisplay.textContent = data.feed_title || "Stored session";
          sessionTitleDisplay.style.color = "#e0a100";
        }
        if (sessionSubstateDisplay) {
          sessionSubstateDisplay.textContent = data.feed_subtext || "This is not the live book.";
        }
      } else {
        if (quoteBoxLabel) quoteBoxLabel.textContent = "Spot Live Quote";
        if (quoteStatusDot) quoteStatusDot.className = "live-dot";
        if (feedBadge) {
          feedBadge.textContent = "⚡ WS Live";
          feedBadge.style.color = "var(--spark)";
          feedBadge.style.background = "var(--spark-bg)";
          feedBadge.style.borderColor = "";
        }
        if (sessionPhaseBadge && data.session_label) {
          sessionPhaseBadge.textContent = data.session_label.toUpperCase();
          sessionPhaseBadge.style.background = "";
          sessionPhaseBadge.style.color = "";
          sessionPhaseBadge.style.border = "";
        }
        if (feedBanner) {
          feedBanner.className = "feed-mode-banner live";
          feedBanner.style.borderColor = "";
          feedBanner.style.background = "";
        }
        if (feedStatusBadge) {
          feedStatusBadge.textContent = "🟢 WebSocket Live";
          feedStatusBadge.style.color = "var(--go)";
          feedStatusBadge.style.background = "var(--go-bg)";
        }
        if (sessionTitleDisplay) {
          sessionTitleDisplay.textContent = data.feed_title || "Ingestion: WebSocket Stream";
          sessionTitleDisplay.style.color = "#4ade80";
        }
        if (sessionSubstateDisplay) {
          sessionSubstateDisplay.textContent = data.feed_subtext || "Live direct exchange tick stream";
        }
      }

      // Ephemeral Pre-Bell briefing: 08:45–09:22 IST, client clock is the hard stop
      syncCockpitPreBellCard(data.is_pre_bell_ephemeral);
      if (data.is_pre_bell_ephemeral && isClientPreBellWindow()) {
        loadCockpit();
      }

      // Token manager status
      const tokPill = document.getElementById("token-status-pill");
      if (tokPill) {
        if (data.dhan_token && data.dhan_token.present) {
          tokPill.textContent = `Active (${data.dhan_token.hours_left.toFixed(0)}h)`;
          tokPill.style.background = "var(--go-bg)";
          tokPill.style.color = "var(--go)";
        } else {
          tokPill.textContent = "Expired / None";
          tokPill.style.background = "var(--stop-bg)";
          tokPill.style.color = "var(--stop)";
        }
      }
    } catch (e) {
      console.error("Error loading status:", e);
    }
  }

  function paintStrikeKpi(elId, node, subId) {
    const el = document.getElementById(elId);
    if (!el || !node) return;
    const val = Number(node.val);
    if (Number.isFinite(val) && val > 0) {
      el.textContent = `₹${Math.round(val).toLocaleString("en-IN")}`;
    }
    if (subId && node.sub) {
      const subEl = document.getElementById(subId);
      if (subEl) subEl.textContent = node.sub;
    }
  }

  function paintWallKpisFromTick(tick) {
    if (!tick) return;
    const tickSym = (tick.symbol || "").toUpperCase();
    const activeSym = (state.symbol || "").toUpperCase();
    if (tickSym && activeSym && tickSym !== activeSym) return;
    const cw = Number(tick.call_wall);
    const pw = Number(tick.put_wall);
    const cwEl = document.getElementById("kpi-call-wall");
    const pwEl = document.getElementById("kpi-put-wall");
    if (cwEl && Number.isFinite(cw) && cw > 0) {
      cwEl.textContent = `₹${Math.round(cw).toLocaleString("en-IN")}`;
    }
    if (pwEl && Number.isFinite(pw) && pw > 0) {
      pwEl.textContent = `₹${Math.round(pw).toLocaleString("en-IN")}`;
    }
    const gfEl = document.getElementById("kpi-gamma-flip");
    const gf = Number(tick.gamma_flip);
    if (gfEl && Number.isFinite(gf) && gf > 0 && (gfEl.textContent || "").trim() === "—") {
      gfEl.textContent = `₹${Math.round(gf).toLocaleString("en-IN")}`;
    }
  }

  function renderBookStructure(pack) {
    const bucketEl = document.getElementById("book-structure-bucket");
    const playEl = document.getElementById("book-structure-playbook");
    const variantsEl = document.getElementById("book-structure-variants");
    const legsEl = document.getElementById("book-structure-legs");
    const whyEl = document.getElementById("book-structure-why");
    const riskEl = document.getElementById("book-structure-risk");
    if (!legsEl || !whyEl || !riskEl) return;
    const candidates = (pack && pack.candidates) || [];
    if (bucketEl) bucketEl.textContent = (pack && pack.bucket_label) || "Middle concentration — wait";
    if (playEl) playEl.textContent = (pack && pack.playbook) || "";
    if (variantsEl) variantsEl.innerHTML = "";
    state.structureVariant = 0;
    const paint = (idx) => {
      state.structureVariant = idx;
      const c = candidates[idx] || candidates[0];
      if (!c) {
        legsEl.textContent = "";
        whyEl.textContent = "Concentration gauge unavailable. Wait.";
        riskEl.textContent = "";
        return;
      }
      const legs = (c.legs || []).map((leg) => {
        const strike = Number(leg.strike).toLocaleString("en-IN");
        return `${leg.action} ${strike} ${leg.right}`;
      });
      legsEl.textContent = c.name + (legs.length ? " · " + legs.join("  ·  ") : "");
      whyEl.textContent = c.rationale || "";
      const bits = [c.risk_note, c.exit_by, c.expiry_note].filter((s) => s && s !== "—");
      riskEl.textContent = bits.join(" · ");
    };
    if (variantsEl && candidates.length > 1) {
      candidates.forEach((c, i) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-secondary";
        btn.style.fontSize = "0.72rem";
        btn.style.padding = "4px 10px";
        btn.textContent = i === 0 ? "Primary" : "Alternate";
        btn.addEventListener("click", () => paint(i));
        variantsEl.appendChild(btn);
      });
    }
    if (variantsEl && candidates.some((c) => (c.legs || []).length)) {
      const trade = document.createElement("button");
      trade.type = "button";
      trade.className = "btn btn-primary";
      trade.style.fontSize = "0.72rem";
      trade.style.padding = "4px 10px";
      trade.textContent = "Paper-trade this structure";
      trade.addEventListener("click", () => window.paperTradeStructure());
      variantsEl.appendChild(trade);
    }
    paint(0);
  }

  window.paperTradeStructure = async function () {
    try {
      const res = await fetch("/api/journal/paper-structure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: state.symbol,
          window: state.window,
          variant: state.structureVariant || 0,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || "Structure was not booked");
        return;
      }
      playSuccessChime();
      showToast(`Paper trade ${data.trade_id.slice(-6)} · net ${data.net_premium}`);
      loadJournal();
    } catch (e) {
      showToast("Paper trade failed");
    }
  };

  function paintParticipantHoldings(book) {
    const body = document.getElementById("participant-body");
    const dateEl = document.getElementById("participant-date");
    if (dateEl) dateEl.textContent = book && book.date ? book.date : "";
    if (!body) return;
    const rows = (book && book.rows) || [];
    const cell = (value) => value == null ? "—" : Number(value).toLocaleString("en-IN");
    body.innerHTML = rows.length
      ? rows.map((row) => `<tr><td>${row.participant}</td><td>${cell(row.fut_long)}</td><td>${cell(row.fut_short)}</td><td>${cell(row.opt_call_long)}</td><td>${cell(row.opt_call_short)}</td><td>${cell(row.opt_put_long)}</td><td>${cell(row.opt_put_short)}</td></tr>`).join("")
      : '<tr><td colspan="7">No participant file.</td></tr>';
  }

  async function loadCockpit() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/cockpit-data?symbol=${encodeURIComponent(requestedSym)}&window=${state.window}`);
      const data = await res.json();
      if (!res.ok) {
        showToast(data.detail || "Cockpit data unavailable");
        return;
      }
      // Discard stale in-flight response if index switched
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) {
        return;
      }

      if (!data.kpis) return;
      state.lastCockpitDash = data;
      if (state.spot > 0) data.spot = state.spot;
      const valueLine = document.getElementById("value-area-line");
      if (valueLine) valueLine.textContent = (data.value_area && data.value_area.text) || "No prior value area.";
      paintParticipantHoldings(data.participant_holdings);
      syncCockpitPreBellCard(data.is_pre_bell_ephemeral);
      paintStrikeKpi("kpi-call-wall", data.kpis.call_wall, "kpi-call-wall-sub");
      paintStrikeKpi("kpi-put-wall", data.kpis.put_wall, "kpi-put-wall-sub");

      if (data.kpis.gamma_flip) {
        const gfEl = document.getElementById("kpi-gamma-flip");
        const gfSubEl = document.getElementById("kpi-gamma-flip-sub");
        const rawGf = data.kpis.gamma_flip.raw !== undefined ? data.kpis.gamma_flip.raw : data.kpis.gamma_flip.val;
        const numGf = Number(rawGf);
        if (numGf > 0) {
          gfEl.textContent = `₹${Math.round(numGf).toLocaleString("en-IN")}`;
        } else if (typeof data.kpis.gamma_flip.val === "string" && data.kpis.gamma_flip.val.startsWith("₹")) {
          gfEl.textContent = data.kpis.gamma_flip.val;
        } else if (data.kpis.gamma_flip.val && data.kpis.gamma_flip.val !== "None" && data.kpis.gamma_flip.val !== 0) {
          gfEl.textContent = String(data.kpis.gamma_flip.val);
        } else {
          gfEl.textContent = "None (Unbounded)";
        }
        if (gfSubEl && data.kpis.gamma_flip.sub) {
          gfSubEl.textContent = data.kpis.gamma_flip.sub;
        }
      }

      const mpEl = document.getElementById("kpi-max-pain");
      const mpSubEl = document.getElementById("kpi-max-pain-sub");
      if (mpEl && data.kpis.max_pain) mpEl.textContent = `₹${data.kpis.max_pain.val.toLocaleString("en-IN")}`;
      if (mpSubEl && data.kpis.max_pain) mpSubEl.textContent = data.kpis.max_pain.sub;

      const dbEl = document.getElementById("kpi-directional-bias");
      const dbModeEl = document.getElementById("kpi-directional-bias-mode");
      if (dbEl && data.kpis.directional_bias) {
        dbEl.textContent = data.kpis.directional_bias.val;
        const rawScore = parseFloat(data.kpis.directional_bias.raw !== undefined ? data.kpis.directional_bias.raw : data.kpis.directional_bias.val);
        dbEl.style.color = rawScore > 0.8 ? "var(--go)" : (rawScore < -0.8 ? "var(--stop)" : "var(--spark)");
      }
      if (dbModeEl && data.kpis.directional_bias) dbModeEl.textContent = data.kpis.directional_bias.sub;

      if (data.kpis.regime) {
        document.getElementById("kpi-regime").textContent = data.kpis.regime.val;
        document.getElementById("kpi-regime-mode").textContent = data.kpis.regime.sub;
      }
      if (data.kpis.net_gex) {
        document.getElementById("kpi-net-gex").textContent = data.kpis.net_gex.val;
        const netSub = document.getElementById("kpi-net-gex-sub");
        if (netSub && data.kpis.net_gex.sub) netSub.textContent = data.kpis.net_gex.sub;
      }
      if (data.kpis.pcr_oi) {
        document.getElementById("kpi-pcr").textContent = data.kpis.pcr_oi.val;
        const pcrSub = document.getElementById("kpi-pcr-sub");
        if (pcrSub && data.kpis.pcr_oi.sub) pcrSub.textContent = data.kpis.pcr_oi.sub;
      }
      renderBookStructure(data.structures);

      const execStatusEl = document.getElementById("cockpit-exec-status");
      if (execStatusEl) {
        if (data.exec_session_status) {
          execStatusEl.textContent = data.exec_session_status;
        } else if (!state.isMarketOpen) {
          const reg = data.kpis && data.kpis.regime ? data.kpis.regime.val : "Normal";
          execStatusEl.textContent = `Snapshot Locked at ₹${data.spot.toLocaleString("en-IN")} · ${reg} Mode`;
        } else {
          execStatusEl.textContent = `${data.kpis.regime.val} Mode · Spot ₹${data.spot.toLocaleString("en-IN")}`;
        }
        execStatusEl.style.color = state.isMarketOpen ? "var(--go)" : "var(--ink-mute)";
      }

      if (data.market_breadth) {
        const mb = data.market_breadth;
        const bEl = document.getElementById("cockpit-breadth-val");
        if (bEl) {
          bEl.textContent = `${mb.advances} Adv / ${mb.declines} Dec (${mb.ratio}x)`;
          bEl.style.color = mb.ratio >= 1.0 ? "var(--go)" : "var(--stop)";
        }
      }

      const cdEl = document.getElementById("cockpit-phase-countdown");
      if (cdEl) {
        if (data.next_phase_countdown) {
          cdEl.textContent = data.next_phase_countdown;
        } else if (data.session_schedule && data.session_schedule.exec_phase_countdown) {
          cdEl.textContent = data.session_schedule.exec_phase_countdown;
        } else if (!state.isMarketOpen) {
          cdEl.textContent = getClientReopenCountdown();
        } else {
          cdEl.textContent = "Live Regular Session (to 15:30 IST)";
        }
        cdEl.style.color = state.isMarketOpen ? "var(--go)" : "var(--ink-mute)";
      }

      // Pre-Market Discovery Summary & 5-Strike Opening Shocks
      if (data.pre_market) {
        const pm = data.pre_market;
        const discovered = Boolean(pm.is_discovered && Number(pm.discovered_spot) > 0);
        const discSpot = discovered ? Number(pm.discovered_spot) : 0;
        const gapPts = discovered ? Number(pm.gap_pts || 0) : 0;
        const gapPct = discovered ? Number(pm.gap_pct || 0) : 0;
        const signStr = gapPts >= 0 ? "+" : "";

        const discEl = document.getElementById("pm-indicative-open");
        if (discEl) {
          discEl.textContent = discovered
            ? `₹${discSpot.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`
            : (pm.prev_close ? `Prev ₹${Number(pm.prev_close).toLocaleString("en-IN", { minimumFractionDigits: 1 })}` : "—");
        }
        const gapEl = document.getElementById("pm-opening-gap");
        if (gapEl) {
          if (discovered) {
            gapEl.textContent = `${signStr}${gapPts.toFixed(1)} pts (${signStr}${gapPct.toFixed(2)}%)`;
            gapEl.style.color = gapPts >= 0 ? "var(--go)" : "var(--stop)";
          } else {
            gapEl.textContent = "Awaiting 09:08 match";
            gapEl.style.color = "var(--ink-mute)";
          }
        }
        const wallsEl = document.getElementById("pm-walls");
        if (wallsEl) {
          const cw = Number(pm.call_wall || (data.kpis && data.kpis.call_wall && data.kpis.call_wall.val) || 0);
          const pw = Number(pm.put_wall || (data.kpis && data.kpis.put_wall && data.kpis.put_wall.val) || 0);
          wallsEl.textContent = cw > 0 && pw > 0
            ? `${cw.toLocaleString("en-IN")} / ${pw.toLocaleString("en-IN")}`
            : "—";
        }
        const playEl = document.getElementById("pm-favored-play");
        if (playEl) playEl.textContent = discovered ? (pm.favored_play || "—") : "Setup only · no open yet";
        const badgeEl = document.getElementById("pm-regime-badge");
        if (badgeEl) {
          badgeEl.textContent = discovered
            ? (pm.regime_badge || "PRE-MARKET DISCOVERY")
            : (pm.is_morning_briefing ? (pm.regime_badge || "PRIOR-SESSION BOOK") : "PRE-OPEN SETUP");
          if (pm.regime_color) badgeEl.style.color = pm.regime_color;
        }
        const briefingEl = document.getElementById("pm-briefing-text");
        if (briefingEl) briefingEl.textContent = pm.regime_briefing || pm.briefing || pm.status_text || "Prior-session gamma strikes. Equilibrium prints around 09:08 IST.";
        const atEl = document.getElementById("pm-discovered-at");
        if (atEl) {
          if (discovered && pm.discovered_at) atEl.textContent = `${datedClock(pm.discovered_at, pm.date)} Discovery`;
          else if (pm.is_morning_briefing) {
            const oiShown = pm.oi_as_of_display || formatValidStamp(pm.oi_as_of);
            atEl.textContent = oiShown ? `OI ${oiShown}` : "OI time unavailable";
          }
          else atEl.textContent = "09:08 IST Discovery";
        }

        // Straddle badge
        const straddleEl = document.getElementById("pm-straddle-badge");
        if (straddleEl && pm.atm_straddle_prev) {
          const straddleClr = (pm.straddle_shock_pct || 0) >= 0 ? "var(--go)" : "var(--stop)";
          straddleEl.innerHTML = `ATM Straddle: ₹${pm.atm_straddle_prev.toFixed(1)} ➔ <b>₹${pm.atm_straddle_projected.toFixed(1)}</b> (<span style="color:${straddleClr};font-weight:700">${(pm.straddle_shock_pct >= 0 ? "+" : "")}${pm.straddle_shock_pct.toFixed(1)}%</span>)`;
        }

        // Shocks Table (5 Strikes around ATM)
        const shockBody = document.getElementById("pm-shocks-body");
        if (shockBody && pm.projected_strikes) {
          shockBody.innerHTML = "";
          // Group projected strikes by strike price
          const strikesMap = {};
          pm.projected_strikes.forEach((s) => {
            strikesMap[s.strike] = strikesMap[s.strike] || {};
            strikesMap[s.strike][s.side] = s;
          });

          Object.keys(strikesMap)
            .sort((a, b) => parseFloat(a) - parseFloat(b))
            .forEach((sk) => {
              const ce = strikesMap[sk].CE || { prev_ltp: 0, projected_open_ltp: 0, shock_pct: 0, shock_pts: 0 };
              const pe = strikesMap[sk].PE || { prev_ltp: 0, projected_open_ltp: 0, shock_pct: 0, shock_pts: 0 };
              const isAtm = ce.is_atm || pe.is_atm;
              const tr = document.createElement("tr");
              if (isAtm) tr.className = "atm-shock-row";

              const ceClr = ce.shock_pts >= 0 ? "var(--go)" : "var(--stop)";
              const peClr = pe.shock_pts >= 0 ? "var(--go)" : "var(--stop)";
              const atmStar = isAtm ? ' <span class="atm-star">ATM</span>' : "";

              tr.innerHTML = `
                <td style="color:var(--ink-mute)">₹${ce.prev_ltp.toFixed(1)}</td>
                <td style="color:var(--go);font-weight:700">₹${ce.projected_open_ltp.toFixed(1)}</td>
                <td style="color:${ceClr};font-weight:700">${ce.shock_pct >= 0 ? "+" : ""}${ce.shock_pct.toFixed(0)}%</td>
                <td style="font-weight:800;color:var(--ink);font-family:var(--mono)">${parseFloat(sk).toLocaleString("en-IN")}${atmStar}</td>
                <td style="color:var(--ink-mute)">₹${pe.prev_ltp.toFixed(1)}</td>
                <td style="color:var(--stop);font-weight:700">₹${pe.projected_open_ltp.toFixed(1)}</td>
                <td style="color:${peClr};font-weight:700">${pe.shock_pct >= 0 ? "+" : ""}${pe.shock_pct.toFixed(0)}%</td>
              `;
              shockBody.appendChild(tr);
            });
        }
      }

      // OrderFlow Alerts (Tape) Quadrant
      const alertsContainer = document.getElementById("cockpit-orderflow-alerts-container");
      if (alertsContainer && data.orderflow_alerts) {
        alertsContainer.innerHTML = "";
        data.orderflow_alerts.forEach((a) => {
          const div = document.createElement("div");
          div.className = "signal-card";
          const scoreClr = a.score >= 80 ? "var(--go)" : a.score >= 65 ? "var(--spark)" : "var(--warn)";
          div.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div style="display:flex;align-items:center;gap:6px">
                <span style="font-family:var(--mono);font-size:0.7rem;color:var(--ink-faint)">${a.time}</span>
                <b style="font-size:0.8rem;color:var(--spark)">${a.type}</b>
                <span style="font-size:0.65rem;background:var(--surface-3);padding:1px 6px;border-radius:3px">${a.category}</span>
              </div>
              <span style="font-size:0.72rem;font-weight:800;color:${scoreClr}">${a.score}/100</span>
            </div>
            <div style="font-size:0.76rem;color:var(--ink);line-height:1.4">${a.insight_text}</div>
            <div style="display:flex;justify-content:space-between;font-size:0.68rem;color:var(--ink-mute);font-family:var(--mono);border-top:1px dashed var(--line);padding-top:4px">
              <span>Delta: <b style="color:${a.delta >= 0 ? "var(--go)" : "var(--stop)"}">${a.delta >= 0 ? "+" : ""}${a.delta}</b></span>
              <span>RVOL: <b>${a.rvol_tod}x</b></span>
              <span>Inval: <b>₹${a.invalidation_level.toLocaleString("en-IN")}</b></span>
            </div>
          `;
          alertsContainer.appendChild(div);
        });
      }

      // Active Paper Positions in Cockpit
      try {
        const jRes = await fetch(`/api/journal?symbol=${state.symbol}`);
        const jData = await jRes.json();
        const mtmBadge = document.getElementById("cockpit-open-mtm-badge");
        if (mtmBadge) {
          const mtm = jData.total_unrealized_mtm || 0;
          mtmBadge.textContent = `MTM: ${mtm >= 0 ? "+" : ""}₹${mtm.toLocaleString("en-IN")}`;
          mtmBadge.style.color = mtm >= 0 ? "var(--go)" : "var(--stop)";
        }
        const openBody = document.getElementById("cockpit-open-positions-body");
        if (openBody && jData.open_positions) {
          openBody.innerHTML = "";
          if (jData.open_positions.length === 0) {
            openBody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:12px;color:var(--ink-mute)">No active positions</td></tr>';
          } else {
            jData.open_positions.slice(0, 4).forEach((p) => {
              const tr = document.createElement("tr");
              tr.innerHTML = `
                <td><b>${p.strategy.slice(0, 18)}</b></td>
                <td>${p.lot_size}</td>
                <td>₹${p.entry_prem.toFixed(1)}</td>
                <td>₹${p.current_prem.toFixed(1)}</td>
                <td style="color:${p.unrealized_pnl >= 0 ? "var(--go)" : "var(--stop)"};font-weight:700">${p.unrealized_pnl >= 0 ? "+" : ""}₹${p.unrealized_pnl.toLocaleString("en-IN")}</td>
              `;
              openBody.appendChild(tr);
            });
          }
        }
      } catch (e) {}

      // Gamma blast card
      const gbBadge = document.getElementById("gamma-blast-badge");
      const gbDesc = document.getElementById("gamma-blast-desc");
      const gbScore = document.getElementById("gamma-blast-score");
      const gbCard = document.getElementById("gamma-blast-card");

      if (gbBadge) gbBadge.textContent = data.gamma_blast.badge;
      if (gbDesc) gbDesc.textContent = data.gamma_blast.description;
      if (gbScore) gbScore.textContent = `${data.gamma_blast.score} / 100`;

      const scoreVal = Number(data.gamma_blast.score || 0);
      let themeColor = "var(--go)";
      if (scoreVal >= 60 || (data.gamma_blast && data.gamma_blast.state === "TRIGGERED")) {
        themeColor = "var(--stop)";
      } else if (scoreVal >= 35 || (data.gamma_blast && data.gamma_blast.state === "COILING")) {
        themeColor = "var(--spark)";
      }

      if (gbBadge) gbBadge.style.color = themeColor;
      if (gbCard) gbCard.style.borderLeftColor = themeColor;

      const crossBody = document.getElementById("crossover-watch-body");
      if (crossBody) {
        const anchors = (data.crossover_watch && data.crossover_watch.anchors) || [];
        crossBody.innerHTML = anchors.length
          ? anchors.map((a) => {
              const strike = a.strike == null ? "—" : `₹${Number(a.strike).toLocaleString("en-IN")}`;
              const prem = (a.call == null || a.put == null) ? "" : ` · call ${a.call} / put ${a.put}`;
              return `<div style="margin-bottom:6px"><b>${a.name} ${strike}</b>${prem}<div style="color:var(--ink-mute)">${a.location}</div></div>`;
            }).join("")
          : "No paired premiums on this read.";
      }
      const magnetBody = document.getElementById("magnet-path-body");
      if (magnetBody) {
        const path = data.magnet_path || [];
        magnetBody.textContent = path.length
          ? path.map((row) => `${formatValidStamp(row.date) || row.date}  ₹${Number(row.magnet).toLocaleString("en-IN")}`).join("   ·   ")
          : "No captured sessions.";
      }
      const futState = document.getElementById("futures-state");
      if (futState) {
        const fut = data.futures_state;
        const futAsOf = fut && fut.as_of ? formatValidStamp(fut.as_of) : "";
        futState.textContent = fut
          ? `${fut.state}${futAsOf ? ` · ${futAsOf}` : ""}`
          : "No futures open-interest change on this read.";
      }

      // Update Cockpit TradingView Live Chart
      if (typeof updateCockpitChart === "function") {
        updateCockpitChart(data);
      }

      // Gamma Price Ladder
      const ladderFlipVal = document.getElementById("ladder-flip-val");
      if (ladderFlipVal) {
        const rawGf = (data.kpis && data.kpis.gamma_flip && data.kpis.gamma_flip.raw) || data.gamma_flip;
        const numGf = Number(rawGf);
        if (numGf > 0) {
          ladderFlipVal.textContent = `₹${Math.round(numGf).toLocaleString("en-IN")}`;
        } else {
          ladderFlipVal.textContent = "None (Unbounded)";
        }
      }

      const tbody = document.getElementById("gamma-ladder-body");
      if (tbody) {
        tbody.innerHTML = "";
        data.ladder.forEach((row) => {
          const tr = document.createElement("tr");
          tr.className = `ladder-row ${row.is_spot ? "spot-row" : ""}`;

          let badgeHtml = "";
          if (row.is_call_wall) badgeHtml += '<span class="ladder-badge" style="background:rgba(239,68,68,.15);color:var(--stop)">CALL WALL</span>';
          if (row.is_put_wall) badgeHtml += '<span class="ladder-badge" style="background:rgba(34,197,94,.15);color:var(--go)">PUT WALL</span>';
          if (row.is_gamma_flip) badgeHtml += '<span class="ladder-badge" style="background:rgba(56,189,248,.15);color:var(--spark)">GAMMA FLIP</span>';
          if (row.is_max_pain) badgeHtml += '<span class="ladder-badge" style="background:rgba(234,179,8,.15);color:var(--warn)">MAX PAIN</span>';
          if (row.is_magnet) badgeHtml += '<span class="ladder-badge" style="background:rgba(168,85,247,.15);color:var(--purple)">MAGNET</span>';
          if (row.is_put_oi) badgeHtml += '<span class="ladder-badge" style="background:rgba(34,197,94,.08);color:var(--go)">PUT OI</span>';
          if (row.is_call_oi) badgeHtml += '<span class="ladder-badge" style="background:rgba(239,68,68,.08);color:var(--stop)">CALL OI</span>';
          if (row.is_spot) badgeHtml += '<span class="ladder-badge" style="background:var(--spark);color:#000">ATM SPOT</span>';
          if (row.is_prior_high) badgeHtml += '<span class="ladder-badge">PRIOR HIGH</span>';
          if (row.is_prior_low) badgeHtml += '<span class="ladder-badge">PRIOR LOW</span>';
          if (row.is_prior_close) badgeHtml += '<span class="ladder-badge">PRIOR CLOSE</span>';
          const doiText = (row.call_doi == null && row.put_doi == null)
            ? "—"
            : `${row.call_doi == null ? "—" : (row.call_doi > 0 ? "+" : "") + Number(row.call_doi).toLocaleString("en-IN")} / ${row.put_doi == null ? "—" : (row.put_doi > 0 ? "+" : "") + Number(row.put_doi).toLocaleString("en-IN")}`;

          tr.innerHTML = `
            <td>₹${row.strike.toLocaleString("en-IN")}</td>
            <td>${badgeHtml || "—"}</td>
            <td style="color:var(--stop)">${row.call_gex.toLocaleString("en-IN")}</td>
            <td style="color:var(--go)">${row.put_gex.toLocaleString("en-IN")}</td>
            <td style="color:${row.net_gex >= 0 ? "var(--go)" : "var(--stop)"}">${row.net_gex >= 0 ? "+" : ""}${row.net_gex.toLocaleString("en-IN")}</td>
            <td style="font-family:var(--mono)">${doiText}</td>
          `;
          tbody.appendChild(tr);
        });
      }

      const oi = data.standing_oi || {};
      const fmtCentre = (v) => (v == null || v === "" ? "—" : `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 1 })}`);
      const putOiEl = document.getElementById("standing-oi-put");
      const callOiEl = document.getElementById("standing-oi-call");
      const oiNoteEl = document.getElementById("standing-oi-note");
      if (putOiEl) {
        putOiEl.textContent = fmtCentre(oi.put_centre);
        if (oi.put_top) putOiEl.textContent += ` · largest ${fmtCentre(oi.put_top)}`;
      }
      if (callOiEl) {
        callOiEl.textContent = fmtCentre(oi.call_centre);
        if (oi.call_top) callOiEl.textContent += ` · largest ${fmtCentre(oi.call_top)}`;
      }
      if (oiNoteEl) oiNoteEl.textContent = oi.note || "";

      // Active Cockpit Recommendations (Pinned + Fresh within 10m)
      const pinnedContainer = document.getElementById("pinned-setups-container");
      const recCountEl = document.getElementById("cockpit-rec-count");
      const recSubEl = document.getElementById("cockpit-rec-subtitle");

      const recs = data.cockpit_recommendations || (data.pinned_setups || []).map((p) => ({
        ...p,
        is_pinned: true,
        is_fresh: false,
      }));
      const totalCards = data.total_cockpit_cards !== undefined ? data.total_cockpit_cards : recs.length;
      const pinnedCount = data.pinned_count !== undefined ? data.pinned_count : (data.pinned_setups ? data.pinned_setups.length : 0);
      const freshCount = data.fresh_count !== undefined ? data.fresh_count : 0;

      if (recCountEl) recCountEl.textContent = totalCards;
      if (recSubEl) {
        if (totalCards > 0) {
          recSubEl.textContent = freshCount > 0
            ? `${pinnedCount} Pinned · ${freshCount} Fresh (< 10m Flash)`
            : `${pinnedCount} Pinned Card${pinnedCount !== 1 ? "s" : ""}`;
        } else {
          recSubEl.textContent = "Cockpit Watchlist · Preserved across scans";
        }
      }

      if (pinnedContainer) {
        if (totalCards === 0) {
          pinnedContainer.innerHTML = `
            <div style="background:var(--surface-2);border:1px dashed var(--line);border-radius:8px;padding:22px 20px;text-align:center;margin-bottom:14px">
              <div style="font-size:1.3rem;margin-bottom:6px">📌</div>
              <div style="font-weight:700;font-size:.84rem;color:var(--ink)">No Pinned or Fresh Setups in Cockpit</div>
              <div style="font-size:.76rem;color:var(--ink-mute);max-width:540px;margin:4px auto 0;line-height:1.4">
                Browse active Grade A+ setups in the <b>⚡ Trade Recommendations</b> tab and click <b>☆ Pin Card</b>. Fresh recommendations generated within the last 10 minutes will also surface here automatically with a flashing <b style="color:var(--spark)">NEW</b> badge.
              </div>
            </div>
          `;
        } else {
          const ceCards = recs.filter((r) => String(r.side).toUpperCase() === "CE");
          const peCards = recs.filter((r) => String(r.side).toUpperCase() === "PE");

          function renderCardItem(c) {
            const sideColor = String(c.side).toUpperCase() === "CE" ? "var(--go)" : "var(--stop)";
            const badgeHtml = c.is_fresh
              ? '<span style="background:rgba(255,160,0,0.18);color:#ffa000;border:1px solid #ffa000;font-size:0.65rem;font-weight:800;padding:2px 6px;border-radius:4px;letter-spacing:0.04em">⚡ NEW (&lt; 10m)</span>'
              : '<span style="background:rgba(0,180,216,0.18);color:var(--spark);border:1px solid var(--spark);font-size:0.65rem;font-weight:800;padding:2px 6px;border-radius:4px;letter-spacing:0.04em">📌 PINNED</span>';

            const ltp = Number(c.current_ltp || c.entry_ltp || 0).toFixed(1);
            const t1 = Number(c.target_1 || 0).toFixed(1);
            const sl = Number(c.stop_loss || 0).toFixed(1);
            const score = c.quality_score || 85;

            return `
              <div style="background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:8px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                  <div style="display:flex;align-items:center;gap:6px">
                    ${badgeHtml}
                    <b style="color:${sideColor};font-size:0.86rem">${c.option_symbol || (c.symbol + " " + c.strike + " " + c.side)}</b>
                  </div>
                  <span style="font-size:0.74rem;color:var(--ink-mute)">Score: <b style="color:var(--spark)">${score}/100</b></span>
                </div>
                <div style="display:flex;justify-content:space-between;font-size:0.75rem;margin-bottom:8px;color:var(--ink-mute)">
                  <span>LTP: <b id="cockpit-rec-ltp-${c.rec_id}" style="color:var(--ink)">₹${ltp}</b></span>
                  <span>T1: <b id="cockpit-rec-t1-${c.rec_id}" style="color:var(--go)">₹${t1}</b></span>
                  <span>SL: <b id="cockpit-rec-sl-${c.rec_id}" style="color:var(--stop)">₹${sl}</b></span>
                </div>
                <div style="display:flex;justify-content:flex-end;gap:6px">
                  ${
                    c.is_pinned
                      ? `<button class="btn btn-secondary" onclick="window.unpinCard('${c.rec_id}')" style="font-size:0.70rem;padding:3px 7px">★ Pinned (Remove)</button>`
                      : `<button class="btn btn-secondary" onclick="window.toggleBookmark('${c.rec_id}')" style="font-size:0.70rem;padding:3px 7px">☆ Pin Card</button>`
                  }
                  <button class="btn btn-primary" onclick="window.executePaperTrade('${c.rec_id}', '${c.option_symbol || ""}', ${c.strike}, '${c.side}', ${Number(c.current_ltp || c.entry_ltp || 0)}, 1)" style="font-size:0.70rem;padding:3px 9px">⚡ Execute</button>
                </div>
              </div>
            `;
          }

          pinnedContainer.innerHTML = `
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:12px">
              <div>
                <div style="font-size:0.75rem;font-weight:750;color:var(--go);margin-bottom:6px;display:flex;justify-content:space-between">
                  <span>🟢 CALL SETUPS (${ceCards.length})</span>
                </div>
                ${ceCards.length > 0 ? ceCards.map(renderCardItem).join("") : '<div style="font-size:0.75rem;color:var(--ink-mute);padding:8px">No active Call setups</div>'}
              </div>
              <div>
                <div style="font-size:0.75rem;font-weight:750;color:var(--stop);margin-bottom:6px;display:flex;justify-content:space-between">
                  <span>🔴 PUT SETUPS (${peCards.length})</span>
                </div>
                ${peCards.length > 0 ? peCards.map(renderCardItem).join("") : '<div style="font-size:0.75rem;color:var(--ink-mute);padding:8px">No active Put setups</div>'}
              </div>
            </div>
          `;
        }
      }

      // Update Flashing Sideways Marquee Alert Tape
      updateMarketAlertTape(data);
    } catch (e) {
      console.error("Error loading cockpit:", e);
    }
  }

  // -------------------------------------------------------------------------
  // Cockpit TradingView Live Chart Module (Selected Index, 1m, 5min, 15m)
  // -------------------------------------------------------------------------
  const cockpitChartState = {
    symbol: "NIFTY",
    timeframe: "5m", // "1m" | "5m" | "15m"
    engine: "gex_canvas", // "gex_canvas" (TV Lightweight Canvas with Live Dhan Ticks) | "tv_widget"
    height: 520,
    cw: 0,
    pw: 0,
    flip: 0,
    dragInitialized: false,
  };

  let cockpitGexChart = null;
  let cockpitCandleSeries = null;
  let cockpitVwapSeries = null;
  let cockpitUp1Series = null;
  let cockpitDn1Series = null;
  let cockpitEma9Series = null;
  let cockpitEma21Series = null;
  let cockpitDeltaSeries = null;
  let cockpitCwLine = null;
  let cockpitPwLine = null;
  let cockpitFlipLine = null;

  function updateCockpitChart(data) {
    if (data && data.kpis) {
      cockpitChartState.cw = Number(data.kpis.call_wall ? data.kpis.call_wall.val : 0) || 0;
      cockpitChartState.pw = Number(data.kpis.put_wall ? data.kpis.put_wall.val : 0) || 0;
      cockpitChartState.flip = Number(data.kpis.gamma_flip ? data.kpis.gamma_flip.val : 0) || 0;

      const cwEl = document.getElementById("cockpit-legend-cw-val");
      const pwEl = document.getElementById("cockpit-legend-pw-val");
      const flipEl = document.getElementById("cockpit-legend-flip-val");
      if (cwEl) cwEl.textContent = cockpitChartState.cw ? `₹${cockpitChartState.cw.toLocaleString("en-IN")}` : "--";
      if (pwEl) pwEl.textContent = cockpitChartState.pw ? `₹${cockpitChartState.pw.toLocaleString("en-IN")}` : "--";
      if (flipEl) flipEl.textContent = cockpitChartState.flip ? `₹${cockpitChartState.flip.toLocaleString("en-IN")}` : "--";
    }

    renderCockpitChart(state.symbol, cockpitChartState.timeframe, cockpitChartState.engine);
  }

  function renderCockpitChart(symbol, tf) {
    cockpitChartState.symbol = (symbol || state.symbol || "NIFTY").toUpperCase();
    cockpitChartState.timeframe = tf || cockpitChartState.timeframe || "5m";
    cockpitChartState.engine = "gex_canvas";

    const symBadge = document.getElementById("cockpit-chart-symbol-badge");
    if (symBadge) symBadge.textContent = cockpitChartState.symbol;

    const chlSym = document.getElementById("chl-sym");
    if (chlSym) chlSym.textContent = cockpitChartState.symbol;

    const isClosed = state.is_market_open === false || state.market_state === "CLOSED";
    const tfBadge = document.getElementById("cockpit-chart-badge");
    if (tfBadge && !state.replayMode) {
      const tfName = cockpitChartState.timeframe === "5m" ? "5min" : cockpitChartState.timeframe;
      tfBadge.textContent = isClosed ? `${tfName} · EOD (CLOSED)` : `${tfName} · LIVE`;
      tfBadge.style.color = isClosed ? "var(--ink-mute)" : "var(--go)";
      tfBadge.style.borderColor = isClosed ? "var(--line)" : "rgba(34, 197, 94, 0.4)";
    }

    const symTag = document.getElementById("legend-sym-tag");
    if (symTag) symTag.textContent = `INDEX: ${cockpitChartState.symbol}`;

    const tfTag = document.getElementById("legend-tf-tag");
    if (tfTag) tfTag.textContent = `Timeframe: ${cockpitChartState.timeframe === "5m" ? "5min" : cockpitChartState.timeframe}`;

    // Update Timeframe button active states
    ["1m", "5m", "15m"].forEach((t) => {
      const btn = document.getElementById(`btn-cockpit-tf-${t}`);
      if (btn) {
        btn.className = t === cockpitChartState.timeframe ? "btn btn-primary" : "btn btn-secondary";
      }
    });

    const gexContainer = document.getElementById("cockpit-gex-canvas-container");
    const hoverHud = document.getElementById("cockpit-hover-legend");
    if (gexContainer) gexContainer.style.display = "block";
    if (hoverHud) hoverHud.style.display = "flex";

    loadCockpitGexCanvas(cockpitChartState.symbol, cockpitChartState.timeframe);
    initCockpitChartDragHandle();
  }

  function renderTradingViewWidget(symbol, tf) {
    const mount = document.getElementById("cockpit-tv-widget-container");
    if (!mount) return;
    mount.innerHTML = "";

    const tvSymbol = symbol === "SENSEX" ? "BSE:SENSEX" : `NSE:${symbol}`;
    const intervalMap = { "1m": "1", "5m": "5", "15m": "15" };
    const interval = intervalMap[tf] || "5";

    const tvMountId = "tv_chart_container_" + Math.random().toString(36).substring(7);
    const wrapper = document.createElement("div");
    wrapper.id = tvMountId;
    wrapper.style.width = "100%";
    wrapper.style.height = "100%";
    mount.appendChild(wrapper);

    const isLight = state.theme === "light";
    const tvTheme = isLight ? "light" : "dark";
    const tvBg = isLight ? "#ffffff" : "#0c1424";
    const tvGrid = isLight ? "rgba(15, 23, 42, 0.06)" : "rgba(56, 189, 248, 0.06)";

    if (typeof TradingView !== "undefined" && TradingView.widget) {
      try {
        new TradingView.widget({
          autosize: true,
          symbol: tvSymbol,
          interval: interval,
          timezone: "Asia/Kolkata",
          theme: tvTheme,
          style: "1",
          locale: "en",
          toolbar_bg: tvBg,
          enable_publishing: false,
          allow_symbol_change: false, // Strict symbol binding to selected index
          container_id: tvMountId,
          hide_side_toolbar: false,
          withdateranges: true,
          save_image: false,
          backgroundColor: tvBg,
          gridColor: tvGrid,
        });
        return;
      } catch (err) {
        console.warn("TradingView widget constructor failed, using embed iframe fallback:", err);
      }
    }

    wrapper.innerHTML = `<iframe src="https://s.tradingview.com/widgetembed/?symbol=${encodeURIComponent(tvSymbol)}&interval=${interval}&theme=${tvTheme}&style=1&timezone=Asia%2FKolkata&locale=en" style="width:100%;height:100%;border:none;border-radius:6px" allowfullscreen></iframe>`;
  }

  async function loadCockpitGexCanvas(symbol, tf) {
    const mount = document.getElementById("cockpit-gex-canvas-container");
    if (!mount || !window.LightweightCharts) return;

    const targetSym = (symbol || cockpitChartState.symbol || state.symbol || "NIFTY").toUpperCase();

    if (!cockpitGexChart) {
        mount.innerHTML = "";
        const isCanvasLight = state.theme === "light";
        cockpitGexChart = window.LightweightCharts.createChart(mount, {
          width: mount.clientWidth || 850,
          height: cockpitChartState.height,
          layout: {
            background: { color: isCanvasLight ? "#ffffff" : "#0c1424" },
            textColor: isCanvasLight ? "#334155" : "#94a3b8"
          },
          grid: {
            vertLines: { color: isCanvasLight ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" },
            horzLines: { color: isCanvasLight ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }
          },
          crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
          rightPriceScale: {
            borderColor: isCanvasLight ? "rgba(203, 213, 225, 0.8)" : "rgba(51, 65, 85, 0.45)",
            autoScale: true,
            scaleMargins: { top: 0.08, bottom: 0.25 },
          },
          handleScale: {
            axisPressedMouseMove: { time: true, price: true },
            axisDoubleClickReset: { time: true, price: true },
            mouseWheel: true,
            pinch: true,
          },
          handleScroll: {
            mouseWheel: false, // Directly zoom candles with mouse wheel without needing Ctrl
            pressedMouseMove: true,
            horzTouchDrag: true,
            vertTouchDrag: true,
          },
          timeScale: {
            timeVisible: true,
            secondsVisible: false,
            borderColor: "rgba(51, 65, 85, 0.45)",
            barSpacing: 12,
            minBarSpacing: 3,
            rightOffset: 6,
          },
          localization: {
            timeFormatter: (ts) => istChartStamp(ts) || String(ts),
          },
        });

        cockpitCandleSeries = cockpitGexChart.addCandlestickSeries({
          upColor: "#10b981",
          downColor: "#ef4444",
          borderVisible: true,
          borderUpColor: "#10b981",
          borderDownColor: "#ef4444",
          wickUpColor: "#10b981",
          wickDownColor: "#ef4444",
          title: "Price",
          autoscaleInfoProvider: (original) => {
            const res = original();
            if (res !== null && res.priceRange) {
              let min = res.priceRange.minValue;
              let max = res.priceRange.maxValue;
              const range = Math.max(max - min, 20);
              const cw = cockpitChartState.cw || 0;
              const pw = cockpitChartState.pw || 0;
              // Expand to include Call Wall / Put Wall if reasonably close, without crushing candle bodies
              if (cw > 0 && cw >= min && cw <= max + range * 0.8) {
                max = Math.max(max, cw + 10);
              }
              if (pw > 0 && pw <= max && pw >= min - range * 0.8) {
                min = Math.min(min, pw - 10);
              }
              return {
                priceRange: { minValue: min, maxValue: max },
                margins: res.margins,
              };
            }
            return res;
          },
        });

        cockpitVwapSeries = cockpitGexChart.addLineSeries({
          color: "#c084fc",
          lineWidth: 1.5,
          title: "VWAP",
        });

        cockpitUp1Series = cockpitGexChart.addLineSeries({
          color: "rgba(192, 132, 252, 0.45)",
          lineWidth: 1,
          lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
          title: "+1σ",
        });

        cockpitDn1Series = cockpitGexChart.addLineSeries({
          color: "rgba(192, 132, 252, 0.45)",
          lineWidth: 1,
          lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
          title: "-1σ",
        });

        cockpitEma9Series = cockpitGexChart.addLineSeries({
          color: "#facc15",
          lineWidth: 2,
          title: "EMA 9",
        });

        cockpitEma21Series = cockpitGexChart.addLineSeries({
          color: "#38bdf8",
          lineWidth: 2,
          title: "EMA 21",
        });

        cockpitDeltaSeries = cockpitGexChart.addHistogramSeries({
          priceFormat: { type: "volume" },
          priceScaleId: "cockpit_delta_scale",
          title: "Δ Vol",
        });
        cockpitGexChart.priceScale("cockpit_delta_scale").applyOptions({
          scaleMargins: { top: 0.82, bottom: 0 },
        });

        // Crosshair move HUD
        cockpitGexChart.subscribeCrosshairMove((param) => {
          if (!param || !param.time || !param.seriesData) return;
          const c = param.seriesData.get(cockpitCandleSeries);
          const v = param.seriesData.get(cockpitVwapSeries);
          const d = param.seriesData.get(cockpitDeltaSeries);
          const e9 = param.seriesData.get(cockpitEma9Series);
          const e21 = param.seriesData.get(cockpitEma21Series);

          const chlTime = document.getElementById("chl-time");
          const chlOhlc = document.getElementById("chl-ohlc");
          const chlVwap = document.getElementById("chl-vwap");
          const chlEma = document.getElementById("chl-ema");
          const chlDelta = document.getElementById("chl-delta");

          if (chlTime && param.time) {
            const ts = typeof param.time === "number" ? param.time : param.time.timestamp;
            if (ts) {
              chlTime.textContent = istChartStamp(ts);
            }
          }
          if (chlOhlc && c) {
            chlOhlc.textContent = `O: ₹${c.open.toFixed(1)} H: ₹${c.high.toFixed(1)} L: ₹${c.low.toFixed(1)} C: ₹${c.close.toFixed(1)}`;
          }
          if (chlVwap && v) {
            chlVwap.textContent = `VWAP: ₹${v.value.toFixed(1)}`;
          }
          if (chlEma && e9 && e21) {
            chlEma.textContent = `EMA 9/21: ₹${e9.value.toFixed(1)} / ₹${e21.value.toFixed(1)}`;
          }
          if (chlDelta && d) {
            chlDelta.textContent = `Δ: ${(d.value || 0).toLocaleString("en-IN")}`;
          }
        });

        window.addEventListener("resize", () => {
          if (cockpitGexChart && mount) {
            cockpitGexChart.applyOptions({ width: mount.clientWidth });
          }
        });
      }

    try {
      const res = await fetch(`/api/sentinel-tape?symbol=${encodeURIComponent(targetSym)}&timeframe=${encodeURIComponent(tf)}`);
      const data = await res.json();

      // Guard against race conditions: only update series if still matching active symbol
      const currentActive = (cockpitChartState.symbol || state.symbol || "").toUpperCase();
      if (targetSym !== currentActive || (data.symbol && data.symbol.toUpperCase() !== currentActive)) {
        return;
      }

      const candles = (data.bars || []).map((b) => ({
        time: b.time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }));

      cockpitCandleSeries.setData(candles);
      if (candles.length > 0) {
        cockpitChartState.currentBar = { ...candles[candles.length - 1] };
      }

      const vwapData = (data.bars || []).filter((b) => b.vwap > 0).map((b) => ({ time: b.time, value: b.vwap }));
      cockpitVwapSeries.setData(vwapData);

      const up1Data = (data.bars || []).filter((b) => b.up1 > 0).map((b) => ({ time: b.time, value: b.up1 }));
      cockpitUp1Series.setData(up1Data);

      const dn1Data = (data.bars || []).filter((b) => b.dn1 > 0).map((b) => ({ time: b.time, value: b.dn1 }));
      cockpitDn1Series.setData(dn1Data);

      function calcEMA(candlesArr, period) {
        if (!candlesArr || candlesArr.length === 0) return [];
        const k = 2.0 / (period + 1.0);
        let ema = candlesArr[0].close;
        const resArr = [];
        for (let i = 0; i < candlesArr.length; i++) {
          const c = candlesArr[i].close;
          ema = c * k + ema * (1.0 - k);
          resArr.push({ time: candlesArr[i].time, value: Math.round(ema * 100) / 100 });
        }
        return resArr;
      }

      cockpitEma9Series.setData(calcEMA(candles, 9));
      cockpitEma21Series.setData(calcEMA(candles, 21));

      const deltaData = (data.bars || []).map((b) => ({
        time: b.time,
        value: b.delta,
        color: b.delta >= 0 ? "rgba(16, 185, 129, 0.45)" : "rgba(239, 68, 68, 0.45)",
      }));
      cockpitDeltaSeries.setData(deltaData);

      // GEX Walls
      if (cockpitCwLine) cockpitCandleSeries.removePriceLine(cockpitCwLine);
      if (cockpitPwLine) cockpitCandleSeries.removePriceLine(cockpitPwLine);
      if (cockpitFlipLine) cockpitCandleSeries.removePriceLine(cockpitFlipLine);

      if (data.call_wall > 0) {
        cockpitChartState.cw = data.call_wall;
        cockpitCwLine = cockpitCandleSeries.createPriceLine({
          price: data.call_wall,
          color: "#ef4444",
          lineWidth: 2,
          lineStyle: 1,
          axisLabelVisible: true,
          title: "Call gamma",
        });
      }
      if (data.put_wall > 0) {
        cockpitChartState.pw = data.put_wall;
        cockpitPwLine = cockpitCandleSeries.createPriceLine({
          price: data.put_wall,
          color: "#22c55e",
          lineWidth: 2,
          lineStyle: 1,
          axisLabelVisible: true,
          title: "Put gamma",
        });
      }
      if (data.gamma_flip > 0) {
        cockpitFlipLine = cockpitCandleSeries.createPriceLine({
          price: data.gamma_flip,
          color: "#eab308",
          lineWidth: 1.5,
          lineStyle: 2,
          axisLabelVisible: true,
          title: "Gamma Flip",
        });
      }

      // Initial Render: Fit the most recent 65 candles at a bold, comfortable width instead of squishing all intraday bars
      const totalBars = candles.length;
      cockpitChartState.totalBars = totalBars;
      if (totalBars > 0) {
        const visibleBars = Math.min(totalBars, 65);
        cockpitGexChart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, totalBars - visibleBars),
          to: totalBars + 5,
        });
      } else {
        cockpitGexChart.timeScale().fitContent();
      }

      setTimeout(() => {
        if (cockpitGexChart && mount) {
          cockpitGexChart.applyOptions({ width: mount.clientWidth });
        }
      }, 50);
    } catch (err) {
      console.error("Error loading Cockpit GEX Canvas:", err);
    }
  }

  window.resetCockpitChartFit = function () {
    if (!cockpitGexChart) return;
    try {
      cockpitGexChart.priceScale("right").applyOptions({ autoScale: true });
      const totalBars = cockpitChartState.totalBars || 65;
      const visibleBars = Math.min(totalBars, 65);
      cockpitGexChart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, totalBars - visibleBars),
        to: totalBars + 5,
      });
    } catch (e) {
      if (cockpitGexChart) cockpitGexChart.timeScale().fitContent();
    }
  };

  window.switchCockpitTimeframe = function (tf) {
    cockpitChartState.timeframe = tf;
    renderCockpitChart(state.symbol, tf, cockpitChartState.engine);
  };

  window.switchCockpitChartEngine = function (engine) {
    cockpitChartState.engine = engine;
    renderCockpitChart(state.symbol, cockpitChartState.timeframe, engine);
  };

  window.setCockpitChartHeight = function (h) {
    cockpitChartState.height = h;
    const vp = document.getElementById("cockpit-chart-viewport");
    if (vp) vp.style.height = `${h}px`;
    if (cockpitGexChart && cockpitChartState.engine === "gex_canvas") {
      cockpitGexChart.applyOptions({ height: h });
    }
  };

  window.toggleCockpitChartFullscreen = function () {
    const card = document.getElementById("cockpit-tv-card");
    if (!card) return;
    if (!document.fullscreenElement) {
      card.requestFullscreen().catch(() => {});
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
    }
  };

  window.openCockpitPopoutChart = function () {
    const sym = state.symbol || "NIFTY";
    const tf = cockpitChartState.timeframe || "5m";
    const engine = "gex_canvas";
    const url = `/chart/tradingview?symbol=${encodeURIComponent(sym)}&timeframe=${encodeURIComponent(tf)}&engine=${encodeURIComponent(engine)}`;
    window.open(url, "_blank", "width=1280,height=820,menubar=no,toolbar=no,location=no,status=no,resizable=yes");
  };

  function initCockpitChartDragHandle() {
    if (cockpitChartState.dragInitialized) return;
    const dragHandle = document.getElementById("cockpit-chart-drag");
    const vp = document.getElementById("cockpit-chart-viewport");
    if (!dragHandle || !vp) return;

    cockpitChartState.dragInitialized = true;
    let isDragging = false;
    let startY = 0;
    let startH = 0;

    dragHandle.addEventListener("mousedown", (e) => {
      isDragging = true;
      startY = e.clientY;
      startH = vp.clientHeight;
      document.body.style.cursor = "ns-resize";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const delta = e.clientY - startY;
      const newH = Math.max(340, Math.min(1000, startH + delta));
      window.setCockpitChartHeight(newH);
    });

    window.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        document.body.style.cursor = "";
      }
    });
  }

  // -------------------------------------------------------------------------
  // Institutional Market Alert State Evaluator & Marquee Tape Renderer
  // -------------------------------------------------------------------------
  const TAPE_HOLD_MS = 75000;
  const TAPE_SIGNAL_MIN_SCORE = 70;
  const TAPE_SIGNAL_MAX_AGE_S = 300;

  function tapeInr(val) {
    return `₹${Math.round(Number(val) || 0).toLocaleString("en-IN")}`;
  }

  function classifyTapeZone(spot, cw, pw, step) {
    spot = Number(spot) || 0;
    cw = Number(cw) || 0;
    pw = Number(pw) || 0;
    step = Math.max(Number(step) || 50, 1);
    if (spot <= 0) return { zone: "UNKNOWN", label: "No spot", through_pts: 0, step };
    const sameWall = cw > 0 && pw > 0 && Math.abs(cw - pw) <= Math.max(0.51, step * 0.01);
    if (sameWall) {
      const mag = cw;
      const dist = spot - mag;
      if (dist < -step) return { zone: "BELOW_MAGNET", through_pts: mag - spot, step, magnet: mag };
      if (dist > step) return { zone: "ABOVE_MAGNET", through_pts: spot - mag, step, magnet: mag };
      return { zone: "TEST_MAGNET", through_pts: Math.abs(dist), step, magnet: mag };
    }
    if (pw > 0 && spot <= pw - step) return { zone: "BELOW_PUT_WALL", through_pts: pw - spot, step };
    if (cw > 0 && spot >= cw + step) return { zone: "ABOVE_CALL_WALL", through_pts: spot - cw, step };
    if (pw > 0 && Math.abs(spot - pw) <= step) return { zone: "TEST_PUT", through_pts: Math.abs(spot - pw), step };
    if (cw > 0 && Math.abs(spot - cw) <= step) return { zone: "TEST_CALL", through_pts: Math.abs(spot - cw), step };
    if (pw > 0 && cw > pw && pw < spot && spot < cw) return { zone: "PINNED", through_pts: 0, step };
    return { zone: "UNSITUATED", through_pts: 0, step };
  }

  function tapeAlertEpoch(alert, nowTs) {
    if (!alert) return null;
    if (alert.ts != null && Number.isFinite(Number(alert.ts))) {
      let val = Number(alert.ts);
      if (val > 1e12) val /= 1000;
      return val;
    }
    const clock = String(alert.time || "").trim().split(" ")[0];
    if (!clock || clock.split(":").length < 2) return null;
    const parts = clock.split(":");
    const now = new Date(nowTs * 1000);
    const ist = new Date(now.getTime() + (now.getTimezoneOffset() * 60000) + 19800000);
    ist.setHours(Number(parts[0]) || 0, Number(parts[1]) || 0, Number(parts[2]) || 0, 0);
    return ist.getTime() / 1000;
  }

  function pickFreshTapeSignal(alerts, nowTs) {
    let best = null;
    let bestTs = -1;
    (alerts || []).forEach((alert) => {
      const status = String(alert.status || "NEW").toUpperCase();
      if (status === "FAILED" || status === "REPLACED") return;
      if (Number(alert.score || 0) < TAPE_SIGNAL_MIN_SCORE) return;
      const ts = tapeAlertEpoch(alert, nowTs);
      if (ts == null) return;
      const age = nowTs - ts;
      if (age < 0 || age > TAPE_SIGNAL_MAX_AGE_S) return;
      if (ts >= bestTs) {
        bestTs = ts;
        best = Object.assign({}, alert, { _ts: ts, _age_s: age });
      }
    });
    return best;
  }

  function patchCockpitDashFromTick(tick) {
    if (!state.lastCockpitDash || !tick) return;
    const dash = state.lastCockpitDash;
    if (tick.spot) dash.spot = tick.spot;
    if (tick.ist_time) dash.as_of_time = tick.ist_time;
    dash.kpis = dash.kpis || {};
    if (Number(tick.call_wall) > 0) {
      dash.kpis.call_wall = Object.assign({}, dash.kpis.call_wall || {}, { val: Number(tick.call_wall) });
    }
    if (Number(tick.put_wall) > 0) {
      dash.kpis.put_wall = Object.assign({}, dash.kpis.put_wall || {}, { val: Number(tick.put_wall) });
    }
    if (Number(tick.gamma_flip) > 0) {
      dash.kpis.gamma_flip = Object.assign({}, dash.kpis.gamma_flip || {}, {
        val: Number(tick.gamma_flip),
        raw: Number(tick.gamma_flip),
      });
    }
    if (tick.is_market_open === false) dash.is_market_open = false;
    if (tick.is_market_open === true) dash.is_market_open = true;
  }

  function evaluateMarketAlertState(dash) {
    if (!dash || !dash.kpis) return null;
    const spot = Number(dash.spot || 0);
    if (spot <= 0) return null;

    const kpis = dash.kpis || {};
    const cw = Number(kpis.call_wall ? kpis.call_wall.val : 0);
    const pw = Number(kpis.put_wall ? kpis.put_wall.val : 0);
    const flip = Number((kpis.gamma_flip && (kpis.gamma_flip.raw !== undefined ? kpis.gamma_flip.raw : kpis.gamma_flip.val)) || dash.gamma_flip || 0);
    const step = Number(dash.strike_step || 50) || 50;
    const regime = String(kpis.regime ? kpis.regime.val : "NEUTRAL").toUpperCase();
    const blast = dash.gamma_blast || {};
    const blastScore = Number(blast.score || 0);
    const isExpiryBlast = Boolean(blast.is_expiry) && blastScore >= 60;
    const breadth = dash.market_breadth || {};
    const adv = Number(breadth.advances || 0);
    const dec = Number(breadth.declines || 0);
    const ratio = Number(breadth.ratio || 1.0);
    const nowTs = state.replayMode && Number.isFinite(state.replayAsOf) ? state.replayAsOf : Date.now() / 1000;

    const isClosed = dash.is_market_open === false || dash.market_state === "CLOSED";
    const isPreBell = Boolean(dash.is_pre_bell_ephemeral);
    const isMorning = dash.session_phase === "PRE_MARKET_SETUP" || Boolean(dash.is_morning_briefing);
    const rawClock = String(dash.as_of_time || "").trim();
    const todayIso = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    const timeStr = /^\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}/.test(rawClock)
      ? (/\bIST\b/i.test(rawClock) ? rawClock : `${rawClock} IST`)
      : datedClock(
          rawClock || new Date().toLocaleTimeString("en-GB", { hour12: false, timeZone: "Asia/Kolkata" }),
          todayIso,
        );
    const hmsOf = (text) => {
      const match = String(text || "").match(/(\d{2}:\d{2}:\d{2})/);
      return match ? match[1] : "";
    };
    const snapAsOf = String(dash.snapshot_as_of || "").trim();
    const snapNote = snapAsOf && hmsOf(snapAsOf) !== hmsOf(timeStr)
      ? ` · GEX snapshot ${snapAsOf}`
      : "";
    const geo = classifyTapeZone(spot, cw, pw, step);
    const zone = geo.zone;
    const nearFlip = flip > 0 && Math.abs(spot - flip) <= step;
    const sig = (isPreBell || isClosed) ? null : pickFreshTapeSignal(dash.orderflow_alerts, nowTs);
    const bdiag = `sectors ${adv}/${dec} vs prior close (context, not flow) · ratio ${ratio.toFixed(2)}x`;

    const pack = (fields) => Object.assign({
      timestamp: timeStr,
      zone,
      through_pts: Math.round((geo.through_pts || 0) * 10) / 10,
      signal_type: (sig && sig.type) || "",
      signal_score: sig ? Number(sig.score || 0) : 0,
    }, fields);

    if (isMorning) {
      const pm = dash.pre_market || {};
      const oi = String(pm.oi_as_of_display || formatValidStamp(pm.oi_as_of) || snapAsOf || "unavailable");
      const staleBook = Boolean(pm.snapshot_stale);
      const fii = pm.fii_bias || "—";
      const cvdNum = Number(pm.prior_session_cvd);
      const cvdTxt = Number.isFinite(cvdNum) ? (cvdNum >= 0 ? "+" : "") + cvdNum.toFixed(0) : "—";
      const brief = String(pm.status_text || "").trim();
      return pack({
        scenario: "MORNING_BRIEFING",
        badge: "🌅 PRIOR-SESSION BOOK",
        theme: "alert-tape-spark",
        headline: "MORNING BRIEFING — PRIOR-SESSION BOOK",
        meaning: staleBook
          ? "The saved book is not the last session. The close is withheld."
          : "Yesterday's walls and flip, before the cash open.",
        body: brief || `Previous NSE close versus call wall ${tapeInr(cw)}, put wall ${tapeInr(pw)}, flip ${tapeInr(flip)}. This is the last published book.`,
        action: "Cash pre-open is 09:00. Equilibrium prints ~09:08. F&O opens 09:15.",
        diagnostics: `OI as of ${oi} · FII ${fii} · prior CVD ${cvdTxt} · CW ${tapeInr(cw)} · PW ${tapeInr(pw)} · flip ${tapeInr(flip)}`,
      });
    }

    if (isPreBell) {
      const discovered = Boolean(dash.pre_market && dash.pre_market.is_discovered);
      return pack({
        scenario: "PRE_OPEN_SETUP",
        badge: discovered ? "🔔 PRE-OPEN DISCOVERED" : "🔔 PRE-OPEN SETUP",
        theme: "alert-tape-warn",
        headline: "PRE-OPEN — PRIOR-SESSION STRUCTURE",
        meaning: "Auction is still on yesterday's walls until 09:15.",
        body: `Cash auction is running (09:00–09:15). Call wall ${tapeInr(cw)} / put wall ${tapeInr(pw)} / flip ${tapeInr(flip)} are last published gamma concentration. Equilibrium prints ~09:08.`,
        action: discovered
          ? "Map the 09:08 print vs walls. Do not chase until regular session 09:15."
          : "Hold the prior-session map. Wait for 09:08 equilibrium before treating any gap as real.",
        diagnostics: `Spot: ${tapeInr(spot)} · CW: ${tapeInr(cw)} · PW: ${tapeInr(pw)}${snapNote}`,
      });
    }

    if (isClosed) {
      return pack({
        scenario: "MARKET_CLOSED",
        badge: "🌙 SESSION CLOSED",
        theme: "alert-tape-spark",
        headline: "SESSION CLOSED — EOD SETTLEMENT FROZEN",
        meaning: "Last published book. Nothing here is a live hedge.",
        body: "Market session concluded at 15:30 IST. Walls and gamma profiles are frozen at the last published snapshot. The book is frozen. Nothing is being hedged live.",
        action: `Review EOD buildup and walls (CW: ${tapeInr(cw)}, PW: ${tapeInr(pw)}). Prepare the next-session plan.`,
        diagnostics: `Spot: ${tapeInr(spot)} · CW: ${tapeInr(cw)} · PW: ${tapeInr(pw)}${snapNote}`,
      });
    }

    const zoneCopy = {
      BELOW_MAGNET: {
        scenario: "BELOW_SUPPORT",
        badge: "📉 BELOW GEX MAGNET",
        theme: "alert-tape-stop",
        headline: "BELOW GEX MAGNET — CONCENTRATION BEHIND PRICE",
        meaning: "Spot is through the only gamma cluster. It is not a pin.",
        body: `Spot is ${Math.round(geo.through_pts || 0)} pts through the single gamma cluster at ${tapeInr(geo.magnet || pw)}. This is concentration, not a floor.`,
        action: "Do not treat the cluster as a pin. Wait for a reclaim or a fresh Sentinel absorption print.",
      },
      ABOVE_MAGNET: {
        scenario: "ABOVE_RESISTANCE",
        badge: "🔥 ABOVE GEX MAGNET",
        theme: "alert-tape-spark",
        headline: "ABOVE GEX MAGNET — CONCENTRATION BEHIND PRICE",
        meaning: "Spot is through the only gamma cluster. It is not a squeeze.",
        body: `Spot is ${Math.round(geo.through_pts || 0)} pts through the single gamma cluster at ${tapeInr(geo.magnet || cw)}. A break of the cluster is a location, not a squeeze.`,
        action: "Do not treat the cluster as intact. Wait until spot is back inside one strike step.",
      },
      TEST_MAGNET: {
        scenario: "TEST_MAGNET",
        badge: "⚖️ AT GEX MAGNET",
        theme: "alert-tape-warn",
        headline: "AT GEX MAGNET — NO WALL CORRIDOR",
        meaning: "Call and put concentration share one strike. There is no corridor.",
        body: `Call and put concentration sit on the same strike ${tapeInr(geo.magnet || cw)}. There is no corridor between two strikes.`,
        action: "Stand aside on fade templates until the call and put strikes split. Use Sentinel for tape.",
      },
      BELOW_PUT_WALL: {
        scenario: "BELOW_SUPPORT",
        badge: "📉 PUT WALL BREACHED",
        theme: "alert-tape-stop",
        headline: "BELOW PUT-WALL CONCENTRATION",
        meaning: "The put-gamma strike is already behind price.",
        body: `Spot is ${Math.round(geo.through_pts || 0)} pts through the put-gamma strike ${tapeInr(pw)}. The strike is already behind price. It is not a floor.`,
        action: "Do not buy dips as if the strike is holding. Look for a reclaim or a confirmed absorption print.",
      },
      ABOVE_CALL_WALL: {
        scenario: "ABOVE_RESISTANCE",
        badge: "🔥 CALL WALL BREACHED",
        theme: "alert-tape-spark",
        headline: "ABOVE CALL-WALL CONCENTRATION",
        meaning: "The call-gamma strike is already behind price.",
        body: `Spot is ${Math.round(geo.through_pts || 0)} pts through the call-gamma strike ${tapeInr(cw)}. The strike is already behind price. It is not trapped-seller proof.`,
        action: "Do not treat the strike as intact. Wait for a close back inside the cluster or Sentinel exhaustion.",
      },
      TEST_PUT: {
        scenario: "TEST_PUT",
        badge: "🚨 PUT WALL TEST",
        theme: "alert-tape-stop",
        headline: "TESTING PUT-WALL CONCENTRATION",
        meaning: "Within one strike step of the put wall. A touch is not a floor.",
        body: `Spot is within one strike step of the put-gamma strike ${tapeInr(pw)}. Watch absorption versus a sweep. A touch is not a floor.`,
        action: "Do not fade the strike. Cut the idea if price prints through by more than one step.",
      },
      TEST_CALL: {
        scenario: "TEST_CALL",
        badge: "🔥 CALL WALL TEST",
        theme: "alert-tape-spark",
        headline: "TESTING CALL-WALL CONCENTRATION",
        meaning: "Within one strike step of the call wall. A touch is not a squeeze.",
        body: `Spot is within one strike step of the call-gamma strike ${tapeInr(cw)}. Watch absorption versus continuation. A touch is not a squeeze.`,
        action: "Do not assume a squeeze on a touch. Need a 5-min hold through the strike plus tape confirmation.",
      },
      PINNED: {
        scenario: "SIDEWAYS_CHOP",
        badge: "⚖️ RANGE BETWEEN WALLS",
        theme: "alert-tape-warn",
        headline: "BETWEEN WALL CONCENTRATIONS",
        meaning: "Spot is inside the corridor. The corridor is a map, not a fade.",
        body: `Spot is inside the corridor ${tapeInr(pw)}–${tapeInr(cw)}. The corridor is a map. It is not a reason to fade either strike.`,
        action: "Do not fade the edges because price is between the strikes. Use Sentinel tape for direction.",
      },
      UNSITUATED: {
        scenario: "UNSITUATED",
        badge: "⚖️ STRUCTURE UNCLEAR",
        theme: "alert-tape-warn",
        headline: "WALL GEOMETRY UNCLEAR",
        meaning: "The strikes do not form a clean corridor around spot.",
        body: "Call/put concentrations do not form a clean corridor around spot. No pin or breach claim.",
        action: "Stand aside on wall templates. Use Sentinel tape and wait for structure.",
      },
    };
    const base = Object.assign({}, zoneCopy[zone] || zoneCopy.UNSITUATED);

    if (sig) {
      const insight = String(sig.insight_text || String(sig.type || "SIGNAL").replace(/_/g, " "));
      let action = String(sig.actionable_play || "").trim() || base.action;
      const inval = Number(sig.invalidation_level || 0);
      if (inval > 0 && action.toLowerCase().indexOf("invalidat") < 0) {
        action = `${action} Invalidation ${tapeInr(inval)}.`;
      }
      const cat = String(sig.category || "").toUpperCase();
      let theme = base.theme;
      if (cat === "BULLISH") theme = "alert-tape-go";
      else if (cat === "BEARISH") theme = "alert-tape-stop";
      else if (cat === "CAUTION") theme = "alert-tape-warn";
      const sigName = String(sig.type || "SIGNAL").replace(/_/g, " ");
      base.scenario = "TAPE_" + String(sig.type || "SIGNAL");
      base.badge = `📡 ${sigName} · ${base.badge.split(" ").slice(1).join(" ")}`;
      base.theme = theme;
      base.headline = `${sigName} · ${base.headline}`;
      base.body = `${insight} Geometry: ${base.body}`;
      base.action = action;
    }

    if (isExpiryBlast) {
      base.badge = `⚡ 0DTE BLAST ${blastScore} · ${base.badge}`;
      base.body = `Expiry gamma-blast score ${blastScore}/100 (parity crossover watch, not dealer sign). ${base.body}`;
    }

    let flipBit = flip > 0 ? ` · Flip ${tapeInr(flip)}` : "";
    if (nearFlip) flipBit += " · within 1 step of flip (writer-imbalance map, unvalidated)";
    let blastBit = ` · Blast ${blastScore}/100`;
    if (blast.is_expiry) blastBit += " 0DTE";
    let sigBit = "";
    if (sig) sigBit = ` · ${String(sig.type).replace(/_/g, " ")} ${Number(sig.score || 0)}/${Math.max(1, Math.round(sig._age_s || 0))}s`;

    return pack({
      scenario: base.scenario,
      badge: base.badge,
      theme: base.theme,
      headline: base.headline,
      meaning: base.meaning || "",
      body: base.body,
      action: base.action,
      diagnostics: `Spot ${tapeInr(spot)} · CW ${tapeInr(cw)} · PW ${tapeInr(pw)}${flipBit}${blastBit}${sigBit} · ${bdiag} · ${regime}`,
    });
  }

  function updateMarketAlertTape(dash) {
    const candidate = evaluateMarketAlertState(dash);
    const container = document.getElementById("cockpit-alert-tape");
    if (!container || !candidate) return;

    const now = Date.now();
    const latch = state.tapeLatch;
    let alert = candidate;
    if (latch && latch.scenario && latch.zone) {
      const zoneChanged = candidate.zone !== latch.zone;
      const held = (now - latch.at) < TAPE_HOLD_MS;
      if (!zoneChanged && held && candidate.scenario !== latch.scenario) {
        alert = Object.assign({}, latch.alert, {
          timestamp: candidate.timestamp,
          diagnostics: candidate.diagnostics,
          through_pts: candidate.through_pts,
        });
      }
    }
    state.tapeLatch = {
      scenario: alert.scenario,
      zone: alert.zone,
      at: (latch && latch.scenario === alert.scenario && latch.zone === alert.zone) ? latch.at : now,
      alert,
    };

    container.className = `alert-tape-marquee-container ${alert.theme}`;
    const tsEl = document.getElementById("alert-tape-timestamp");
    if (tsEl) tsEl.textContent = alert.timestamp;
    const badgeEl = document.getElementById("alert-tape-badge");
    if (badgeEl) badgeEl.textContent = alert.badge;

    const track = document.getElementById("alert-marquee-track");
    const paintSig = `${alert.scenario}|${alert.theme}|${alert.headline}|${alert.meaning}|${alert.body}|${alert.action}`;
    if (track && state.lastTapePaintSig !== paintSig) {
      state.lastTapePaintSig = paintSig;
      const meaningHtml = alert.meaning ? `<span class="marquee-meaning">${alert.meaning}</span>` : "";
      const itemHtml = `
        <span class="alert-marquee-item">
          <span class="marquee-headline">${alert.headline}</span>${meaningHtml} ${alert.body}
          <span class="marquee-action-tag">👉 ACTION: ${alert.action}</span>
          <span class="marquee-telemetry">[${alert.diagnostics}]</span>
        </span>
      `;
      track.innerHTML = itemHtml;
    } else if (track) {
      track.querySelectorAll(".marquee-telemetry").forEach((el) => {
        el.textContent = `[${alert.diagnostics}]`;
      });
    }

    // Symbol-isolated state transition detection & 3.0s Louder Brass Bell Alert
    const sym = (state.symbol || "NIFTY").toUpperCase();
    const lastScenario = state.lastAlertScenarios[sym] || null;

    if (alert.scenario !== lastScenario) {
      const now = Date.now();
      const isInitial = lastScenario === null;
      state.lastAlertScenarios[sym] = alert.scenario;
      state.lastAlertScenario = alert.scenario;

      if (!isInitial) {
        // Play 3.0-second louder brass bell
        if (now - state.lastAlertSoundTime > 45000) {
          if (!state.audioCtx || state.audioCtx.state === "suspended") {
            state.pendingAlertBell = true;
            state.pendingAlertBellTime = now;
          }
          playTraderBellRing();
          state.lastAlertSoundTime = now;
        }

        // Post alert message with timestamp to Session Commentary timeline
        fetch("/api/commentary/log-market-alert", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol: sym,
            scenario: alert.scenario,
            badge: alert.badge,
            headline: alert.headline,
            body: alert.body,
            action_advice: alert.action,
            diagnostics: alert.diagnostics,
          }),
        }).catch((err) => console.warn("Failed to log alert to commentary:", err));
      }
    }
  }

  window.onTargetEngineParamsChange = function () {
    const hSelect = document.getElementById("te-horizon-select");
    const mSelect = document.getElementById("te-mode-select");
    if (hSelect) state.teHorizon = parseInt(hSelect.value) || 120;
    if (mSelect) state.teMode = mSelect.value || "heuristic";
    loadTargetEngine();
  };

  function renderRankedCandidates(candidates, features) {
    const body = document.getElementById("te-candidates-body");
    const feat = document.getElementById("te-features-body");
    if (body) {
      if (!candidates.length) {
        body.innerHTML = '<div style="color:var(--ink-mute);font-size:0.8rem">No ranked structure on this read.</div>';
      } else {
        body.innerHTML = candidates.slice(0, 4).map((c) => `
          <div style="display:grid;grid-template-columns:1.4fr repeat(5, 1fr);gap:8px;font-size:0.78rem;padding:6px 0;border-top:1px solid var(--line)">
            <div><b>${c.structure_type || "Structure"}</b><div style="color:var(--ink-mute)">${c.option_type} ${Number(c.strike).toLocaleString("en-IN")} · ${c.scenario}</div></div>
            <div>Score<br><b>${c.score}</b></div>
            <div>Entry<br><b>₹${c.entry_ltp}</b></div>
            <div>T1<br><b>₹${c.target_1}</b></div>
            <div>Stop<br><b>₹${c.stop_loss}</b></div>
            <div>R:R<br><b>${c.risk_reward}</b></div>
          </div>`).join("");
      }
    }
    if (feat) {
      if (!features.length) {
        feat.innerHTML = "";
      } else {
        feat.innerHTML = `<div style="font-size:0.72rem;color:var(--ink-mute);margin-bottom:4px">Feature values used by the dial</div>` +
          features.slice(0, 12).map((f) => `<span style="display:inline-block;margin:0 10px 4px 0;font-size:0.72rem;font-family:var(--mono)">${f.name} ${f.value}</span>`).join("");
      }
    }
  }

  async function loadTargetEngine() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const horizon = state.teHorizon || 120;
      const mode = state.teMode || "heuristic";
      const res = await fetch(`/api/target-engine?symbol=${requestedSym}&window=${state.window}&horizon=${horizon}&mode=${mode}`);
      const data = await res.json();
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) return;

      const dialVerdict = document.getElementById("dial-verdict");
      if (dialVerdict) {
        dialVerdict.textContent = data.dial.verdict;
        if (data.dial.color) dialVerdict.style.color = data.dial.color;
      }
      document.getElementById("dial-score-num").textContent = `${data.dial.score} / 100`;
      const convBadge = document.getElementById("dial-conviction-badge");
      if (convBadge) {
        convBadge.textContent = data.dial.conviction_label || "CONVICTION";
        const vc = (data.dial.verdict || "").toUpperCase();
        convBadge.style.background = vc.includes("BEAR") ? "var(--stop-bg)" : (vc.includes("BULL") ? "var(--go-bg)" : "var(--spark-bg, rgba(250,204,21,.12))");
        convBadge.style.color = data.dial.color || "var(--ink)";
      }

      // 1. Dominant Scenario & Multi-Color Segmented Probability Bar
      if (data.scenario_set) {
        const sc = data.scenario_set;
        const domScen = sc.dominant_scenario ? sc.dominant_scenario.toUpperCase() : "—";
        const probs = sc.probabilities || {};
        const pBull = probs.bull || 0;
        const pBase = probs.base || 0;
        const pBear = probs.bear || 0;
        const maxProb = Math.max(pBull, pBase, pBear);

        const domEl = document.getElementById("te-dominant-scenario");
        if (domEl) {
          domEl.textContent = `${domScen} (${(maxProb * 100).toFixed(1)}%)`;
          domEl.style.color = domScen.includes("BULL") ? "var(--go)" : (domScen.includes("BEAR") ? "var(--stop)" : "var(--spark)");
        }

        const edgeBadge = document.getElementById("te-edge-badge");
        if (edgeBadge) {
          const noEdge = !!(sc.no_edge || (sc.edge_label || "").includes("NO") || (sc.edge_label || "").includes("RANGE LED"));
          edgeBadge.textContent = sc.edge_label || (noEdge ? "NO EDGE — RANGE DOMINANT" : "CONFIRMED EDGE");
          edgeBadge.style.background = noEdge ? "#78350f" : "#064e3b";
          edgeBadge.style.borderColor = noEdge ? "rgba(245,158,11,0.4)" : "rgba(16,185,129,0.3)";
        }

        const convEl = document.getElementById("te-conviction-val");
        if (convEl) convEl.textContent = `${((sc.conviction || 0) * 100).toFixed(0)}%`;

        const spotEl = document.getElementById("te-spot-val");
        if (spotEl) spotEl.textContent = `₹${data.spot.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;

        // Segmented Progress Bar
        const barBull = document.getElementById("te-bar-bull");
        if (barBull) {
          barBull.style.width = `${(pBull * 100).toFixed(1)}%`;
          barBull.title = `Bull: ${(pBull * 100).toFixed(1)}%`;
        }
        const barBase = document.getElementById("te-bar-base");
        if (barBase) {
          barBase.style.width = `${(pBase * 100).toFixed(1)}%`;
          barBase.title = `Base: ${(pBase * 100).toFixed(1)}%`;
        }
        const barBear = document.getElementById("te-bar-bear");
        if (barBear) {
          barBear.style.width = `${(pBear * 100).toFixed(1)}%`;
          barBear.title = `Bear: ${(pBear * 100).toFixed(1)}%`;
        }

        // Probability Labels
        const lblBull = document.getElementById("te-prob-bull");
        if (lblBull) lblBull.textContent = `${(pBull * 100).toFixed(1)}%`;
        const lblBase = document.getElementById("te-prob-base");
        if (lblBase) lblBase.textContent = `${(pBase * 100).toFixed(1)}%`;
        const lblBear = document.getElementById("te-prob-bear");
        if (lblBear) lblBear.textContent = `${(pBear * 100).toFixed(1)}%`;
      }

      // 2. Tri-Scenario Target Cards with P(Touch), P(Close Beyond), and Forecast IV
      if (data.targets) {
        const horizonTxt = `${data.horizon || 120}m`;
        ["te-bull-horizon-badge", "te-base-horizon-badge", "te-bear-horizon-badge"].forEach((id) => {
          const el = document.getElementById(id);
          if (el) el.textContent = horizonTxt;
        });

        if (data.targets.bull) {
          const b = data.targets.bull;
          const tgtEl = document.getElementById("te-bull-target-val") || document.getElementById("target-bull-val");
          if (tgtEl) tgtEl.textContent = `₹${b.target.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
          const stopEl = document.getElementById("te-bull-stop-val");
          if (stopEl) stopEl.textContent = `₹${b.stop.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
          const pTouchEl = document.getElementById("te-bull-ptouch");
          if (pTouchEl) pTouchEl.textContent = `${((b.p_touch_target || b.p_touch || 0) * 100).toFixed(1)}%`;
          const pCloseEl = document.getElementById("te-bull-pclose");
          if (pCloseEl) pCloseEl.textContent = `${((b.p_close_beyond || 0) * 100).toFixed(1)}%`;
          const ivEl = document.getElementById("te-bull-iv");
          if (ivEl) ivEl.textContent = b.forecast_iv == null ? "—" : `${(b.forecast_iv * 100).toFixed(2)}%`;
          const ancEl = document.getElementById("te-bull-anchor");
          if (ancEl) ancEl.textContent = b.reason || "—";
        }
        if (data.targets.base) {
          const b = data.targets.base;
          const tgtEl = document.getElementById("te-base-target-val") || document.getElementById("target-base-val");
          if (tgtEl) tgtEl.textContent = `₹${b.target.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
          const stopEl = document.getElementById("te-base-stop-val");
          if (stopEl) stopEl.textContent = `₹${b.stop.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
          const pTouchEl = document.getElementById("te-base-ptouch");
          if (pTouchEl) pTouchEl.textContent = `${((b.p_touch_target || b.p_touch || 0) * 100).toFixed(1)}%`;
          const pCloseEl = document.getElementById("te-base-pclose");
          if (pCloseEl) pCloseEl.textContent = `${((b.p_close_beyond || 0) * 100).toFixed(1)}%`;
          const ivEl = document.getElementById("te-base-iv");
          if (ivEl) ivEl.textContent = b.forecast_iv == null ? "—" : `${(b.forecast_iv * 100).toFixed(2)}%`;
          const ancEl = document.getElementById("te-base-anchor");
          if (ancEl) ancEl.textContent = b.reason || "Range centre";
        }
        if (data.targets.bear) {
          const b = data.targets.bear;
          const tgtEl = document.getElementById("te-bear-target-val") || document.getElementById("target-bear-val");
          if (tgtEl) tgtEl.textContent = `₹${b.target.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
          const stopEl = document.getElementById("te-bear-stop-val");
          if (stopEl) stopEl.textContent = `₹${b.stop.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
          const pTouchEl = document.getElementById("te-bear-ptouch");
          if (pTouchEl) pTouchEl.textContent = `${((b.p_touch_target || b.p_touch || 0) * 100).toFixed(1)}%`;
          const pCloseEl = document.getElementById("te-bear-pclose");
          if (pCloseEl) pCloseEl.textContent = `${((b.p_close_beyond || 0) * 100).toFixed(1)}%`;
          const ivEl = document.getElementById("te-bear-iv");
          if (ivEl) ivEl.textContent = b.forecast_iv == null ? "—" : `${(b.forecast_iv * 100).toFixed(2)}%`;
          const ancEl = document.getElementById("te-bear-anchor");
          if (ancEl) ancEl.textContent = b.reason || "Put-gamma strike";
        }
      }

      // Update Reference Anchors Status Strip
      const fmtLvl = (v) => `₹${Number(v).toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
      const ceInvalEl = document.getElementById("ladder-ce-inval");
      if (ceInvalEl && data.targets && data.targets.bull) ceInvalEl.textContent = fmtLvl(data.targets.bull.stop);
      const ceTgt1El = document.getElementById("ladder-ce-tgt1");
      if (ceTgt1El && data.targets && data.targets.base) ceTgt1El.textContent = fmtLvl(data.targets.base.target);
      const ceTgt2El = document.getElementById("ladder-ce-tgt2");
      if (ceTgt2El && data.targets && data.targets.bull) ceTgt2El.textContent = fmtLvl(data.targets.bull.target);

      const peInvalEl = document.getElementById("ladder-pe-inval");
      if (peInvalEl && data.targets && data.targets.bear) peInvalEl.textContent = fmtLvl(data.targets.bear.stop);
      const peTgt1El = document.getElementById("ladder-pe-tgt1");
      if (peTgt1El && data.targets && data.targets.base) peTgt1El.textContent = fmtLvl(data.targets.base.target);
      const peTgt2El = document.getElementById("ladder-pe-tgt2");
      if (peTgt2El && data.targets && data.targets.bear) peTgt2El.textContent = fmtLvl(data.targets.bear.target);

      const spotDisp = document.getElementById("ladder-spot-display");
      if (spotDisp) spotDisp.textContent = `₹${data.spot.toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;
      const atmDisp = document.getElementById("ladder-atm-display");
      if (atmDisp) atmDisp.textContent = `${data.atm_strike ? Math.round(data.atm_strike) : Math.round(data.spot)}`;

      // Populate 11-Strike Dual-Wing Target / SL Matrix
      const ladderBody = document.getElementById("strike-ladder-body");
      if (ladderBody && data.ladder) {
        ladderBody.innerHTML = "";
        data.ladder.forEach((row) => {
          const tr = document.createElement("tr");
          if (row.is_atm) tr.className = "atm-row";

          const ce = row.ce || {};
          const pe = row.pe || {};

          const cePnlClr = (ce.tgt1_inr || 0) >= 0 ? "var(--go)" : "var(--stop)";
          const pePnlClr = (pe.tgt1_inr || 0) >= 0 ? "var(--go)" : "var(--stop)";
          const ceRrCls = "rr-med";
          const peRrCls = "rr-med";
          const greekLine = (wing) => {
            if (wing.delta == null && wing.iv == null) return "";
            const d = wing.delta == null ? "" : `Δ ${Number(wing.delta).toFixed(2)}`;
            const iv = wing.iv == null ? "" : `IV ${Number(wing.iv).toFixed(1)}%`;
            return [d, iv].filter(Boolean).join(" · ");
          };

          const strikeTag = row.is_atm
            ? `₹${row.strike.toLocaleString("en-IN")} <span class="atm-star">ATM</span>`
            : `₹${row.strike.toLocaleString("en-IN")}`;

          tr.innerHTML = `
            <td style="color:${cePnlClr};font-weight:700">₹${(ce.tgt1_inr || 0).toLocaleString("en-IN")}<div class="sub-pts" style="color:var(--ink-faint)">T2: ₹${(ce.tgt2_inr || 0).toLocaleString("en-IN")}</div></td>
            <td><span class="rr-badge-ladder ${ceRrCls}">${ce.rr_str || "—"}</span><div class="sub-pts" style="color:var(--ink-mute)">P: ${ce.p_touch_tgt1 == null ? "—" : ce.p_touch_tgt1 + "%"}</div></td>
            <td style="color:var(--go);font-weight:700">₹${(ce.opt_tgt2 || 0).toFixed(1)}<div class="sub-pts" style="color:var(--go)">+${(ce.tgt2_pts || 0).toFixed(1)}</div></td>
            <td style="color:#4ade80;font-weight:700">₹${(ce.opt_tgt1 || 0).toFixed(1)}<div class="sub-pts" style="color:#4ade80">+${(ce.tgt1_pts || 0).toFixed(1)}</div></td>
            <td style="color:var(--stop);font-weight:700">₹${(ce.opt_sl || 0).toFixed(1)}<div class="sub-pts" style="color:var(--stop)">-${(ce.sl_pts || 0).toFixed(1)}</div></td>
            <td style="color:var(--spark);font-weight:700">₹${(ce.ltp || 0).toFixed(1)}<div class="sub-pts" style="color:var(--ink-faint)">${greekLine(ce)}</div></td>
            <td class="col-strike">${strikeTag}</td>
            <td style="color:var(--spark);font-weight:700">₹${(pe.ltp || 0).toFixed(1)}<div class="sub-pts" style="color:var(--ink-faint)">${greekLine(pe)}</div></td>
            <td style="color:var(--stop);font-weight:700">₹${(pe.opt_sl || 0).toFixed(1)}<div class="sub-pts" style="color:var(--stop)">-${(pe.sl_pts || 0).toFixed(1)}</div></td>
            <td style="color:#4ade80;font-weight:700">₹${(pe.opt_tgt1 || 0).toFixed(1)}<div class="sub-pts" style="color:#4ade80">+${(pe.tgt1_pts || 0).toFixed(1)}</div></td>
            <td style="color:var(--go);font-weight:700">₹${(pe.opt_tgt2 || 0).toFixed(1)}<div class="sub-pts" style="color:var(--go)">+${(pe.tgt2_pts || 0).toFixed(1)}</div></td>
            <td><span class="rr-badge-ladder ${peRrCls}">${pe.rr_str || "—"}</span><div class="sub-pts" style="color:var(--ink-mute)">P: ${pe.p_touch_tgt1 == null ? "—" : pe.p_touch_tgt1 + "%"}</div></td>
            <td style="color:${pePnlClr};font-weight:700">₹${(pe.tgt1_inr || 0).toLocaleString("en-IN")}<div class="sub-pts" style="color:var(--ink-faint)">T2: ₹${(pe.tgt2_inr || 0).toLocaleString("en-IN")}</div></td>
          `;
          ladderBody.appendChild(tr);
        });
        const lotChip = document.getElementById("ladder-lot-size");
        if (lotChip) lotChip.textContent = data.lot_size ? String(data.lot_size) : "—";
        const wsChip = document.getElementById("ladder-ws-status");
        if (wsChip) {
          const live = (data.ladder_meta && data.ladder_meta.live_ticks) || 0;
          wsChip.textContent = live > 0 ? `${live} live ticks` : "Chain snapshot";
        }
        renderRankedCandidates(data.candidates || [], data.features || []);
        state.teData = data;

        // Update Preset Buttons with active target levels
        const btnBull = document.getElementById("te-btn-preset-bull");
        if (btnBull && data.targets && data.targets.bull) {
          btnBull.textContent = `🚀 Bull Preset (+1σ: ₹${Math.round(data.targets.bull.target)})`;
        }
        const btnBear = document.getElementById("te-btn-preset-bear");
        if (btnBear && data.targets && data.targets.bear) {
          btnBear.textContent = `🔻 Bear Preset (-1σ: ₹${Math.round(data.targets.bear.target)})`;
        }
        const btnBase = document.getElementById("te-btn-preset-base");
        if (btnBase && data.targets && data.targets.base) {
          btnBase.textContent = `⚖️ Base (Range: ₹${Math.round(data.targets.base.target)})`;
        }

        // Populate Expiry and 21-Strike Band in Target / SL Calculator
        const expSelect = document.getElementById("te-calc-expiry");
        if (expSelect && data.expiries) {
          const currentExp = expSelect.value;
          expSelect.innerHTML = "";
          data.expiries.forEach((exp) => {
            const opt = document.createElement("option");
            const expId = exp.id || exp;
            opt.value = expId;
            opt.textContent = exp.label || exp;
            if (currentExp ? expId === currentExp : (expId === data.active_expiry)) {
              opt.selected = true;
            }
            expSelect.appendChild(opt);
          });
          expSelect.onchange = () => window.recalculateTargetSL();
        }

        const strikeSelect = document.getElementById("te-calc-strike");
        if (strikeSelect && data.nearby_strikes) {
          const currentStrike = parseFloat(strikeSelect.value);
          const atmStrike = data.atm_strike || Math.round(data.spot / 50) * 50;
          strikeSelect.innerHTML = "";
          data.nearby_strikes.forEach((sk) => {
            const opt = document.createElement("option");
            opt.value = sk;
            opt.textContent = sk === atmStrike ? `₹${sk.toLocaleString("en-IN")} (ATM)` : `₹${sk.toLocaleString("en-IN")}`;
            if (currentStrike ? sk === currentStrike : (sk === atmStrike)) {
              opt.selected = true;
            }
            strikeSelect.appendChild(opt);
          });
          strikeSelect.onchange = () => window.recalculateTargetSL();
        }

        // Initialize Spot Target inputs if empty
        const tgtSpotInput = document.getElementById("te-input-target-spot");
        const slSpotInput = document.getElementById("te-input-sl-spot");
        if (tgtSpotInput && (!tgtSpotInput.value || tgtSpotInput.value === "24570")) {
          const bullTgt = (data.targets && data.targets.bull) ? data.targets.bull.target : (data.spot + 60.0);
          tgtSpotInput.value = bullTgt.toFixed(1);
        }
        if (slSpotInput && (!slSpotInput.value || slSpotInput.value === "24460")) {
          slSpotInput.value = (data.spot - 35.0).toFixed(1);
        }

        // Trigger initial calculation
        setTimeout(() => {
          if (window.recalculateTargetSL) window.recalculateTargetSL();
        }, 80);
      }
    } catch (e) {
      console.error("Error loading target engine:", e);
    }
  }

  // Target Engine Calculator State & Methods
  const teCalcState = {
    mode: "calc",
  };

  window.switchTEMode = function (mode) {
    teCalcState.mode = mode;
    const btnCalc = document.getElementById("te-mode-calc");
    const btnSliders = document.getElementById("te-mode-sliders");
    const panelCalc = document.getElementById("te-calc-panel");
    const panelSliders = document.getElementById("te-sliders-panel");
    if (btnCalc && btnSliders) {
      if (mode === "calc") {
        btnCalc.classList.add("active");
        btnSliders.classList.remove("active");
        if (panelCalc) panelCalc.style.display = "block";
        if (panelSliders) panelSliders.style.display = "none";
      } else {
        btnSliders.classList.add("active");
        btnCalc.classList.remove("active");
        if (panelCalc) panelCalc.style.display = "none";
        if (panelSliders) panelSliders.style.display = "block";
      }
    }
  };

  window.applyTEPreset = function (preset) {
    const tgtInput = document.getElementById("te-input-target-spot");
    const slInput = document.getElementById("te-input-sl-spot");
    if (!tgtInput || !slInput) return;

    const baseSpot = (state.teData && state.teData.spot) ? state.teData.spot : (state.spot || 24500.0);
    const ceRadio = document.querySelector('input[name="te_calc_type"][value="CE"]');
    const peRadio = document.querySelector('input[name="te_calc_type"][value="PE"]');

    const targets = (state.teData && state.teData.targets) ? state.teData.targets : {};
    const bTgt = targets.bull ? targets.bull.target : baseSpot + 60.0;
    const bSl = targets.bull && targets.bull.stop ? targets.bull.stop : baseSpot - 35.0;
    const bearTgt = targets.bear ? targets.bear.target : baseSpot - 60.0;
    const bearSl = targets.bear && targets.bear.stop ? targets.bear.stop : baseSpot + 35.0;
    const basePin = targets.base ? targets.base.target : baseSpot;
    const baseSl = baseSpot - 25.0;

    // Toggle visual active button state in both panels
    ["bull", "bear", "base", "reset"].forEach((p) => {
      const b1 = document.getElementById(`te-btn-preset-${p}`);
      const b2 = document.getElementById(`te-slider-preset-${p}`);
      if (b1) {
        if (p === preset) b1.classList.add("active");
        else b1.classList.remove("active");
      }
      if (b2) {
        if (p === preset) b2.classList.add("active");
        else b2.classList.remove("active");
      }
    });

    if (preset === "bull") {
      tgtInput.value = bTgt.toFixed(1);
      slInput.value = bSl.toFixed(1);
      if (ceRadio) ceRadio.checked = true;
    } else if (preset === "bear") {
      tgtInput.value = bearTgt.toFixed(1);
      slInput.value = bearSl.toFixed(1);
      if (peRadio) peRadio.checked = true;
    } else if (preset === "base") {
      const isCe = ceRadio && ceRadio.checked;
      if (isCe) {
        tgtInput.value = (basePin + 45.0).toFixed(1);
        slInput.value = baseSl.toFixed(1);
      } else {
        tgtInput.value = (basePin - 45.0).toFixed(1);
        slInput.value = (baseSpot + 25.0).toFixed(1);
      }
    } else if (preset === "reset") {
      tgtInput.value = (baseSpot + 60.0).toFixed(1);
      slInput.value = (baseSpot - 35.0).toFixed(1);
      if (ceRadio) ceRadio.checked = true;
      const hSlider = document.getElementById("te-slider-holding-mins");
      if (hSlider) {
        hSlider.value = "60";
        const hVal = document.getElementById("te-val-holding-mins");
        if (hVal) hVal.textContent = "60 mins";
      }
      const shockSel = document.getElementById("te-select-iv-shock");
      if (shockSel) shockSel.selectedIndex = 0;

      // Reset strike to ATM
      const strikeSel = document.getElementById("te-calc-strike");
      if (strikeSel && state.teData && state.teData.atm_strike) {
        strikeSel.value = state.teData.atm_strike;
      }
    }

    // Also synchronize Mode 2 Custom What-If Sliders
    const spotShift = document.getElementById("slider-spot-shift");
    const ivShock = document.getElementById("slider-iv-shock");
    const decayMins = document.getElementById("slider-decay-mins");
    if (spotShift && ivShock && decayMins) {
      if (preset === "bull") {
        spotShift.value = "60";
        ivShock.value = "-0.5";
        decayMins.value = "30";
      } else if (preset === "bear") {
        spotShift.value = "-60";
        ivShock.value = "1.0";
        decayMins.value = "30";
      } else if (preset === "base") {
        spotShift.value = "0";
        ivShock.value = "-0.2";
        decayMins.value = "60";
      } else if (preset === "reset") {
        spotShift.value = "0";
        ivShock.value = "0.0";
        decayMins.value = "60";
      }
      spotShift.dispatchEvent(new Event("input"));
    }

    window.recalculateTargetSL();
  };

  window.recalculateTargetSL = async function () {
    try {
      const strikeSel = document.getElementById("te-calc-strike");
      const expSel = document.getElementById("te-calc-expiry");
      const typeRadio = document.querySelector('input[name="te_calc_type"]:checked');
      const tgtInput = document.getElementById("te-input-target-spot");
      const slInput = document.getElementById("te-input-sl-spot");
      const hSlider = document.getElementById("te-slider-holding-mins");
      const ivSelect = document.getElementById("te-select-iv-shock");

      const customStrikeEl = document.getElementById("te-calc-custom-strike");
      const customStrike = customStrikeEl && customStrikeEl.value !== "" ? parseFloat(customStrikeEl.value) : NaN;
      const strike = Number.isFinite(customStrike)
        ? customStrike
        : (strikeSel ? parseFloat(strikeSel.value) : state.spot);
      const expiry = expSel ? expSel.value : "current_week";
      const side = typeRadio ? typeRadio.value : "CE";
      const targetSpot = tgtInput ? parseFloat(tgtInput.value) : (state.spot + 60);
      const slSpot = slInput ? parseFloat(slInput.value) : (state.spot - 35);
      const horizonMins = hSlider ? parseInt(hSlider.value) : 60;
      const ivShock = ivSelect ? ivSelect.value : "Auto (Regime-Conditioned)";

      const payload = {
        symbol: state.symbol,
        side: side,
        strike: strike,
        expiry: expiry,
        target_spot: targetSpot,
        sl_spot: slSpot,
        holding_minutes: horizonMins,
        iv_regime_shock: ivShock,
      };

      const res = await fetch("/api/target-engine/reprice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!data.target || !data.stop_loss) return;

      // 1. Update Contract Strip
      const sTitle = document.getElementById("te-strip-title");
      if (sTitle) sTitle.textContent = data.contract || `${state.symbol} ${expiry} ${strike} ${side}`;
      const sSub = document.getElementById("te-strip-sub");
      if (sSub) sSub.textContent = `(DTE: ${data.dte}d · Lot: ${data.lot_size} · Underlying Fut: ₹${Number(data.base_fut).toLocaleString("en-IN", { minimumFractionDigits: 1 })})`;
      const sLtp = document.getElementById("te-strip-ltp");
      if (sLtp) sLtp.textContent = `₹${data.entry_premium.toFixed(2)}`;
      const sTheo = document.getElementById("te-strip-theo");
      if (sTheo) sTheo.textContent = `₹${data.p_theo_curr.toFixed(2)}`;
      const sIv = document.getElementById("te-strip-iv");
      if (sIv) sIv.textContent = `${data.strike_iv_pct.toFixed(1)}%`;
      const sDelta = document.getElementById("te-strip-delta");
      if (sDelta) sDelta.textContent = `${data.greeks.delta >= 0 ? "+" : ""}${data.greeks.delta.toFixed(3)}`;
      const sTheta = document.getElementById("te-strip-theta");
      if (sTheta) sTheta.textContent = `${data.greeks.theta.toFixed(2)}`;
      const sVega = document.getElementById("te-strip-vega");
      if (sVega) sVega.textContent = `${data.greeks.vega >= 0 ? "+" : ""}${data.greeks.vega.toFixed(2)}`;

      // Store contract telemetry for What-If Sliders
      state.teRepriceEntry = data.entry_premium;
      if (data.greeks) {
        state.teRepriceDelta = data.greeks.delta;
        state.teRepriceGamma = data.greeks.gamma;
        state.teRepriceTheta = data.greeks.theta;
        state.teRepriceVega = data.greeks.vega;
      }
      state.teRepriceContract = data.contract;
      if (window.updateWhatIfRepricer) window.updateWhatIfRepricer();

      // 2. Update 4 Real-Time Outcome Metric Cards
      const outTgtLtp = document.getElementById("te-out-tgt-ltp");
      if (outTgtLtp) outTgtLtp.textContent = `₹${data.target.premium.toFixed(2)}`;
      const outTgtGain = document.getElementById("te-out-tgt-gain");
      if (outTgtGain) outTgtGain.textContent = `${data.target.gain_pts >= 0 ? "+" : ""}${data.target.gain_pts.toFixed(2)} pts (${data.target.gain_pct >= 0 ? "+" : ""}${data.target.gain_pct.toFixed(1)}%)`;
      const outTgtSub = document.getElementById("te-out-tgt-sub");
      if (outTgtSub) outTgtSub.textContent = `Target PnL / Lot: ${data.target.gain_inr >= 0 ? "+" : ""}₹${Number(data.target.gain_inr).toLocaleString("en-IN")} · P(Touch): ${data.target.p_touch}%`;

      const outSlLtp = document.getElementById("te-out-sl-ltp");
      if (outSlLtp) outSlLtp.textContent = `₹${data.stop_loss.premium.toFixed(2)}`;
      const outSlLoss = document.getElementById("te-out-sl-loss");
      if (outSlLoss) outSlLoss.textContent = `-${data.stop_loss.loss_pts.toFixed(2)} pts (-${data.stop_loss.loss_pct.toFixed(1)}%)`;
      const outSlSub = document.getElementById("te-out-sl-sub");
      if (outSlSub) outSlSub.textContent = `Risk / Lot: -₹${Math.abs(Number(data.stop_loss.loss_inr)).toLocaleString("en-IN")} · P(Touch SL): ${data.stop_loss.p_touch}%`;

      const outRrRatio = document.getElementById("te-out-rr-ratio");
      if (outRrRatio) outRrRatio.textContent = `1 : ${Number(data.risk_reward.ratio).toFixed(2)}`;
      const outRrVerdict = document.getElementById("te-out-rr-verdict");
      if (outRrVerdict) {
        outRrVerdict.textContent = data.risk_reward.verdict;
        outRrVerdict.style.color = data.risk_reward.ratio >= 1.8 ? "var(--go)" : "var(--spark)";
      }
      const outRrSub = document.getElementById("te-out-rr-sub");
      if (outRrSub) outRrSub.textContent = `Net Spread: ${data.risk_reward.net_spread_pts >= 0 ? "+" : ""}${data.risk_reward.net_spread_pts.toFixed(1)} pts · Breakeven: ₹${Number(data.risk_reward.breakeven).toLocaleString("en-IN", { minimumFractionDigits: 1 })}`;

      const outEvVal = document.getElementById("te-out-ev-val");
      if (outEvVal) {
        outEvVal.textContent = `${data.touch_weighted_ev.net_ev_inr >= 0 ? "+" : ""}₹${Number(data.touch_weighted_ev.net_ev_inr).toLocaleString("en-IN")}`;
        outEvVal.style.color = data.touch_weighted_ev.net_ev_inr >= 0 ? "var(--go)" : "var(--stop)";
      }
      const outEvVerdict = document.getElementById("te-out-ev-verdict");
      if (outEvVerdict) {
        outEvVerdict.textContent = data.touch_weighted_ev.expectancy;
        outEvVerdict.style.color = data.touch_weighted_ev.net_ev_inr >= 0 ? "var(--go)" : "var(--stop)";
      }
      const outEvSub = document.getElementById("te-out-ev-sub");
      if (outEvSub) outEvSub.textContent = `Target IV: ${data.target.iv_at_target.toFixed(1)}% (${data.target.d_iv_pts >= 0 ? "+" : ""}${data.target.d_iv_pts.toFixed(1)} pts)`;

      // 3. Greek Attribution Breakdown Table
      const attrBody = document.getElementById("te-greek-attr-body");
      if (attrBody && data.greek_attribution) {
        attrBody.innerHTML = data.greek_attribution.map((row) => `
          <tr style="border-bottom:1px solid rgba(255,255,255,0.06);font-family:var(--mono)">
            <td style="padding:6px;font-weight:700">${row.phase}</td>
            <td style="padding:6px;color:var(--ink-mute)">${row.spot_move}</td>
            <td style="padding:6px;font-weight:700;color:${row.full_pnl.startsWith('-') ? 'var(--stop)' : 'var(--go)'}">${row.full_pnl}</td>
            <td style="padding:6px;color:#38bdf8">${row.delta_pnl}</td>
            <td style="padding:6px;color:#c084fc">${row.gamma_pnl}</td>
            <td style="padding:6px;color:#facc15">${row.vega_pnl}</td>
            <td style="padding:6px;color:#f87171">${row.theta_bleed}</td>
            <td style="padding:6px;color:var(--ink-mute)">${row.residual}</td>
          </tr>
        `).join("");
      }
    } catch (e) {
      console.error("Error recalculating Target / SL:", e);
    }
  };

  window.updateSizerPreferences = function () {
    const cap = document.getElementById("sizer-cap-select");
    const risk = document.querySelector('input[name="sizer_risk_radio"]:checked');
    if (cap) state.capital = parseFloat(cap.value);
    if (risk) state.riskPct = parseFloat(risk.value);
    loadRecommendations();
  };

  async function loadRecommendations() {
    try {
      const res = await fetch(`/api/recommendations?symbol=${state.symbol}&window=${state.window}&capital=${state.capital}&risk=${state.riskPct}`);
      const data = await res.json();
      const lotEl = document.getElementById("hud-lot-size");
      const lot = data.hud && data.hud.lot_size;
      if (lotEl && lot) lotEl.textContent = `${lot} Qty/lot`;

      // Check if off-market closed card needs to be shown
      let noticeContainer = document.getElementById("rec-closed-notice-container");
      if (!noticeContainer) {
        noticeContainer = document.createElement("div");
        noticeContainer.id = "rec-closed-notice-container";
        const targetSection = document.getElementById("higher-ce-container")?.closest("div[style*='margin-bottom:28px']");
        if (targetSection && targetSection.parentNode) {
          targetSection.parentNode.insertBefore(noticeContainer, targetSection);
        }
      }

      // Populate effectiveness metrics if present
      if (data.hud) {
        const riskInr = Number(data.hud.max_risk_budget || 0);
        const capInr = Number(data.hud.account_capital || 0);
        const budgetEl = document.getElementById("hud-risk-budget");
        const sizerBudget = document.getElementById("sizer-budget-val");
        const outlayEl = document.getElementById("hud-outlay-cap");
        if (budgetEl) budgetEl.textContent = `₹${riskInr.toLocaleString("en-IN")} / trade`;
        if (sizerBudget) sizerBudget.textContent = `₹${riskInr.toLocaleString("en-IN")} / trade`;
        if (outlayEl) outlayEl.textContent = `₹${Math.round(capInr * 0.15).toLocaleString("en-IN")}`;
      }
      if (data.effectiveness_summary) {
        const eff = data.effectiveness_summary;
        const totalTrackedEl = document.getElementById("eff-total-tracked");
        const tgtRateEl = document.getElementById("eff-tgt-rate");
        const slRateEl = document.getElementById("eff-sl-rate");
        const invalRateEl = document.getElementById("eff-inval-rate");
        const avgRrEl = document.getElementById("eff-avg-rr");
        if (totalTrackedEl) totalTrackedEl.textContent = `${eff.total_tracked} setups`;
        if (tgtRateEl) tgtRateEl.textContent = `${eff.target_hit_rate}%`;
        if (slRateEl) slRateEl.textContent = `${eff.stop_loss_rate}%`;
        if (invalRateEl) invalRateEl.textContent = `${eff.spot_invalidation_rate}%`;
        if (avgRrEl) avgRrEl.textContent = eff.avg_realized_rr == null ? "—" : `1 : ${eff.avg_realized_rr}`;
      }

      if (data.is_market_closed) {
        if (noticeContainer) {
          noticeContainer.innerHTML = `
            <div style="background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:32px 24px;text-align:center;margin-bottom:24px">
              <div style="font-size:2rem;margin-bottom:10px">🌙</div>
              <div style="font-size:1.1rem;font-weight:800;color:var(--ink);margin-bottom:6px">Session Closed · Off-Market Hours</div>
              <p style="font-size:0.84rem;color:var(--ink-mute);margin:0 auto;max-width:580px;line-height:1.5">
                Broker API polling and NSE live feeds are paused after market hours.
                Recommendations run during the F&O session (09:15 – 15:40 IST).
              </p>
            </div>
          `;
        }
        renderClusterList("higher-ce-container", [], "CE", false);
        renderClusterList("higher-pe-container", [], "PE", false);
        renderClusterList("fallback-ce-container", [], "CE", false);
        renderClusterList("fallback-pe-container", [], "PE", false);
        return;
      }
      if (noticeContainer) noticeContainer.innerHTML = "";

      // Render Higher Conviction: CE on Left, PE on Right
      renderClusterList("higher-ce-container", data.higher_conviction ? data.higher_conviction.ce_clusters : [], "CE", false);
      renderClusterList("higher-pe-container", data.higher_conviction ? data.higher_conviction.pe_clusters : [], "PE", false);

      // Render Secondary Conviction: CE on Left, PE on Right
      renderClusterList("fallback-ce-container", data.fallback_tier ? data.fallback_tier.ce_clusters : [], "CE", data.ce_fallback_active);
      renderClusterList("fallback-pe-container", data.fallback_tier ? data.fallback_tier.pe_clusters : [], "PE", data.pe_fallback_active);
    } catch (e) {
      console.error("Error loading recommendations:", e);
    }
  }

  // Active strategy indices per cluster
  const activeClusterStratIdx = {};

  function renderClusterList(containerId, clusters, side, isFallbackActive) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = "";

    if (!clusters || clusters.length === 0) {
      const emptyMsg = isFallbackActive
        ? `Secondary ${side} safety filter active — no qualified secondary setups.`
        : `No ${side} candidate clusters currently active in this conviction tier.`;
      container.innerHTML = `<div style="color:var(--ink-mute);font-size:0.8rem;padding:14px;background:var(--surface-2);border-radius:6px;border:1px dashed var(--line);text-align:center">${emptyMsg}</div>`;
      return;
    }

    clusters.forEach((cluster, cIdx) => {
      const clusterKey = `${containerId}-${cIdx}`;
      if (activeClusterStratIdx[clusterKey] === undefined) {
        activeClusterStratIdx[clusterKey] = 0;
      }

      const activeIdx = Math.min(activeClusterStratIdx[clusterKey], (cluster.ideas || []).length - 1);
      const activeIdea = (cluster.ideas && cluster.ideas[activeIdx]) ? cluster.ideas[activeIdx] : cluster.best_idea;
      const best = activeIdea;

      const card = document.createElement("div");
      let cardClass = "rec-card";
      if (best.is_bookmarked) cardClass += " bookmarked";
      if (best.latch_state === "TARGET_HIT") cardClass += " event-tgt";
      if (best.latch_state === "INVALIDATED") cardClass += " event-sl";

      card.className = cardClass;
      card.id = `rec-card-${best.rec_id}`;

      // 1. Confluence Banner & Multi-Strategy Switcher
      let confHeaderHtml = "";
      if (cluster.ideas && cluster.ideas.length > 1) {
        const stratTagsHtml = cluster.ideas
          .map((idea, i) => {
            const isSel = i === activeIdx;
            const clr = isSel ? "var(--spark)" : "var(--ink-mute)";
            const bg = isSel ? "rgba(56,189,248,0.2)" : "var(--surface-3)";
            return `<button class="confluence-strat-tag" style="background:${bg};color:${clr};border:1px solid ${isSel ? "var(--spark)" : "var(--line)"};padding:3px 8px;border-radius:4px;font-size:0.68rem;cursor:pointer" onclick="window.switchClusterStrategy('${clusterKey}', ${i})">${idea.strategy_type || idea.play_badge} (${idea.quality_score})</button>`;
          })
          .join("");

        const stratTableRows = cluster.ideas
          .map((idea, i) => {
            const isSel = i === activeIdx;
            return `
              <tr style="${isSel ? "background:rgba(56,189,248,0.08);font-weight:700" : ""}">
                <td>${idea.strategy_type || idea.play_badge}</td>
                <td style="color:var(--spark)">${idea.quality_score}/100</td>
                <td style="color:var(--go)">₹${idea.target_1.toFixed(1)}</td>
                <td style="color:var(--stop)">₹${idea.stop_loss.toFixed(1)}</td>
                <td>${idea.risk_reward_ratio}</td>
                <td>₹${(idea.pos_size.max_risk_inr || 0).toLocaleString("en-IN")}</td>
                <td style="color:var(--go)">+₹${(idea.pos_size.target_1_pnl_inr || 0).toLocaleString("en-IN")}</td>
              </tr>
            `;
          })
          .join("");

        confHeaderHtml = `
          <div class="confluence-banner">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
              <span style="font-weight:800;color:var(--spark)">⚡ MULTI-STRATEGY CONFLUENCE (${cluster.ideas.length} Strategies)</span>
              <span style="font-size:0.68rem;background:rgba(56,189,248,.2);padding:2px 6px;border-radius:4px;font-weight:750">Unified Strike ₹${best.strike}</span>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
              ${stratTagsHtml}
            </div>
            <!-- Comparative Strategy Matrix -->
            <table class="conf-matrix-table" style="width:100%;font-size:0.68rem;margin-bottom:4px;border-collapse:collapse">
              <thead>
                <tr style="color:var(--ink-mute);text-align:left;border-bottom:1px solid var(--line)">
                  <th>Strategy</th>
                  <th>Score</th>
                  <th>Tgt 1</th>
                  <th>SL</th>
                  <th>R:R</th>
                  <th>Risk ₹</th>
                  <th>Target EV ₹</th>
                </tr>
              </thead>
              <tbody>
                ${stratTableRows}
              </tbody>
            </table>
          </div>
        `;
      } else if (cluster.is_confluence) {
        confHeaderHtml = `
          <div class="confluence-banner">
            <div>${cluster.confluence_badge}</div>
            <span style="font-size:0.68rem;background:rgba(56,189,248,.2);padding:2px 6px;border-radius:4px;font-weight:750">${cluster.confluence_sub}</span>
          </div>
        `;
      }

      // State machine chip & badges
      const bmText = best.is_bookmarked ? "★ Pinned" : "☆ Pin Card";
      const bmBtnType = best.is_bookmarked ? "btn-primary" : "btn-secondary";

      // Defined Risk Spread Alternative Box
      let spreadBoxHtml = "";
      if (best.spread_alternative) {
        const sp = best.spread_alternative;
        const maxProfitPts = sp.max_profit != null ? sp.max_profit : (sp.max_profit_pts || 0);
        const maxLossPts = sp.max_loss != null ? sp.max_loss : (sp.max_loss_pts || 0);
        const maxProfitInr = sp.max_profit_inr != null ? sp.max_profit_inr : Math.round(maxProfitPts * 25);
        const maxLossInr = sp.max_loss_inr != null ? sp.max_loss_inr : Math.round(maxLossPts * 25);
        const beSpot = sp.breakeven_spot != null ? sp.breakeven_spot : (sp.breakeven || 0);
        const netDebit = sp.net_debit != null ? sp.net_debit : (sp.net_cost || 0);

        spreadBoxHtml = `
          <div style="background:rgba(56,189,248,0.06);border:1px solid rgba(56,189,248,0.25);border-radius:6px;padding:8px 12px;margin:8px 0;font-size:0.74rem">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
              <span style="font-weight:800;color:var(--spark)">🛡️ Defined-Risk Spread: ${sp.spread_name || "Debit Vertical"}</span>
              <span style="font-family:var(--mono);color:var(--ink-mute)">Net Debit: ₹${Number(netDebit).toFixed(1)}</span>
            </div>
            <div style="display:flex;justify-content:space-between;font-family:var(--mono);color:var(--ink)">
              <span>Max Gain: <b style="color:var(--go)">+₹${Number(maxProfitInr).toLocaleString("en-IN")} (+${Number(maxProfitPts).toFixed(1)} pts)</b></span>
              <span>Max Risk: <b style="color:var(--stop)">-₹${Number(maxLossInr).toLocaleString("en-IN")} (-${Number(maxLossPts).toFixed(1)} pts)</b></span>
              <span>Breakeven: <b>₹${Number(beSpot).toLocaleString("en-IN", { minimumFractionDigits: 1 })}</b></span>
            </div>
          </div>
        `;
      }

      // Trajectory Audit
      let trajectoryHtml = "";
      if (best.score_history && best.score_history.length > 1) {
        const histStr = best.score_history.map((s) => `<b>${s.score}</b>`).join(" ➔ ");
        trajectoryHtml = `
          <div style="font-size:0.68rem;color:var(--ink-mute);background:var(--surface-2);padding:4px 8px;border-radius:4px;margin-top:6px;display:flex;justify-content:space-between">
            <span>Score Trajectory: ${histStr}</span>
            <span style="color:var(--go);font-weight:750">● Momentum Confirmed</span>
          </div>
        `;
      }

      // State Machine Label & Lock Status
      let stateBadgeHtml = "";
      const stCode = (best.latch_state || "ACTIVE").toUpperCase();
      if (stCode === "ACTIVE" || stCode === "LATCHED") {
        stateBadgeHtml = `<span class="rec-state-tag" style="background:rgba(34,197,94,.15);color:var(--go);border:1px solid rgba(34,197,94,.4);padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:800;display:inline-flex;align-items:center;gap:3px">🔒 LATCHED</span>`;
      } else if (stCode === "TARGET_HIT") {
        stateBadgeHtml = `<span class="rec-state-tag" style="background:rgba(34,197,94,.15);color:var(--go);border:1px solid rgba(34,197,94,.4);padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:800;display:inline-flex;align-items:center;gap:3px">🎯 TARGET HIT</span>`;
      } else if (stCode === "INVALIDATED") {
        stateBadgeHtml = `<span class="rec-state-tag" style="background:rgba(239,68,68,.15);color:var(--stop);border:1px solid rgba(239,68,68,.4);padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:800;display:inline-flex;align-items:center;gap:3px">🛑 INVALIDATED</span>`;
      } else if (stCode === "QUALIFIED") {
        stateBadgeHtml = `<span class="rec-state-tag" style="background:rgba(56,189,248,.15);color:var(--spark);border:1px solid rgba(56,189,248,.4);padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:800;display:inline-flex;align-items:center;gap:3px">⚡ QUALIFIED</span>`;
      } else {
        stateBadgeHtml = `<span class="rec-state-tag" style="background:rgba(234,179,8,.15);color:var(--warn);border:1px solid rgba(234,179,8,.4);padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:800;display:inline-flex;align-items:center;gap:3px">⚠️ ${stCode}</span>`;
      }

      const extraTagsHtml = (stCode === "ACTIVE" || stCode === "LATCHED")
        ? `<span style="font-size:0.68rem;background:rgba(34,197,94,.15);color:var(--go);padding:2px 7px;border-radius:4px;border:1px solid rgba(34,197,94,.3);font-weight:750">🔒 LATCHED</span>`
        : (stCode === "TARGET_HIT")
        ? `<span style="font-size:0.68rem;background:rgba(34,197,94,.15);color:var(--go);padding:2px 7px;border-radius:4px;border:1px solid rgba(34,197,94,.3);font-weight:750">🎯 TARGET HIT</span>`
        : (stCode === "INVALIDATED")
        ? `<span style="font-size:0.68rem;background:rgba(239,68,68,.15);color:var(--stop);padding:2px 7px;border-radius:4px;border:1px solid rgba(239,68,68,.3);font-weight:750">🛑 INVALIDATED</span>`
        : "";

      card.innerHTML = `
        ${confHeaderHtml}
        <div class="rec-header-row">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="rec-counter-chip" 
                  data-latch-state="${best.latch_state}" 
                  data-first-active="${best.first_active_ts}" 
                  data-last-active="${best.last_active_ts}">
              ${best.active_label}
            </span>
            ${stateBadgeHtml}
            <span style="font-size:0.75rem;font-weight:750;color:${best.conviction_color}">
              Score ${best.quality_score}/100 (${best.conviction_label})
            </span>
          </div>
          <div>
            <span class="rec-badge-new">NEW CYCLE</span>
          </div>
        </div>

        <div class="rec-symbol-row">
          <span class="rec-symbol ${best.side.toLowerCase()}">${best.option_symbol}</span>
          <span style="font-size:0.75rem;font-weight:750;color:var(--spark)">${best.play_badge}</span>
          <span style="font-size:0.75rem;color:var(--ink-mute)">R:R ${best.risk_reward_ratio}</span>
        </div>

        <div class="rec-ltp-block">
          <span id="rec-ltp-${best.rec_id}" class="rec-ltp-large" style="color:var(--ink)">₹${best.current_ltp.toFixed(1)}</span>
          <span id="rec-live-badge-${best.rec_id}" style="font-size:0.72rem;background:var(--spark-bg);color:${best.is_ws_live ? "var(--spark)" : "var(--ink-mute)"};padding:2px 6px;border-radius:4px;font-family:var(--mono);font-weight:700">${best.is_ws_live ? "⚡ WS 0ms" : "⚡ MTM"}</span>
          <span style="font-size:0.72rem;background:var(--surface-2);color:var(--ink-mute);padding:2px 6px;border-radius:4px;font-family:var(--mono)">Entry: ₹${best.entry_ltp.toFixed(1)}</span>
          <span id="rec-mtm-${best.rec_id}" style="font-size:0.72rem;font-weight:750;font-family:var(--mono);color:${best.pnl_pts >= 0 ? "var(--go)" : "var(--stop)"}">
            MTM: ${best.pnl_pts >= 0 ? "+" : ""}₹${best.pnl_pts.toFixed(1)} (${best.pnl_pct >= 0 ? "+" : ""}${best.pnl_pct.toFixed(1)}%)
          </span>
          <span id="rec-t1-${best.rec_id}" style="font-size:0.72rem;background:rgba(34,197,94,.1);color:var(--go);padding:2px 6px;border-radius:4px;font-family:var(--mono)">T1: ₹${best.target_1.toFixed(1)}</span>
          <span id="rec-sl-${best.rec_id}" style="font-size:0.72rem;background:rgba(239,68,68,.1);color:var(--stop);padding:2px 6px;border-radius:4px;font-family:var(--mono)">SL: ₹${best.stop_loss.toFixed(1)}</span>
        </div>

        <!-- Position Sizing Outlay Strip -->
        <div style="background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:7px 12px;margin:8px 0;font-size:0.75rem;display:flex;justify-content:space-between">
          <span>📦 <b>${best.pos_size.suggested_lots} Lots</b> (${best.pos_size.total_quantity} Qty) · Outlay: <b>₹${best.pos_size.capital_outlay_inr.toLocaleString("en-IN")}</b> (${best.pos_size.portfolio_pct.toFixed(1)}% Port)</span>
          <span style="color:var(--ink-mute)">${best.pos_size.sizing_rationale}</span>
        </div>

        <!-- Trigger & Invalidation Box -->
        <div style="background:rgba(0,0,0,0.25);border:1px solid var(--line);border-radius:6px;padding:9px 12px;margin:8px 0;font-size:0.78rem">
          <div><b>⚡ Trigger:</b> ${best.entry_trigger}</div>
          <div style="margin-top:6px;padding-top:6px;border-top:1px dashed var(--line);display:flex;justify-content:space-between;align-items:center">
            <span style="color:var(--stop)"><b>🛑 Spot Invalidation:</b> ₹${best.spot_invalidation.toLocaleString("en-IN")} — ${best.spot_invalidation_text}</span>
            <span style="font-size:0.7rem;font-weight:750;color:${best.inval_breached ? "var(--stop)" : "var(--go)"}">
              ${best.inval_breached ? `🛑 INVALIDATED (-${Math.abs(best.inval_buffer).toFixed(0)} pts)` : `🟢 Active (+${best.inval_buffer.toFixed(0)} pts buffer)`}
            </span>
          </div>
        </div>

        <!-- Key Levels 4-Grid -->
        <div class="rec-levels-grid">
          <div class="rec-lvl-item">
            <small>Spot Inval / SL</small>
            <b style="color:var(--stop)">₹${best.spot_invalidation.toLocaleString("en-IN")}</b>
            <span style="font-size:0.68rem;color:var(--stop)">Max Risk -₹${best.pos_size.max_risk_inr.toLocaleString("en-IN")}</span>
          </div>
          <div class="rec-lvl-item">
            <small>Target 1</small>
            <b id="rec-t1-grid-${best.rec_id}" style="color:var(--go)">₹${best.target_1.toFixed(1)}</b>
            <span style="font-size:0.68rem;color:var(--go)">+₹${best.pos_size.target_1_pnl_inr.toLocaleString("en-IN")}</span>
          </div>
          <div class="rec-lvl-item">
            <small>Target 2</small>
            <b id="rec-t2-grid-${best.rec_id}" style="color:var(--go)">₹${best.target_2.toFixed(1)}</b>
            <span style="font-size:0.68rem;color:var(--go)">+₹${best.pos_size.target_2_pnl_inr.toLocaleString("en-IN")}</span>
          </div>
          <div class="rec-lvl-item">
            <small>Risk : Reward</small>
            <b style="color:var(--spark)">${best.risk_reward_ratio}</b>
            <span style="font-size:0.68rem;color:var(--ink-mute)">Quality ${best.quality_score}/100</span>
          </div>
        </div>

        ${spreadBoxHtml}

        <!-- Probabilities & Delta Strip -->
        <div style="display:flex;justify-content:space-between;font-size:0.72rem;color:var(--ink-mute);font-family:var(--mono);background:var(--surface-2);padding:5px 10px;border-radius:4px;margin:6px 0">
          <span>P(T1): <b style="color:var(--go)">${Math.round((best.p_touch_target_1 || 0.65) * 100)}%</b> · P(SL): <b style="color:var(--stop)">${Math.round((best.p_touch_sl || 0.35) * 100)}%</b></span>
          <span>EV: <b style="color:${(best.net_ev_pts || 0) >= 0 ? "var(--go)" : "var(--stop)"}">${(best.net_ev_pts || 0) >= 0 ? "+" : ""}${(best.net_ev_pts || 0).toFixed(1)} pts</b> · Δ ~${(best.delta_approx || 0.50).toFixed(2)}</span>
        </div>

        <!-- Rationale & Tags -->
        <div style="font-size:0.78rem;color:var(--ink-mute);margin:8px 0">${best.rationale}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0">
          ${extraTagsHtml}
          ${best.catalyst_tags.map((t) => `<span style="font-size:0.68rem;background:var(--surface-3);color:var(--ink-mute);padding:2px 7px;border-radius:4px;border:1px solid var(--line)">${t}</span>`).join("")}
        </div>

        ${trajectoryHtml}

        <!-- Action Controls -->
        <div class="rec-actions">
          <button class="btn ${bmBtnType}" style="flex:1" onclick="window.toggleBookmark('${best.rec_id}')">${bmText}</button>
          <button class="btn btn-primary" style="flex:2.5" onclick="window.executePaperTrade('${best.rec_id}', '${best.option_symbol}', ${best.strike}, '${best.side}', ${best.current_ltp}, ${best.pos_size.suggested_lots})">
            📝 Paper-Trade ${best.pos_size.suggested_lots} Lots (₹${best.pos_size.capital_outlay_inr.toLocaleString("en-IN")})
          </button>
        </div>
      `;
      container.appendChild(card);
    });
  }

  window.switchClusterStrategy = function (clusterKey, idx) {
    activeClusterStratIdx[clusterKey] = idx;
    loadRecommendations();
  };

  const sentinelState = {
    timeframe: "1m",
    profile: "Full",
    chartHeight: 520,
  };

  async function loadSentinelTape() {
    try {
      const res = await fetch(`/api/sentinel-tape?symbol=${state.symbol}&timeframe=${sentinelState.timeframe}`);
      const data = await res.json();
      state.sentinelData = data;

      document.getElementById("sentinel-kpi-regime").textContent = data.kpis.cvd_regime;
      document.getElementById("sentinel-kpi-net-cvd").textContent = data.kpis.net_cvd;
      document.getElementById("sentinel-kpi-buyer-ratio").textContent = data.kpis.buyer_ratio;
      document.getElementById("sentinel-kpi-blocks").textContent = `${data.kpis.institutional_blocks} Blocks`;
      document.getElementById("sentinel-kpi-absorption").textContent = data.kpis.absorption_status;

      // 4 Quick Summary KPIs & Regimes
      if (data.summary_kpis) {
        const sk = data.summary_kpis;
        const netClr = sk.total_net_cvd >= 0 ? "var(--go)" : "var(--stop)";
        const regBadge = document.getElementById("sentinel-10d-regime-badge");
        if (regBadge) {
          regBadge.textContent = sk.total_net_cvd >= 0 ? "🟢 INSTITUTIONAL ACCUMULATION" : "🔴 INSTITUTIONAL DISTRIBUTION";
          regBadge.style.color = netClr;
          regBadge.style.background = sk.total_net_cvd >= 0 ? "var(--go-bg)" : "var(--stop-bg)";
        }
        const netBadge = document.getElementById("sentinel-10d-net-badge");
        if (netBadge) {
          netBadge.textContent = `${sk.total_net_cvd >= 0 ? "+" : ""}${sk.total_net_cvd.toLocaleString("en-IN")}`;
          netBadge.style.color = netClr;
        }
        const bRatio = document.getElementById("sentinel-10d-buyer-ratio");
        if (bRatio) bRatio.textContent = `${sk.buy_pct}%`;

        document.getElementById("kpi-10d-net-cvd").textContent = `${sk.total_net_cvd >= 0 ? "+" : ""}${sk.total_net_cvd.toLocaleString("en-IN")}`;
        document.getElementById("kpi-10d-net-cvd").style.color = netClr;
        document.getElementById("kpi-10d-avg-cvd").textContent = `Avg Daily: ${sk.total_net_cvd >= 0 ? "+" : ""}${Math.round(sk.total_net_cvd / (data.daily_suite ? data.daily_suite.length : 10)).toLocaleString("en-IN")}`;

        document.getElementById("kpi-10d-buy-vol").textContent = `${sk.tot_buy_vol.toLocaleString("en-IN")}`;
        document.getElementById("kpi-10d-buy-pct").textContent = `${sk.buy_pct}% of session volume`;

        document.getElementById("kpi-10d-sell-vol").textContent = `${sk.tot_sell_vol.toLocaleString("en-IN")}`;
        document.getElementById("kpi-10d-sell-pct").textContent = `${(100 - sk.buy_pct).toFixed(1)}% of session volume`;

        document.getElementById("kpi-10d-peaks").textContent = `${sk.peak_buy_day} vs ${sk.peak_sell_day}`;
        document.getElementById("kpi-10d-top-buy").textContent = `Top Buy: ${sk.peak_buy_vol.toLocaleString("en-IN")}`;
      }

      // Mount or update TradingView Lightweight Chart
      const mount = document.getElementById("sentinel-chart-mount");
      if (mount && window.LightweightCharts) {
        if (!state.sentinelChart) {
          mount.innerHTML = "";
          const isSentLight = state.theme === "light";
          state.sentinelChart = window.LightweightCharts.createChart(mount, {
            width: mount.clientWidth || 850,
            height: sentinelState.chartHeight,
            layout: {
              background: { color: isSentLight ? "#ffffff" : "#0c1424" },
              textColor: isSentLight ? "#334155" : "#94a3b8"
            },
            grid: {
              vertLines: { color: isSentLight ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" },
              horzLines: { color: isSentLight ? "rgba(15,23,42,0.06)" : "rgba(56,189,248,0.06)" }
            },
            crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
            rightPriceScale: {
              borderColor: isSentLight ? "rgba(203, 213, 225, 0.8)" : "rgba(51, 65, 85, 0.45)",
              autoScale: true,
              scaleMargins: { top: 0.08, bottom: 0.38 },
            },
            handleScale: {
              axisPressedMouseMove: { time: true, price: true },
              axisDoubleClickReset: { time: true, price: true },
              mouseWheel: true,
              pinch: true,
            },
            handleScroll: {
              mouseWheel: false, // Directly zoom candles with mouse wheel
              pressedMouseMove: true,
              horzTouchDrag: true,
              vertTouchDrag: true,
            },
            timeScale: {
              timeVisible: true,
              secondsVisible: false,
              borderColor: "rgba(51, 65, 85, 0.45)",
              barSpacing: 12,
              minBarSpacing: 3,
              rightOffset: 6,
            },
            localization: {
              timeFormatter: (ts) => istChartStamp(ts) || String(ts),
            },
          });

          // 1. Candlestick Series with GEX Walls Autoscale Support
          state.candlestickSeries = state.sentinelChart.addCandlestickSeries({
            upColor: "#10b981",
            downColor: "#ef4444",
            borderVisible: true,
            borderUpColor: "#10b981",
            borderDownColor: "#ef4444",
            wickUpColor: "#10b981",
            wickDownColor: "#ef4444",
            title: "Price",
            autoscaleInfoProvider: (original) => {
              const res = original();
              if (res !== null && res.priceRange) {
                let min = res.priceRange.minValue;
                let max = res.priceRange.maxValue;
                const range = Math.max(max - min, 20);
                const cw = state.sentinelCw || 0;
                const pw = state.sentinelPw || 0;
                if (cw > 0 && cw >= min && cw <= max + range * 0.8) {
                  max = Math.max(max, cw + 10);
                }
                if (pw > 0 && pw <= max && pw >= min - range * 0.8) {
                  min = Math.min(min, pw - 10);
                }
                return {
                  priceRange: { minValue: min, maxValue: max },
                  margins: res.margins,
                };
              }
              return res;
            },
          });

          // 2. Session VWAP & Envelopes
          state.vwapSeries = state.sentinelChart.addLineSeries({
            color: "#c084fc",
            lineWidth: 1.5,
            title: "VWAP",
          });
          state.up1Series = state.sentinelChart.addLineSeries({
            color: "rgba(192, 132, 252, 0.45)",
            lineWidth: 1,
            lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
            title: "+1σ",
          });
          state.dn1Series = state.sentinelChart.addLineSeries({
            color: "rgba(192, 132, 252, 0.45)",
            lineWidth: 1,
            lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
            title: "-1σ",
          });

          // 3. EMA 9 & EMA 21 Ribbon
          state.ema9Series = state.sentinelChart.addLineSeries({
            color: "#facc15",
            lineWidth: 2,
            title: "EMA 9",
          });
          state.ema21Series = state.sentinelChart.addLineSeries({
            color: "#38bdf8",
            lineWidth: 2,
            title: "EMA 21",
          });

          // 4. Volume Delta Histogram Subplot
          state.deltaSeries = state.sentinelChart.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: "delta_scale",
            title: "Δ Vol",
          });
          state.sentinelChart.priceScale("delta_scale").applyOptions({
            scaleMargins: { top: 0.85, bottom: 0 },
          });

          // 5. Cumulative Volume Delta (CVD) Line Subplot
          state.cvdSeries = state.sentinelChart.addLineSeries({
            color: "#06b6d4",
            lineWidth: 2,
            priceScaleId: "cvd_scale",
            title: "CVD",
          });
          state.sentinelChart.priceScale("cvd_scale").applyOptions({
            scaleMargins: { top: 0.68, bottom: 0.02 },
          });

          // Top Crosshair Hover Subscriber
          state.sentinelChart.subscribeCrosshairMove((param) => {
            if (!param || !param.time || !param.seriesData) return;
            const c = param.seriesData.get(state.candlestickSeries);
            const v = param.seriesData.get(state.vwapSeries);
            const d = param.seriesData.get(state.deltaSeries);
            const cv = param.seriesData.get(state.cvdSeries);

            const shlTime = document.getElementById("shl-time");
            const shlOhlc = document.getElementById("shl-ohlc");
            const shlVwap = document.getElementById("shl-vwap");
            const shlDelta = document.getElementById("shl-delta");
            const shlCvd = document.getElementById("shl-cvd");

            if (shlTime && param.time) {
              const ts = typeof param.time === "number" ? param.time : param.time.timestamp;
              if (ts) {
                shlTime.textContent = istChartStamp(ts);
              }
            }
            if (shlOhlc && c) {
              shlOhlc.textContent = `O: ₹${c.open.toFixed(1)} H: ₹${c.high.toFixed(1)} L: ₹${c.low.toFixed(1)} C: ₹${c.close.toFixed(1)}`;
            }
            if (shlVwap && v) {
              shlVwap.textContent = `VWAP: ₹${v.value.toFixed(1)}`;
            }
            if (shlDelta && d) {
              shlDelta.textContent = `Δ: ${(d.value || 0).toLocaleString("en-IN")}`;
            }
            if (shlCvd && cv) {
              shlCvd.textContent = `CVD: ${cv.value >= 0 ? "+" : ""}${Math.round(cv.value).toLocaleString("en-IN")}`;
            }
          });

          window.addEventListener("resize", () => {
            if (state.sentinelChart && mount) {
              state.sentinelChart.applyOptions({ width: mount.clientWidth });
            }
          });

          initChartDragHandle();
        }

        setTimeout(() => {
          if (state.sentinelChart && mount) {
            state.sentinelChart.applyOptions({ width: mount.clientWidth || 850 });
          }
        }, 50);
      }

      function computeEMA(candles, period) {
        if (!candles || candles.length === 0) return [];
        const k = 2.0 / (period + 1.0);
        let ema = candles[0].close;
        const res = [];
        for (let i = 0; i < candles.length; i++) {
          const c = candles[i];
          ema = c.close * k + ema * (1.0 - k);
          res.push({ time: c.time, value: parseFloat(ema.toFixed(1)) });
        }
        return res;
      }

      if (state.candlestickSeries && data.bars && data.bars.length > 0) {
        const cw = parseFloat(data.call_wall || 0);
        const pw = parseFloat(data.put_wall || 0);
        const flip = parseFloat(data.gamma_flip || 0);
        state.sentinelCw = cw;
        state.sentinelPw = pw;
        state.sentinelFlip = flip;

        const candleData = data.bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
        state.candlestickSeries.setData(candleData);
        if (candleData.length > 0) {
          sentinelState.currentBar = { ...candleData[candleData.length - 1] };
        }

        // 1. VWAP & Envelopes
        if (state.vwapSeries) {
          state.vwapSeries.setData(data.bars.filter((b) => b.vwap != null).map((b) => ({ time: b.time, value: b.vwap })));
        }
        if (state.up1Series) {
          state.up1Series.setData(data.bars.filter((b) => b.up1 != null).map((b) => ({ time: b.time, value: b.up1 })));
        }
        if (state.dn1Series) {
          state.dn1Series.setData(data.bars.filter((b) => b.dn1 != null).map((b) => ({ time: b.time, value: b.dn1 })));
        }

        // 2. EMA 9 & EMA 21 Ribbon
        const ema9Data = (data.ema9 && data.ema9.length) ? data.ema9 : computeEMA(candleData, 9);
        const ema21Data = (data.ema21 && data.ema21.length) ? data.ema21 : computeEMA(candleData, 21);
        if (state.ema9Series) state.ema9Series.setData(ema9Data);
        if (state.ema21Series) state.ema21Series.setData(ema21Data);

        // 3. Delta Histogram
        if (state.deltaSeries) {
          state.deltaSeries.setData(data.bars.map((b) => ({
            time: b.time,
            value: Math.abs(b.delta || 0),
            color: (b.delta || 0) >= 0 ? "rgba(34, 197, 94, 0.65)" : "rgba(239, 68, 68, 0.65)",
          })));
        }

        // 4. Cumulative Volume Delta (CVD)
        if (state.cvdSeries) {
          state.cvdSeries.setData(data.bars.map((b) => ({ time: b.time, value: b.cvd || 0 })));
        }

        // 5. Call Wall & Put Wall Price Lines
        if (state.callWallLine) {
          try { state.candlestickSeries.removePriceLine(state.callWallLine); } catch (e) {}
          state.callWallLine = null;
        }
        if (state.putWallLine) {
          try { state.candlestickSeries.removePriceLine(state.putWallLine); } catch (e) {}
          state.putWallLine = null;
        }
        if (state.gammaFlipLine) {
          try { state.candlestickSeries.removePriceLine(state.gammaFlipLine); } catch (e) {}
          state.gammaFlipLine = null;
        }

        if (cw > 0) {
          state.callWallLine = state.candlestickSeries.createPriceLine({
            price: cw,
            color: "#ef4444",
            lineWidth: 2,
            lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: `Call Wall ${Math.round(cw).toLocaleString("en-IN")}`,
          });
          const lCw = document.getElementById("legend-cw-val");
          if (lCw) lCw.textContent = Math.round(cw).toLocaleString("en-IN");
        }
        if (pw > 0) {
          state.putWallLine = state.candlestickSeries.createPriceLine({
            price: pw,
            color: "#22c55e",
            lineWidth: 2,
            lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dashed : 2,
            axisLabelVisible: true,
            title: `Put Wall ${Math.round(pw).toLocaleString("en-IN")}`,
          });
          const lPw = document.getElementById("legend-pw-val");
          if (lPw) lPw.textContent = Math.round(pw).toLocaleString("en-IN");
        }
        if (flip > 0) {
          state.gammaFlipLine = state.candlestickSeries.createPriceLine({
            price: flip,
            color: "#eab308",
            lineWidth: 1.5,
            lineStyle: window.LightweightCharts.LineStyle ? window.LightweightCharts.LineStyle.Dotted : 1,
            axisLabelVisible: true,
            title: `Gamma Flip ${Math.round(flip).toLocaleString("en-IN")}`,
          });
          const lFlip = document.getElementById("legend-flip-val");
          if (lFlip) lFlip.textContent = Math.round(flip).toLocaleString("en-IN");
        }

        // 6. Signal Markers on Candles
        if (data.signals && data.signals.length) {
          const markers = [];
          data.signals.forEach((s, idx) => {
            const sigIdx = Math.max(0, candleData.length - 1 - (idx * 6));
            if (candleData[sigIdx]) {
              markers.push({
                time: candleData[sigIdx].time,
                position: s.category === "BEARISH" ? "aboveBar" : "belowBar",
                color: s.category === "BULLISH" ? "#10b981" : (s.category === "BEARISH" ? "#ef4444" : "#f59e0b"),
                shape: s.category === "BEARISH" ? "arrowDown" : "arrowUp",
                text: `${s.type || s.category} (${s.score})`,
              });
            }
          });
          markers.sort((a, b) => a.time - b.time);
          try { state.candlestickSeries.setMarkers(markers); } catch (e) {}
        }

        // Apply active profile visibility
        window.switchSentinelProfile(sentinelState.profile);

        // Initial Render: Fit most recent 65 candles at a legible size
        sentinelState.totalBars = candleData.length;
        if (!sentinelState.initialFitDone && candleData.length > 0) {
          sentinelState.initialFitDone = true;
          const totalBars = candleData.length;
          const visibleBars = Math.min(totalBars, 65);
          state.sentinelChart.timeScale().setVisibleLogicalRange({
            from: Math.max(0, totalBars - visibleBars),
            to: totalBars + 5,
          });
        }
      }

      // Subtab 1: Render SVG 10-Day Closing CVD Bar Chart
      renderCvdBarChart(data.daily_suite);

      // Subtab 2: Render SVG Buyer vs Seller Volume Breakdown
      renderVolBreakdownChart(data.daily_suite);

      // Subtab 3: 10-Day Historical Daily Ledger
      const cvdBody = document.getElementById("daily-cvd-suite-body");
      if (cvdBody && data.daily_suite) {
        cvdBody.innerHTML = "";
        data.daily_suite.forEach((d) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td><b>${d.date}</b> <span style="font-size:0.68rem;color:var(--ink-faint)">(${d.trade_date})</span></td>
            <td style="color:var(--go);font-family:var(--mono)">${d.buyer_vol.toLocaleString("en-IN")}</td>
            <td style="color:var(--stop);font-family:var(--mono)">${d.seller_vol.toLocaleString("en-IN")}</td>
            <td style="color:${d.closing_cvd >= 0 ? "var(--go)" : "var(--stop)"};font-weight:750;font-family:var(--mono)">${d.closing_cvd >= 0 ? "+" : ""}${d.closing_cvd.toLocaleString("en-IN")}</td>
            <td style="color:${d.delta_pct >= 0 ? "var(--go)" : "var(--stop)"};font-weight:750;font-family:var(--mono)">${d.delta_pct >= 0 ? "+" : ""}${d.delta_pct}%</td>
          `;
          cvdBody.appendChild(tr);
        });
      }

      // Render OrderFlow Signals Stream
      state.sentinelSignals = data.signals || [];
      renderSentinelSignalsList("ALL");

      // Live Block Tape Table
      const tapeBody = document.getElementById("block-tape-body");
      if (tapeBody && data.block_tape) {
        tapeBody.innerHTML = "";
        data.block_tape.forEach((b) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${b.time}</td>
            <td style="color:${b.side === "CE" ? "var(--go)" : "var(--stop)"}"><b>${b.strike} ${b.side}</b></td>
            <td>${b.qty}</td>
            <td style="font-weight:750">₹${b.premium_lakhs}L</td>
            <td><span style="font-size:0.65rem;background:var(--surface-3);padding:1px 5px;border-radius:3px">${b.badge}</span></td>
          `;
          tapeBody.appendChild(tr);
        });
      }
    } catch (e) {
      console.error("Error loading sentinel tape:", e);
    }
  }

  window.switchSentinelTimeframe = function (tf) {
    sentinelState.timeframe = tf;
    sentinelState.initialFitDone = false;
    const sel = document.getElementById("sentinel-timeframe-select");
    if (sel) sel.value = tf;
    loadSentinelTape();
  };

  window.reseedSentinelFlow = async function (tf) {
    try {
      if (tf) {
        sentinelState.timeframe = tf;
        const sel = document.getElementById("sentinel-timeframe-select");
        if (sel) sel.value = tf;
      }
      const targetTf = tf || sentinelState.timeframe || "1m";
      showToast(`Syncing the live ${targetTf} feed...`, "⚡");
      const res = await fetch("/api/sentinel-tape/reseed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: state.symbol, timeframe: targetTf }),
      });
      const data = await res.json();
      showToast(data.message || "Flow reseeded successfully", "🔄");
      loadSentinelTape();
    } catch (e) {
      console.error("Error reseeding flow:", e);
    }
  };

  /**
   * Force-sync genuine 1m candles from the Dhan broker feed.
   * Falls back to the EOD snapshot when the market is closed.
   */
  window.syncLiveSentinelFeed = async function () {
    try {
      showToast("Syncing genuine candles from Dhan broker feed...", "🔄");
      const res = await fetch("/api/sentinel-tape/reseed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: state.symbol, action: "sync_live" }),
      });
      const data = await res.json();
      if (data.status === "offline") {
        showToast(data.message || "Broker feed offline — no live data available", "⚠️");
      } else {
        showToast(data.message || "Broker feed synced", "✅");
      }
      loadSentinelTape();
    } catch (e) {
      console.error("Error syncing live sentinel feed:", e);
      showToast("Sync error — check console", "❌");
    }
  };

  /**
   * Reload the last verified EOD session snapshot.
   */
  window.loadEODSentinelSnapshot = async function () {
    try {
      showToast("Loading verified EOD session snapshot...", "📥");
      const res = await fetch("/api/sentinel-tape/reseed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: state.symbol, action: "load_eod" }),
      });
      const data = await res.json();
      if (data.status === "offline") {
        showToast(data.message || "No EOD snapshot available", "⚠️");
      } else {
        showToast(data.message || "EOD snapshot loaded", "✅");
      }
      loadSentinelTape();
    } catch (e) {
      console.error("Error loading EOD sentinel snapshot:", e);
      showToast("Load error — check console", "❌");
    }
  };

  window.switchSentinelProfile = function (profile) {
    sentinelState.profile = profile;
    const profs = ["Full", "Pure", "CVD", "Trend"];
    profs.forEach((p) => {
      const btn = document.getElementById(`btn-prof-${p.toLowerCase()}`);
      if (btn) {
        if (p.toLowerCase() === profile.toLowerCase()) {
          btn.className = "btn btn-primary";
        } else {
          btn.className = "btn btn-secondary";
        }
      }
    });

    const titleEl = document.getElementById("sentinel-chart-title");
    const descMap = {
      "Full": "📊 ⚡ Full OrderFlow: Candlesticks, VWAP Envelopes, EMA 9/21 Ribbon, gamma strikes + CVD & Delta",
      "Pure": "📊 🎯 Pure Price: Clean Candlesticks Auto-Scaled Directly to Price Action with GEX Walls Only",
      "CVD": "📊 🌊 CVD Focus: Candlesticks + Session VWAP with Magnified Cumulative Volume Delta Subplot",
      "Trend": "📊 📈 Trend Follower: EMA 9 & 21 Ribbon, VWAP Envelopes, GEX Walls Paired with Delta Volume",
    };
    if (titleEl) {
      titleEl.textContent = descMap[profile] || descMap["Full"];
    }

    if (!state.sentinelChart) return;

    if (profile === "Pure") {
      // 🎯 Pure Price: ONLY candles + Call Wall + Put Wall. Clean uncluttered price action.
      if (state.candlestickSeries) state.candlestickSeries.applyOptions({ visible: true });
      if (state.vwapSeries) state.vwapSeries.applyOptions({ visible: false });
      if (state.up1Series) state.up1Series.applyOptions({ visible: false });
      if (state.dn1Series) state.dn1Series.applyOptions({ visible: false });
      if (state.ema9Series) state.ema9Series.applyOptions({ visible: false });
      if (state.ema21Series) state.ema21Series.applyOptions({ visible: false });
      if (state.cvdSeries) state.cvdSeries.applyOptions({ visible: false });
      if (state.deltaSeries) state.deltaSeries.applyOptions({ visible: false });
      state.sentinelChart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.1, bottom: 0.1 },
      });
    } else if (profile === "CVD") {
      // 🌊 CVD Focus: Candles + VWAP + GEX Walls + Prominent CVD Subplot. No EMAs or Delta.
      if (state.candlestickSeries) state.candlestickSeries.applyOptions({ visible: true });
      if (state.vwapSeries) state.vwapSeries.applyOptions({ visible: true });
      if (state.up1Series) state.up1Series.applyOptions({ visible: false });
      if (state.dn1Series) state.dn1Series.applyOptions({ visible: false });
      if (state.ema9Series) state.ema9Series.applyOptions({ visible: false });
      if (state.ema21Series) state.ema21Series.applyOptions({ visible: false });
      if (state.cvdSeries) state.cvdSeries.applyOptions({ visible: true, lineWidth: 3 });
      if (state.deltaSeries) state.deltaSeries.applyOptions({ visible: false });
      state.sentinelChart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.08, bottom: 0.38 },
      });
      state.sentinelChart.priceScale("cvd_scale").applyOptions({
        scaleMargins: { top: 0.65, bottom: 0.02 },
      });
    } else if (profile === "Trend") {
      // 📈 Trend Follower: Candles + EMA 9 & 21 Ribbon (Prominent) + VWAP & Envelopes + Delta Volume. No CVD.
      if (state.candlestickSeries) state.candlestickSeries.applyOptions({ visible: true });
      if (state.vwapSeries) state.vwapSeries.applyOptions({ visible: true });
      if (state.up1Series) state.up1Series.applyOptions({ visible: true });
      if (state.dn1Series) state.dn1Series.applyOptions({ visible: true });
      if (state.ema9Series) state.ema9Series.applyOptions({ visible: true, lineWidth: 2 });
      if (state.ema21Series) state.ema21Series.applyOptions({ visible: true, lineWidth: 2 });
      if (state.cvdSeries) state.cvdSeries.applyOptions({ visible: false });
      if (state.deltaSeries) state.deltaSeries.applyOptions({ visible: true });
      state.sentinelChart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.10, bottom: 0.25 },
      });
      state.sentinelChart.priceScale("delta_scale").applyOptions({
        scaleMargins: { top: 0.80, bottom: 0 },
      });
    } else {
      // ⚡ Full OrderFlow: Everything active (Candles, VWAP + Envelopes, EMA 9/21 Ribbon, CVD, Delta, GEX Walls)
      if (state.candlestickSeries) state.candlestickSeries.applyOptions({ visible: true });
      if (state.vwapSeries) state.vwapSeries.applyOptions({ visible: true });
      if (state.up1Series) state.up1Series.applyOptions({ visible: true });
      if (state.dn1Series) state.dn1Series.applyOptions({ visible: true });
      if (state.ema9Series) state.ema9Series.applyOptions({ visible: true, lineWidth: 1.5 });
      if (state.ema21Series) state.ema21Series.applyOptions({ visible: true, lineWidth: 1.5 });
      if (state.cvdSeries) state.cvdSeries.applyOptions({ visible: true, lineWidth: 2 });
      if (state.deltaSeries) state.deltaSeries.applyOptions({ visible: true });
      state.sentinelChart.priceScale("right").applyOptions({
        scaleMargins: { top: 0.08, bottom: 0.38 },
      });
      state.sentinelChart.priceScale("cvd_scale").applyOptions({
        scaleMargins: { top: 0.68, bottom: 0.02 },
      });
      state.sentinelChart.priceScale("delta_scale").applyOptions({
        scaleMargins: { top: 0.86, bottom: 0 },
      });
    }
  };

  window.setChartHeight = function (h) {
    sentinelState.chartHeight = h;
    const mount = document.getElementById("sentinel-chart-mount");
    if (mount) {
      mount.style.height = `${h}px`;
    }
    if (state.sentinelChart) {
      state.sentinelChart.applyOptions({ height: h });
    }
  };

  window.zoomSentinelBoth = function (factor) {
    if (!state.sentinelChart) return;
    const ts = state.sentinelChart.timeScale();
    const curSpacing = (ts.options && ts.options().barSpacing) || 9;
    ts.applyOptions({ barSpacing: Math.max(2, Math.min(60, curSpacing * factor)) });

    const ps = state.sentinelChart.priceScale("right");
    const margins = (ps.options && ps.options().scaleMargins) || { top: 0.1, bottom: 0.35 };
    ps.applyOptions({
      autoScale: true,
      scaleMargins: {
        top: Math.max(0.02, Math.min(0.40, margins.top + (1 - factor) * 0.06)),
        bottom: Math.max(0.05, Math.min(0.65, margins.bottom + (1 - factor) * 0.06)),
      },
    });
  };

  window.resetSentinelPriceScale = function () {
    if (!state.sentinelChart) return;
    state.sentinelChart.priceScale("right").applyOptions({
      autoScale: true,
      scaleMargins: { top: 0.08, bottom: 0.35 },
    });
  };

  window.fitSentinelTimeScale = function () {
    if (!state.sentinelChart) return;
    try {
      state.sentinelChart.priceScale("right").applyOptions({ autoScale: true });
      const totalBars = sentinelState.totalBars || 65;
      const visibleBars = Math.min(totalBars, 65);
      state.sentinelChart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, totalBars - visibleBars),
        to: totalBars + 5,
      });
    } catch (e) {
      state.sentinelChart.timeScale().fitContent();
    }
  };

  window.resetSentinelAxes = function () {
    if (!state.sentinelChart) return;
    state.sentinelChart.timeScale().applyOptions({ barSpacing: 12, rightOffset: 6 });
    window.fitSentinelTimeScale();
    window.resetSentinelPriceScale();
  };

  window.toggleSentinelFullscreen = function () {
    const el = document.getElementById("sentinel-chart-mount");
    if (!el) return;
    if (!document.fullscreenElement) {
      if (el.requestFullscreen) el.requestFullscreen();
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
    }
  };

  function initChartDragHandle() {
    const dragHandle = document.getElementById("sentinel-chart-drag");
    const mount = document.getElementById("sentinel-chart-mount");
    if (!dragHandle || !mount) return;

    let isDragging = false;
    let startY = 0;
    let startH = 0;

    dragHandle.addEventListener("mousedown", (e) => {
      isDragging = true;
      startY = e.clientY;
      startH = mount.clientHeight;
      document.body.style.cursor = "ns-resize";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const delta = e.clientY - startY;
      const newH = Math.max(320, Math.min(1000, startH + delta));
      window.setChartHeight(newH);
    });

    window.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        document.body.style.cursor = "";
      }
    });
  }

  window.openPopoutChart = function () {
    const tf = sentinelState.timeframe || "1m";
    const sym = state.symbol || "NIFTY";
    const prof = sentinelState.profile || "Full";
    const url = `/chart/sentinel?symbol=${encodeURIComponent(sym)}&timeframe=${encodeURIComponent(tf)}&profile=${encodeURIComponent(prof)}`;
    window.open(url, "_blank", "width=1320,height=840,menubar=no,toolbar=no,location=no,status=no,resizable=yes");
  };

  // -------------------------------------------------------------------------
  // Sentinel 10-Day CVD SVG Charts & Stream Renderers
  // -------------------------------------------------------------------------
  function renderCvdBarChart(dailyRecords) {
    const container = document.getElementById("sentinel-cvd-barchart-container");
    if (!container || !dailyRecords || dailyRecords.length === 0) return;

    const width = container.clientWidth || 800;
    const height = 260;
    const padding = { top: 30, right: 30, bottom: 40, left: 60 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const maxAbsCvd = Math.max(...dailyRecords.map((r) => Math.abs(r.closing_cvd)), 10000) * 1.25;
    const zeroY = padding.top + chartH / 2;
    const barWidth = Math.min(42, (chartW / dailyRecords.length) * 0.65);
    const stepX = chartW / dailyRecords.length;

    let barsHtml = "";
    dailyRecords.forEach((r, idx) => {
      const cx = padding.left + (idx + 0.5) * stepX;
      const cvd = r.closing_cvd;
      const isPos = cvd >= 0;
      const barH = (Math.abs(cvd) / maxAbsCvd) * (chartH / 2);
      const y = isPos ? zeroY - barH : zeroY;
      const clr = isPos ? "#22c55e" : "#ef4444";
      const lblY = isPos ? y - 8 : y + barH + 14;
      const valText = `${isPos ? "+" : ""}${(cvd / 1000).toFixed(1)}K`;

      barsHtml += `
        <rect x="${cx - barWidth / 2}" y="${y}" width="${barWidth}" height="${barH}" rx="3" fill="${clr}" opacity="0.85">
          <title>${r.trade_date}: ${valText}</title>
        </rect>
        <text x="${cx}" y="${lblY}" text-anchor="middle" font-size="11" font-weight="700" fill="${clr}" font-family="var(--mono)">${valText}</text>
        <text x="${cx}" y="${height - 12}" text-anchor="middle" font-size="11" fill="var(--ink-mute)">${r.date}</text>
      `;
    });

    container.innerHTML = `
      <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow:visible">
        <!-- Zero Line -->
        <line x1="${padding.left}" y1="${zeroY}" x2="${width - padding.right}" y2="${zeroY}" stroke="rgba(148, 163, 184, 0.3)" stroke-dasharray="4,4" />
        <text x="${padding.left - 10}" y="${zeroY + 4}" text-anchor="end" font-size="10" fill="var(--ink-faint)" font-family="var(--mono)">0</text>
        ${barsHtml}
      </svg>
    `;
  }

  function renderVolBreakdownChart(dailyRecords) {
    const container = document.getElementById("sentinel-vol-breakdown-container");
    if (!container || !dailyRecords || dailyRecords.length === 0) return;

    const width = container.clientWidth || 800;
    const height = 260;
    const padding = { top: 30, right: 30, bottom: 40, left: 60 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const maxVol = Math.max(...dailyRecords.map((r) => Math.max(r.buyer_vol, r.seller_vol)), 50000) * 1.2;
    const barWidth = Math.min(18, (chartW / dailyRecords.length) * 0.35);
    const stepX = chartW / dailyRecords.length;

    let barsHtml = "";
    dailyRecords.forEach((r, idx) => {
      const cx = padding.left + (idx + 0.5) * stepX;
      const bH = (r.buyer_vol / maxVol) * chartH;
      const sH = (r.seller_vol / maxVol) * chartH;
      const bY = padding.top + chartH - bH;
      const sY = padding.top + chartH - sH;

      barsHtml += `
        <!-- Buyer Bar (Green) -->
        <rect x="${cx - barWidth - 2}" y="${bY}" width="${barWidth}" height="${bH}" rx="3" fill="#22c55e" opacity="0.85">
          <title>${r.trade_date} Buy: ${(r.buyer_vol / 1000).toFixed(0)}K</title>
        </rect>
        <!-- Seller Bar (Red) -->
        <rect x="${cx + 2}" y="${sY}" width="${barWidth}" height="${sH}" rx="3" fill="#ef4444" opacity="0.85">
          <title>${r.trade_date} Sell: ${(r.seller_vol / 1000).toFixed(0)}K</title>
        </rect>
        <text x="${cx}" y="${height - 12}" text-anchor="middle" font-size="11" fill="var(--ink-mute)">${r.date}</text>
      `;
    });

    container.innerHTML = `
      <div style="display:flex;justify-content:flex-end;gap:14px;margin-bottom:6px;font-size:0.74rem">
        <span style="display:flex;align-items:center;gap:5px"><span style="display:inline-block;width:10px;height:10px;background:#22c55e;border-radius:2px"></span> Buyer Volume</span>
        <span style="display:flex;align-items:center;gap:5px"><span style="display:inline-block;width:10px;height:10px;background:#ef4444;border-radius:2px"></span> Seller Volume</span>
      </div>
      <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow:visible">
        <line x1="${padding.left}" y1="${padding.top + chartH}" x2="${width - padding.right}" y2="${padding.top + chartH}" stroke="var(--line)" />
        ${barsHtml}
      </svg>
    `;
  }

  function renderSentinelSignalsList(category) {
    const container = document.getElementById("sentinel-signals-stream-container");
    if (!container || !state.sentinelSignals) return;

    let filtered = state.sentinelSignals;
    if (category === "HIGH_CONF") {
      filtered = filtered.filter((s) => Number(s.score) >= 70);
    } else if (category && category !== "ALL") {
      filtered = filtered.filter((s) => s.category.toUpperCase().includes(category) || s.type.toUpperCase().includes(category));
    }

    container.innerHTML = "";
    if (filtered.length === 0) {
      container.innerHTML = '<div style="color:var(--ink-mute);font-size:0.8rem;padding:12px">No signals match filter.</div>';
      return;
    }

    filtered.forEach((s) => {
      const card = document.createElement("div");
      card.className = "signal-card";
      const scoreClr = s.score >= 80 ? "var(--go)" : s.score >= 65 ? "var(--spark)" : "var(--warn)";
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div style="display:flex;align-items:center;gap:6px">
            <span style="font-family:var(--mono);font-size:0.7rem;color:var(--ink-faint)">${s.time}</span>
            <b style="font-size:0.8rem;color:var(--spark)">${s.type}</b>
            <span style="font-size:0.65rem;background:var(--surface-3);padding:1px 6px;border-radius:3px">${s.category}</span>
          </div>
          <span style="font-size:0.72rem;font-weight:800;color:${scoreClr}">${s.score}/100</span>
        </div>
        <div style="font-size:0.76rem;color:var(--ink);line-height:1.4">${s.insight_text}</div>
        <div style="display:flex;justify-content:space-between;font-size:0.68rem;color:var(--ink-mute);font-family:var(--mono);border-top:1px dashed var(--line);padding-top:4px">
          <span>Delta: <b style="color:${s.delta >= 0 ? "var(--go)" : "var(--stop)"}">${s.delta >= 0 ? "+" : ""}${s.delta}</b></span>
          <span>RVOL: <b>${s.rvol_tod}x</b></span>
          <span>Inval: <b>₹${Number(s.invalidation_level || 0).toLocaleString("en-IN")}</b></span>
          <span>${s.outcome ? (s.outcome.target_hit ? "Target" : s.outcome.stopped_out ? "Stopped" : `MFE ${s.outcome.mfe} / MAE ${s.outcome.mae}`) : "Open"}</span>
        </div>
      `;
      container.appendChild(card);
    });
  }

  window.switchSentinelSubtab = function (subtab) {
    const tabs = ["chart", "breakdown", "ledger"];
    tabs.forEach((t) => {
      const pane = document.getElementById(`subtab-content-${t}`);
      const btn = document.getElementById(`btn-subtab-${t === "chart" ? "cvd-chart" : t === "breakdown" ? "vol-breakdown" : "daily-ledger"}`);
      if (pane) pane.style.display = t === subtab ? "block" : "none";
      if (btn) {
        if (t === subtab) btn.classList.add("active");
        else btn.classList.remove("active");
      }
    });
  };

  window.filterSentinelSignals = function (category) {
    const buttons = document.querySelectorAll("#tab-sentinel .news-filters button");
    buttons.forEach((b) => {
      if (b.textContent.trim().toUpperCase() === category.toUpperCase()) {
        b.className = "btn btn-primary";
      } else {
        b.className = "btn btn-secondary";
      }
    });
    renderSentinelSignalsList(category);
  };

  // Signal Efficiency State & Controls
  const effState = {
    period: "daily",
    scope: "symbol",
    subtab: "timeline",
  };

  window.switchEffPeriod = function (val) {
    effState.period = val;
    const lbl = document.getElementById("eff-timeline-period-label");
    if (lbl) lbl.textContent = val === "weekly" ? "Weekly" : "Daily";
    loadSignalEfficiency();
  };

  window.switchEffScope = function (val) {
    effState.scope = val;
    loadSignalEfficiency();
  };

  window.switchEffSubtab = function (subtab) {
    effState.subtab = subtab;
    const subtabs = ["timeline", "breakdowns", "expectancy", "calib"];
    subtabs.forEach((st) => {
      const btn = document.getElementById(`btn-eff-tab-${st}`);
      const pane = document.getElementById(`eff-content-${st}`);
      if (btn) {
        if (st === subtab) btn.classList.add("active");
        else btn.classList.remove("active");
      }
      if (pane) {
        pane.style.display = st === subtab ? "block" : "none";
      }
    });
  };

  async function loadSignalEfficiency() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/signal-efficiency?symbol=${requestedSym}&period_type=${effState.period}&scope=${effState.scope}`);
      const data = await res.json();
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) return;

      // Top 5-KPI Strip
      const kTotal = document.getElementById("eff-kpi-total");
      if (kTotal) kTotal.textContent = data.kpis.total_signals;
      const kHit = document.getElementById("eff-kpi-hit-rate");
      if (kHit) kHit.textContent = data.kpis.hit_rate;
      const kStop = document.getElementById("eff-kpi-stop-rate");
      if (kStop) kStop.textContent = data.kpis.stop_rate;
      const kExc = document.getElementById("eff-kpi-excursions");
      if (kExc) kExc.textContent = data.kpis.avg_excursions;
      const kEdge = document.getElementById("eff-kpi-edge-ratio");
      if (kEdge) kEdge.textContent = data.kpis.edge_ratio;

      // Subtab 1: Timeline Table
      const tbBody = document.getElementById("eff-timeline-body");
      if (tbBody && data.timeline) {
        tbBody.innerHTML = "";
        data.timeline.forEach((row) => {
          const tr = document.createElement("tr");
          const hitClr = row.hit_rate_pct >= 70 ? "var(--go)" : "var(--spark)";
          const edgeClr = row.edge_ratio >= 2.0 ? "var(--go)" : (row.edge_ratio >= 1.5 ? "var(--spark)" : "var(--warn)");
          tr.innerHTML = `
            <td style="font-weight:750;font-family:var(--mono)">${row.period}</td>
            <td>${row.total_signals}</td>
            <td style="color:var(--go);font-weight:700">${row.target_hits}</td>
            <td style="color:${hitClr};font-weight:750">${row.hit_rate_pct.toFixed(1)}%</td>
            <td style="color:var(--stop);font-weight:750">${row.stop_rate_pct.toFixed(1)}%</td>
            <td style="color:var(--go);font-family:var(--mono)">+${row.avg_mfe_pts.toFixed(1)}</td>
            <td style="color:var(--stop);font-family:var(--mono)">-${row.avg_mae_pts.toFixed(1)}</td>
            <td style="color:${edgeClr};font-weight:750;font-family:var(--mono)">${row.edge_ratio.toFixed(2)}×</td>
            <td style="color:${(row.avg_ret_5m || 0) >= 0 ? "var(--go)" : "var(--stop)"};font-family:var(--mono)">${(row.avg_ret_5m || 0) >= 0 ? "+" : ""}${(row.avg_ret_5m || 0).toFixed(1)}</td>
            <td style="color:${(row.avg_ret_15m || 0) >= 0 ? "var(--go)" : "var(--stop)"};font-family:var(--mono)">${(row.avg_ret_15m || 0) >= 0 ? "+" : ""}${(row.avg_ret_15m || 0).toFixed(1)}</td>
          `;
          tbBody.appendChild(tr);
        });
      }

      // Subtab 2: Breakdowns (By Type & By Alignment)
      const typeBody = document.getElementById("eff-by-type-body");
      if (typeBody && data.by_type) {
        typeBody.innerHTML = "";
        data.by_type.forEach((b) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td style="font-weight:750">${b.label}</td>
            <td>${b.total_signals}</td>
            <td style="color:var(--go);font-weight:750">${b.hit_rate_pct.toFixed(1)}%</td>
            <td style="color:var(--go)">+${b.avg_mfe_pts.toFixed(1)}</td>
            <td style="color:var(--stop)">-${b.avg_mae_pts.toFixed(1)}</td>
            <td style="font-weight:750;color:var(--spark)">${b.edge_ratio.toFixed(2)}×</td>
          `;
          typeBody.appendChild(tr);
        });
      }

      const alignBody = document.getElementById("eff-by-align-body");
      if (alignBody && data.by_alignment) {
        alignBody.innerHTML = "";
        data.by_alignment.forEach((b) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td style="font-weight:750">${b.label}</td>
            <td>${b.total_signals}</td>
            <td style="color:var(--go);font-weight:750">${b.hit_rate_pct.toFixed(1)}%</td>
            <td style="color:var(--go)">+${b.avg_mfe_pts.toFixed(1)}</td>
            <td style="color:var(--stop)">-${b.avg_mae_pts.toFixed(1)}</td>
            <td style="font-weight:750;color:var(--spark)">${b.edge_ratio.toFixed(2)}×</td>
          `;
          alignBody.appendChild(tr);
        });
      }

      // Subtab 3: Return Expectancy Profile Bars
      const expBars = document.getElementById("eff-expectancy-bars");
      if (expBars && data.expectancy_curve) {
        expBars.innerHTML = "";
        const maxVal = Math.max(...data.expectancy_curve.map((c) => Math.abs(c.expected_return)), 25);
        data.expectancy_curve.forEach((c) => {
          const pct = Math.min(100, Math.round((Math.abs(c.expected_return) / maxVal) * 100));
          const isPos = c.expected_return >= 0;
          const barClr = isPos ? "var(--go)" : "var(--stop)";
          const rowDiv = document.createElement("div");
          rowDiv.style = "display:grid;grid-template-columns:80px 1fr 100px;align-items:center;gap:12px;font-size:0.76rem";
          rowDiv.innerHTML = `
            <div style="font-weight:750;color:var(--ink-mute);font-family:var(--mono)">${c.horizon}</div>
            <div style="background:var(--surface-3);border-radius:4px;height:20px;position:relative;overflow:hidden">
              <div style="background:${barClr};width:${pct}%;height:100%;border-radius:4px;transition:width 0.4s ease"></div>
            </div>
            <div style="font-weight:800;font-family:var(--mono);color:${barClr};text-align:right">${isPos ? "+" : ""}${c.expected_return.toFixed(1)} pts</div>
          `;
          expBars.appendChild(rowDiv);
        });
      }

      // Subtab 4: Model Calibration & Options Expectancy
      if (data.calibration) {
        const cal = data.calibration;
        const cEmp = document.getElementById("calib-kpi-empirical");
        if (cEmp) cEmp.textContent = `${cal.empirical_hit_rate.toFixed(1)}%`;
        const cTheo = document.getElementById("calib-kpi-theo");
        if (cTheo) cTheo.textContent = `${cal.theoretical_p_touch.toFixed(1)}%`;
        const cAlpha = document.getElementById("calib-kpi-alpha");
        if (cAlpha) cAlpha.textContent = `${cal.calibration_alpha >= 0 ? "+" : ""}${cal.calibration_alpha.toFixed(1)}%`;
        const cAtm = document.getElementById("calib-kpi-atm");
        if (cAtm) cAtm.textContent = `${cal.atm_strike.toLocaleString("en-IN")} CE`;
        const cEntry = document.getElementById("calib-kpi-entry");
        if (cEntry) cEntry.textContent = `₹${cal.atm_call_entry.toFixed(1)} Entry`;

        const calTypeBody = document.getElementById("calib-by-type-body");
        if (calTypeBody && cal.by_type_calibration) {
          calTypeBody.innerHTML = "";
          cal.by_type_calibration.forEach((r) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td style="font-weight:750">${r.label}</td>
              <td>${r.count}</td>
              <td style="color:var(--go);font-weight:700">${r.empirical_hit}</td>
              <td style="color:var(--spark)">${r.theoretical_p}</td>
              <td style="color:var(--go);font-weight:750">${r.alpha}</td>
            `;
            calTypeBody.appendChild(tr);
          });
        }

        const calAlignBody = document.getElementById("calib-by-align-body");
        if (calAlignBody && cal.by_alignment_calibration) {
          calAlignBody.innerHTML = "";
          cal.by_alignment_calibration.forEach((r) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td style="font-weight:750">${r.label}</td>
              <td>${r.count}</td>
              <td style="color:var(--go);font-weight:700">${r.empirical_hit}</td>
              <td style="color:var(--spark)">${r.theoretical_p}</td>
              <td style="color:var(--go);font-weight:750">${r.alpha}</td>
            `;
            calAlignBody.appendChild(tr);
          });
        }
      }
    } catch (e) {
      console.error("Error loading signal efficiency:", e);
    }
  }

  // Market Intelligence Filter
  window.filterMarketNews = function(cat) {
    state.selectedNewsCat = cat || 'ALL';
    const pills = document.querySelectorAll('#news-category-pills .news-cat-pill');
    pills.forEach(p => {
      if (p.getAttribute('data-cat') === state.selectedNewsCat) {
        p.classList.add('active');
      } else {
        p.classList.remove('active');
      }
    });
    renderMarketNewsList();
  };

  function renderMarketNewsList() {
    const newsContainer = document.getElementById("news-feed-container");
    if (!newsContainer) return;
    const headlines = state.intelHeadlines || [];
    const cat = state.selectedNewsCat || 'ALL';
    const filtered = (cat === 'ALL') ? headlines : headlines.filter(h => h.category === cat);

    if (!filtered || filtered.length === 0) {
      newsContainer.innerHTML = `
        <div style="padding:24px 16px;text-align:center;color:var(--ink-mute);font-size:0.82rem">
          No headlines found in selected category. Tape is calm.
        </div>
      `;
      return;
    }

    newsContainer.innerHTML = filtered.map(h => {
      const sentCls = h.sentiment === "BULLISH" ? "bull" : (h.sentiment === "BEARISH" ? "bear" : "neutral");
      const freshBadge = h.is_fresh ? '<span style="color:var(--spark);font-weight:700;margin-left:5px">⚡ Fresh</span>' : '';
      const impactBadge = (h.impact && h.impact > 0) ? `<span style="background:var(--surface-3);color:var(--ink-2);padding:1px 6px;border-radius:3px;font-size:0.66rem;font-weight:700">Impact ${h.impact}</span>` : '';
      const pubTime = h.pub_time_ist ? `${h.pub_time_ist} (${h.age_min || 0}m ago)` : '';
      
      return `
        <div class="news-card">
          <div class="news-meta">
            <div style="display:flex;align-items:center;gap:6px">
              <span class="news-chip ${sentCls}">${h.sentiment}</span>
              <span style="color:var(--ink-mute);font-family:var(--mono)">${pubTime}</span>
              ${freshBadge}
            </div>
            <div style="display:flex;align-items:center;gap:6px">
              <span style="font-size:0.68rem;color:var(--ink-mute)">${h.category || ''}</span>
              ${impactBadge}
            </div>
          </div>
          <div class="news-title">${h.title}</div>
        </div>
      `;
    }).join('');
  }

  async function loadMarketIntel() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/market-intel?symbol=${requestedSym}`);
      const data = await res.json();
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) return;

      // Macro Verdict & Narrative
      const verdictEl = document.getElementById("intel-verdict");
      if (verdictEl && data.macro_verdict) verdictEl.textContent = data.macro_verdict;

      const narrEl = document.getElementById("intel-narrative");
      if (narrEl && data.executive_narrative) narrEl.textContent = data.executive_narrative;

      // Risk Chip
      const riskEl = document.getElementById("intel-risk-chip");
      if (riskEl) {
        riskEl.className = `sentinel-chip ${data.risk_chip_cls || 'go'}`;
        riskEl.textContent = data.risk_label || '🟢 LOW EXOGENOUS RISK';
      }

      // 4-KPI Grid
      if (data.kpis) {
        if (data.kpis.sector_breadth) {
          const valEl = document.getElementById("mi-kpi-breadth-val");
          const subEl = document.getElementById("mi-kpi-breadth-sub");
          if (valEl) {
            valEl.textContent = data.kpis.sector_breadth.ratio_str;
            valEl.style.color = data.kpis.sector_breadth.color || 'var(--go)';
          }
          if (subEl) subEl.textContent = data.kpis.sector_breadth.sub;
        }

        if (data.kpis.weighted_contrib) {
          const valEl = document.getElementById("mi-kpi-contrib-val");
          const subEl = document.getElementById("mi-kpi-contrib-sub");
          if (valEl) {
            valEl.textContent = data.kpis.weighted_contrib.val_str;
            valEl.style.color = data.kpis.weighted_contrib.color || 'var(--go)';
          }
          if (subEl) subEl.textContent = data.kpis.weighted_contrib.sub;
        }

        if (data.kpis.news_sentiment) {
          const valEl = document.getElementById("mi-kpi-sent-val");
          const subEl = document.getElementById("mi-kpi-sent-sub");
          if (valEl) {
            valEl.textContent = data.kpis.news_sentiment.val_str;
            valEl.style.color = data.kpis.news_sentiment.color || 'var(--ink-mute)';
          }
          if (subEl) subEl.textContent = data.kpis.news_sentiment.sub;
        }

        if (data.kpis.alignment_with_index) {
          const valEl = document.getElementById("mi-kpi-align-val");
          const subEl = document.getElementById("mi-kpi-align-sub");
          if (valEl) {
            valEl.textContent = data.kpis.alignment_with_index.val_str;
            valEl.style.color = data.kpis.alignment_with_index.color || 'var(--spark)';
          }
          if (subEl) subEl.textContent = data.kpis.alignment_with_index.sub;
        }
      }

      // Actionable Trader Takeaways
      const takeawaysContainer = document.getElementById("intel-takeaways-container");
      if (takeawaysContainer && Array.isArray(data.actionable_points)) {
        if (data.actionable_points.length === 0) {
          takeawaysContainer.innerHTML = '<div style="font-size:0.80rem;color:var(--ink-mute)">• Neutral session dynamics; standard mean-reversion trading rules apply.</div>';
        } else {
          takeawaysContainer.innerHTML = data.actionable_points.map(pt => 
            `<div style="font-size:0.80rem;color:var(--ink-2);line-height:1.5">• ${pt}</div>`
          ).join('');
        }
      }

      // Sector Performance Leaderboard
      const secContainer = document.getElementById("sector-table-container");
      if (secContainer && Array.isArray(data.sectors)) {
        secContainer.innerHTML = data.sectors.map(s => {
          const pctVal = s.pct;
          let pctStr = "—";
          let barPct = 0.0;
          let valColor = "var(--ink-mute)";
          let isPos = true;

          if (pctVal !== null && pctVal !== undefined) {
            pctStr = (pctVal >= 0 ? "+" : "") + pctVal.toFixed(2) + "%";
            valColor = pctVal > 0 ? "var(--go)" : (pctVal < 0 ? "var(--stop)" : "var(--ink-mute)");
            isPos = pctVal >= 0;
            barPct = Math.min(50.0, Math.abs(pctVal) * 15.0);
          }

          const wtStr = (s.weight !== null && s.weight !== undefined) ? `~${Math.round(s.weight)}%` : "";
          const pxStr = s.last ? `₹${s.last.toLocaleString('en-IN', {maximumFractionDigits:1})}` : "";
          const barHtml = isPos 
            ? `<div class="sector-bar-fill pos" style="width:${barPct.toFixed(1)}%"></div>` 
            : `<div class="sector-bar-fill neg" style="width:${barPct.toFixed(1)}%"></div>`;

          return `
            <div class="sector-row">
              <div><b style="color:var(--ink)">${s.label}</b><span style="font-size:0.66rem;color:var(--ink-mute);margin-left:4px">${wtStr}</span></div>
              <div style="font-size:0.72rem;color:var(--ink-mute);font-family:var(--mono)">${pxStr}</div>
              <div class="sector-bar-track">${barHtml}</div>
              <div style="font-weight:700;color:${valColor};text-align:right;font-family:var(--mono)">${pctStr}</div>
            </div>
          `;
        }).join('');
      }

      // Broad Market Context
      const broadContainer = document.getElementById("broad-market-container");
      if (broadContainer && Array.isArray(data.broad_indices)) {
        broadContainer.innerHTML = data.broad_indices.map(b => {
          const bPct = b.pct;
          const bStr = (bPct !== null && bPct !== undefined) ? ((bPct >= 0 ? "+" : "") + bPct.toFixed(2) + "%") : "—";
          const bColor = (bPct > 0) ? "var(--go)" : ((bPct < 0) ? "var(--stop)" : "var(--ink-mute)");
          const bPx = b.last ? `₹${b.last.toLocaleString('en-IN', {maximumFractionDigits:1})}` : "";

          return `
            <div class="sector-row" style="background:var(--ground)">
              <div><b style="color:var(--ink)">${b.label}</b></div>
              <div style="font-size:0.72rem;color:var(--ink-mute);font-family:var(--mono)">${bPx}</div>
              <div style="font-size:0.70rem;color:var(--ink-mute)">Benchmark</div>
              <div style="font-weight:700;color:${bColor};text-align:right;font-family:var(--mono)">${bStr}</div>
            </div>
          `;
        }).join('');
      }

      // Sector Source & Weights Metadata
      const srcEl = document.getElementById("sector-source-label");
      if (srcEl && data.sector_source) srcEl.textContent = `Source: ${data.sector_source}`;
      const wtEl = document.getElementById("sector-weights-label");
      if (wtEl && data.weights_as_of) wtEl.textContent = `Weights as of: ${formatValidStamp(data.weights_as_of)}`;

      // News Stream Headlines & Categories
      state.intelHeadlines = data.news_headlines || [];
      const catContainer = document.getElementById("news-category-pills");
      if (catContainer && Array.isArray(data.news_categories)) {
        catContainer.innerHTML = data.news_categories.map(c => {
          const isActive = (state.selectedNewsCat || 'ALL') === c;
          return `<button class="news-cat-pill ${isActive ? 'active' : ''}" data-cat="${c}" onclick="window.filterMarketNews('${c}')">${c}</button>`;
        }).join('');
      }
      renderMarketNewsList();

    } catch (e) {
      console.error("Error loading market intel:", e);
    }
  }

  async function loadOptionsAnalytics() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/options-analytics?symbol=${requestedSym}&window=${state.window}`);
      const data = await res.json();
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) return;

      // Top 6-Card KPI Strip
      if (data.kpis) {
        if (data.kpis.atm_iv) {
          const el = document.getElementById("noa-kpi-iv");
          if (el) el.textContent = data.kpis.atm_iv.val;
          const sub = document.getElementById("noa-kpi-skew");
          if (sub) sub.textContent = data.kpis.atm_iv.sub;
        }
        if (data.kpis.pcr) {
          const el = document.getElementById("noa-kpi-pcr");
          if (el) el.textContent = data.kpis.pcr.val;
          const sub = document.getElementById("noa-kpi-pcr-regime");
          if (sub) {
            sub.textContent = data.kpis.pcr.sub;
            sub.style.color = "var(--ink-mute)";
          }
        }
        if (data.kpis.max_pain) {
          const el = document.getElementById("noa-kpi-mp");
          if (el) el.textContent = data.kpis.max_pain.val;
          const sub = document.getElementById("noa-kpi-pin");
          if (sub) sub.textContent = data.kpis.max_pain.sub;
        }
        if (data.kpis.iv_rank) {
          const el = document.getElementById("noa-kpi-ivr");
          if (el) el.textContent = data.kpis.iv_rank.val;
          const sub = document.getElementById("noa-kpi-ivr-sub");
          if (sub && data.kpis.iv_rank.sub) sub.textContent = data.kpis.iv_rank.sub;
        }
        if (data.kpis.india_vix) {
          const el = document.getElementById("noa-kpi-vix");
          if (el) el.textContent = data.kpis.india_vix.val;
          const sub = document.getElementById("noa-kpi-vix-sub");
          if (sub && data.kpis.india_vix.sub) sub.textContent = data.kpis.india_vix.sub;
        }
        if (data.kpis.bias_gauge) {
          const el = document.getElementById("noa-kpi-bias");
          if (el) {
            el.textContent = data.kpis.bias_gauge.val;
            el.style.color = parseFloat(data.kpis.bias_gauge.val) >= 0 ? "var(--go)" : "var(--stop)";
          }
          const sub = document.getElementById("noa-kpi-bias-sub");
          if (sub && data.kpis.bias_gauge.sub) sub.textContent = data.kpis.bias_gauge.sub;
        }
      }

      if (data.expected_move) {
        document.getElementById("exp-move-low").textContent = `Lower 1σ: ₹${data.expected_move.low.toLocaleString("en-IN")}`;
        document.getElementById("exp-move-center").textContent = `Spot: ₹${data.spot.toLocaleString("en-IN")}`;
        document.getElementById("exp-move-high").textContent = `Upper 1σ: ₹${data.expected_move.high.toLocaleString("en-IN")}`;
        const straddleEl = document.getElementById("exp-move-straddle");
        if (straddleEl) {
          const src = data.expected_move.straddle_source === "mid" ? " (mid)" : "";
          straddleEl.textContent = data.expected_move.straddle == null
            ? "ATM straddle: no paired quotes"
            : `ATM straddle${src}: ₹${data.expected_move.straddle}`;
        }
      }
      const biasBody = document.getElementById("bias-rows-body");
      if (biasBody) {
        biasBody.innerHTML = (data.bias_rows || []).map((row) => `
          <tr>
            <td>${row.key}</td>
            <td>${row.label}${row.detail ? `<div style="color:var(--ink-mute);font-size:0.72rem">${row.detail}</div>` : ""}</td>
            <td style="font-family:var(--mono)">${Number(row.contribution).toFixed(2)}</td>
          </tr>`).join("") || '<tr><td colspan="3">No bias rows on this read.</td></tr>';
      }
      const oiHost = document.getElementById("oi-bars");
      if (oiHost && data.buildup_matrix) {
        const maxOi = Math.max(1, ...data.buildup_matrix.map((b) => Math.max(b.call_oi || 0, b.put_oi || 0)));
        oiHost.innerHTML = data.buildup_matrix.map((b) => `
          <div style="display:grid;grid-template-columns:72px 1fr 1fr;gap:6px;align-items:center;margin-bottom:4px;font-size:0.72rem">
            <span style="font-family:var(--mono)">${Number(b.strike).toLocaleString("en-IN")}</span>
            <div style="background:var(--surface-3);height:8px"><div style="width:${(b.call_oi / maxOi) * 100}%;height:8px;background:var(--stop)"></div></div>
            <div style="background:var(--surface-3);height:8px"><div style="width:${(b.put_oi / maxOi) * 100}%;height:8px;background:var(--go)"></div></div>
          </div>`).join("") + '<div style="font-size:0.7rem;color:var(--ink-mute)">Left bar is call open interest. Right bar is put open interest.</div>';
      }

      const tbody = document.getElementById("buildup-matrix-body");
      if (tbody && data.buildup_matrix) {
        tbody.innerHTML = "";
        data.buildup_matrix.forEach((b) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td style="font-weight:750">₹${b.strike.toLocaleString("en-IN")}</td>
            <td>${b.call_oi.toLocaleString("en-IN")}</td>
            <td style="font-family:var(--mono)">${b.call_doi >= 0 ? "+" : ""}${b.call_doi.toLocaleString("en-IN")}</td>
            <td>${b.put_oi.toLocaleString("en-IN")}</td>
            <td style="font-family:var(--mono)">${b.put_doi >= 0 ? "+" : ""}${b.put_doi.toLocaleString("en-IN")}</td>
            <td style="font-weight:750">${b.pcr}</td>
          `;
          tbody.appendChild(tr);
        });
      }

      // Populate Intraday Shift Events Log
      const shiftContainer = document.getElementById("noa-shift-events-container");
      if (shiftContainer) {
        const events = data.location_alerts || data.shift_events || [];
        shiftContainer.innerHTML = "";
        if (!events.length) {
          shiftContainer.innerHTML = '<div style="color:var(--ink-mute);font-size:0.8rem">No strike cross or parity gap on this read.</div>';
        }
        events.forEach((ev) => {
          const item = document.createElement("div");
          item.className = "shift-event-item";
          item.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:0.7rem;color:var(--ink-faint);font-family:var(--mono)">${ev.time || ""}</span>
              <span style="font-size:0.68rem;padding:2px 7px;border-radius:4px;background:var(--surface-3);font-weight:750;font-family:var(--mono)">${ev.badge}</span>
              <span style="font-size:0.76rem;color:var(--ink)">${ev.desc}</span>
            </div>
          `;
          shiftContainer.appendChild(item);
        });
      }
      const parityBody = document.getElementById("noa-parity-body");
      const parityBadge = document.getElementById("noa-parity-badge");
      if (parityBody && data.parity_monitor) {
        const mon = data.parity_monitor;
        if (parityBadge) parityBadge.textContent = mon.summary || "Parity";
        const rows = mon.deviations || [];
        parityBody.innerHTML = rows.length ? rows.map((d) => `
          <tr>
            <td>₹${Number(d.strike).toLocaleString("en-IN")}</td>
            <td>${Number(d.call_ltp).toFixed(1)}</td>
            <td>${Number(d.put_ltp).toFixed(1)}</td>
            <td>${Number(d.discrepancy_pts).toFixed(1)} pts · ${Number(d.discrepancy_bps).toFixed(1)} bps</td>
          </tr>`).join("") : '<tr><td colspan="4">No paired call and put quotes.</td></tr>';
      }

      // Populate IV Skew & Term Structure Panel
      if (data.skew_summary) {
        const sk = data.skew_summary;
        const d25El = document.getElementById("noa-skew-25d");
        if (d25El) d25El.textContent = sk.skew_25d == null ? "—" : `${sk.skew_25d >= 0 ? "+" : ""}${Number(sk.skew_25d).toFixed(1)}%`;
        const atmIvEl = document.getElementById("noa-skew-atm-iv");
        if (atmIvEl) atmIvEl.textContent = sk.near_atm_iv == null ? "—" : `${Number(sk.near_atm_iv).toFixed(1)}%`;
        const termEl = document.getElementById("noa-skew-term-spread");
        if (termEl) {
          if (sk.term_spread == null) {
            termEl.textContent = "—";
            termEl.style.color = "var(--ink-mute)";
          } else {
            termEl.textContent = `${sk.term_spread >= 0 ? "+" : ""}${Number(sk.term_spread).toFixed(2)}%${sk.term_label ? ` (${sk.term_label})` : ""}`;
            termEl.style.color = sk.term_spread >= 0 ? "var(--go)" : "var(--stop)";
          }
        }
        const badgeEl = document.getElementById("noa-skew-badge");
        if (badgeEl) badgeEl.textContent = sk.skew_label || "—";
      }

      if (data.iv_skew_curve) {
        renderIVSkewCurve(data.iv_skew_curve);
      }
    } catch (e) {
      console.error("Error loading options analytics:", e);
    }
  }

  function renderIVSkewCurve(points) {
    const container = document.getElementById("iv-skew-chart-container");
    if (!container) return;
    if (!points || points.length === 0) {
      container.textContent = "No chain IV for this window.";
      return;
    }

    const width = container.clientWidth || 800;
    const height = 220;
    const padding = { top: 25, right: 40, bottom: 35, left: 50 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const allIvs = points.flatMap((p) => [p.call_iv, p.put_iv]);
    const minIv = Math.max(5, Math.floor(Math.min(...allIvs) - 1));
    const maxIv = Math.ceil(Math.max(...allIvs) + 1);
    const ivRange = maxIv - minIv || 1;

    const stepX = chartW / Math.max(1, points.length - 1);

    let callPath = "";
    let putPath = "";
    let dotsHtml = "";
    let labelsHtml = "";

    points.forEach((p, i) => {
      const cx = padding.left + i * stepX;
      const callY = padding.top + chartH - ((p.call_iv - minIv) / ivRange) * chartH;
      const putY = padding.top + chartH - ((p.put_iv - minIv) / ivRange) * chartH;

      callPath += `${i === 0 ? "M" : "L"} ${cx.toFixed(1)} ${callY.toFixed(1)} `;
      putPath += `${i === 0 ? "M" : "L"} ${cx.toFixed(1)} ${putY.toFixed(1)} `;

      if (p.is_atm) {
        dotsHtml += `
          <line x1="${cx}" y1="${padding.top}" x2="${cx}" y2="${padding.top + chartH}" stroke="rgba(255,255,255,0.25)" stroke-dasharray="3,3" />
          <text x="${cx}" y="${padding.top - 6}" text-anchor="middle" font-size="10" font-weight="800" fill="var(--ink)" font-family="var(--mono)">ATM</text>
        `;
      }

      dotsHtml += `
        <circle cx="${cx}" cy="${callY}" r="3" fill="#38bdf8"><title>CE IV: ${p.call_iv.toFixed(1)}%</title></circle>
        <circle cx="${cx}" cy="${putY}" r="3" fill="#fb923c"><title>PE IV: ${p.put_iv.toFixed(1)}%</title></circle>
      `;

      if (i % 2 === 0 || p.is_atm) {
        labelsHtml += `<text x="${cx}" y="${height - 10}" text-anchor="middle" font-size="10" fill="var(--ink-mute)" font-family="var(--mono)">${p.strike}</text>`;
      }
    });

    container.innerHTML = `
      <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow:visible">
        <!-- Grid lines -->
        <line x1="${padding.left}" y1="${padding.top}" x2="${width - padding.right}" y2="${padding.top}" stroke="rgba(255,255,255,0.05)" />
        <line x1="${padding.left}" y1="${padding.top + chartH}" x2="${width - padding.right}" y2="${padding.top + chartH}" stroke="rgba(255,255,255,0.1)" />
        <text x="${padding.left - 8}" y="${padding.top + 4}" text-anchor="end" font-size="10" fill="var(--ink-faint)" font-family="var(--mono)">${maxIv}%</text>
        <text x="${padding.left - 8}" y="${padding.top + chartH + 4}" text-anchor="end" font-size="10" fill="var(--ink-faint)" font-family="var(--mono)">${minIv}%</text>
        
        <!-- Call IV Curve (Blue) -->
        <path d="${callPath}" fill="none" stroke="#38bdf8" stroke-width="2.5" stroke-linecap="round" />
        <!-- Put IV Curve (Orange) -->
        <path d="${putPath}" fill="none" stroke="#fb923c" stroke-width="2.5" stroke-linecap="round" />
        
        ${dotsHtml}
        ${labelsHtml}
      </svg>
    `;
  }

  async function loadJournal() {
    try {
      const res = await fetch(`/api/journal?symbol=${state.symbol}`);
      const data = await res.json();

      document.getElementById("journal-total-mtm").textContent = `${data.total_unrealized_mtm >= 0 ? "+" : ""}₹${data.total_unrealized_mtm.toLocaleString("en-IN")}`;
      document.getElementById("journal-total-mtm").style.color = data.total_unrealized_mtm >= 0 ? "var(--go)" : "var(--stop)";

      const tbody = document.getElementById("open-positions-body");
      if (tbody) {
        tbody.innerHTML = "";
        if (!data.open_positions || data.open_positions.length === 0) {
          tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:16px;color:var(--ink-mute)">No active open paper trades in ledger.</td></tr>';
        } else {
          data.open_positions.forEach((pos) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td style="font-family:var(--mono);font-size:0.75rem">${pos.trade_id.slice(-8)}</td>
              <td><b>${pos.symbol}</b></td>
              <td>${pos.strategy}</td>
              <td>${pos.lot_size} Qty</td>
              <td style="font-size:0.75rem;color:var(--ink-mute)">${pos.entry_time.slice(11, 19)}</td>
              <td>₹${pos.entry_prem.toFixed(1)}</td>
              <td>₹${pos.current_prem.toFixed(1)}</td>
              <td style="color:${pos.unrealized_pnl >= 0 ? "var(--go)" : "var(--stop)"};font-weight:750">
                ${pos.unrealized_pnl >= 0 ? "+" : ""}₹${pos.unrealized_pnl.toLocaleString("en-IN")}
              </td>
              <td>
                <button class="btn btn-danger" style="font-size:0.68rem;padding:3px 8px" onclick="window.beginClosePosition('${pos.trade_id}', ${pos.current_prem})">Close</button>
              </td>
            `;
            tbody.appendChild(tr);
          });
        }
      }
    } catch (e) {
      console.error("Error loading journal:", e);
    }
  }

  async function loadScorecard() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/scorecard?symbol=${requestedSym}`);
      const data = await res.json();
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) return;

      document.getElementById("scorecard-kpi-pnl").textContent = data.kpis.realized_pnl;
      document.getElementById("scorecard-kpi-trades").textContent = `${data.kpis.total_trades} Trades`;
      document.getElementById("scorecard-kpi-winrate").textContent = data.kpis.win_rate;
      document.getElementById("scorecard-kpi-pf").textContent = data.kpis.profit_factor;
      document.getElementById("scorecard-kpi-dd").textContent = data.kpis.max_drawdown;

      const fillBreakdown = (id, rows, key) => {
        const body = document.getElementById(id);
        if (!body) return;
        body.innerHTML = (rows || []).length
          ? rows.map((r) => `<tr><td>${r[key]}</td><td>${r.trades}</td><td>${r.win_rate}</td><td>${r.net_pnl}</td></tr>`).join("")
          : '<tr><td colspan="4">No closed trades.</td></tr>';
      };
      fillBreakdown("scorecard-regime-body", data.regime_breakdown, "regime");
      fillBreakdown("scorecard-strategy-body", data.strategy_breakdown, "strategy");
      const sentBody = document.getElementById("scorecard-sentinel-body");
      if (sentBody) {
        const rows = data.sentinel_outcomes || [];
        sentBody.innerHTML = rows.length
          ? rows.map((o) => `<tr><td>${o.signal_type}</td><td>${o.entry}</td><td>${o.mfe}</td><td>${o.mae}</td><td>${o.ret_5m}</td><td>${o.ret_15m}</td><td>${o.target_hit ? "Target" : o.stopped_out ? "Stopped" : "Open"}</td></tr>`).join("")
          : '<tr><td colspan="7">No evaluated sentinel signals.</td></tr>';
      }

      // Render Cumulative Equity Curve SVG
      const eqContainer = document.getElementById("equity-curve-container");
      if (eqContainer && data.equity_curve && data.equity_curve.length > 0) {
        const pts = data.equity_curve;
        const vals = pts.map((p) => p.cumulative);
        const minVal = Math.min(0, ...vals);
        const maxVal = Math.max(1000, ...vals);
        const range = maxVal - minVal || 1;
        const width = eqContainer.clientWidth || 700;
        const height = 240;

        const pathData = pts
          .map((p, i) => {
            const x = (i / Math.max(1, pts.length - 1)) * (width - 40) + 20;
            const y = height - 20 - ((p.cumulative - minVal) / range) * (height - 40);
            return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
          })
          .join(" ");

        eqContainer.innerHTML = `
          <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow:visible">
            <line x1="20" y1="${height - 20 - ((0 - minVal) / range) * (height - 40)}" x2="${width - 20}" y2="${height - 20 - ((0 - minVal) / range) * (height - 40)}" stroke="rgba(255,255,255,0.1)" stroke-dasharray="4" />
            <path d="${pathData}" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        `;
      }

      // Closed Trades Table
      const tbody = document.getElementById("closed-trades-body");
      if (tbody && data.closed_trades) {
        tbody.innerHTML = "";
        data.closed_trades.forEach((t) => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td style="font-family:var(--mono);font-size:0.75rem">${t.trade_id.slice(-8)}</td>
            <td>${t.strategy}</td>
            <td style="font-size:0.75rem;color:var(--ink-mute)">${t.entry_time.slice(0, 16)}</td>
            <td style="font-size:0.75rem;color:var(--ink-mute)">${t.exit_time.slice(0, 16)}</td>
            <td style="font-weight:750;color:${t.realized_pnl >= 0 ? "var(--go)" : "var(--stop)"}">
              ${t.realized_pnl >= 0 ? "+" : ""}₹${t.realized_pnl.toLocaleString("en-IN")}
            </td>
            <td><span style="font-size:0.7rem;background:var(--surface-3);padding:2px 6px;border-radius:4px">${t.exit_reason}</span></td>
          `;
          tbody.appendChild(tr);
        });
      }
    } catch (e) {
      console.error("Error loading scorecard:", e);
    }
  }

  async function loadOps() {
    try {
      const res = await fetch(`/api/ops-calendar?symbol=${state.symbol}`);
      const data = await res.json();

      document.getElementById("ops-cb-state").textContent = data.circuit_breaker.state;
      document.getElementById("ops-poller-rtt").textContent = `${data.resilience.adaptive_poller_rtt_ms} ms`;
      document.getElementById("ops-cache-entries").textContent = `${data.resilience.cache_entries} Keys`;
      document.getElementById("ops-wal-size").textContent = `${data.resilience.sqlite_wal_mb} MB`;

      // 11-Window Timeline Track
      const track = document.getElementById("timeline-track-container");
      if (track && data.timeline_windows) {
        track.innerHTML = "";
        data.timeline_windows.forEach((w) => {
          const node = document.createElement("div");
          node.className = `timeline-node ${w.status === "ACTIVE" ? "active" : ""}`;
          let badgeHtml = "";
          if (w.status === "ACTIVE") badgeHtml = '<span class="badge-active">● ACTIVE</span>';
          else if (w.status === "COMPLETED") badgeHtml = '<span class="badge-done">✓ COMPLETED</span>';
          else badgeHtml = '<span class="badge-upcoming">UPCOMING</span>';

          node.innerHTML = `
            <div style="font-family:var(--mono);font-size:0.8rem;color:var(--spark);font-weight:700">${w.range}</div>
            <div style="font-weight:600;font-size:0.82rem">${w.name}</div>
            <div>${badgeHtml}</div>
          `;
          track.appendChild(node);
        });
      }
    } catch (e) {
      console.error("Error loading ops:", e);
    }
  }

  window.loadCommentary = loadCommentary;

  state.commentaryFilter = "ALL";
  state.commentaryData = null;

  async function loadCommentary() {
    try {
      const requestedSym = (state.symbol || "").toUpperCase();
      const res = await fetch(`/api/session-commentary?symbol=${requestedSym}`);
      const data = await res.json();
      if ((data.symbol && data.symbol.toUpperCase() !== (state.symbol || "").toUpperCase()) ||
          requestedSym !== (state.symbol || "").toUpperCase()) return;
      state.commentaryData = data;

      const titleEl = document.getElementById("commentary-phase-title");
      if (titleEl) {
        const dateTag = data.date_badge_text || data.session_date || "Current Session";
        titleEl.innerHTML = `${data.phase} Session Narrative <span style="font-size:0.75rem;font-weight:700;color:var(--spark);background:var(--spark-bg);border:1px solid rgba(56,189,248,.3);padding:2px 8px;border-radius:4px;vertical-align:middle;margin-left:8px">${dateTag}</span>`;
      }

      // 1. Render Pre-Market Tactical Baseline Card (Morning Anchor - whole day on commentary)
      const pmCard = document.getElementById("comm-pre-market-card");
      if (pmCard && data.pre_market) {
        const pm = data.pre_market;

        let shocksTableHtml = "";
        if (pm.shocks && pm.shocks.projected_strikes && pm.shocks.projected_strikes.length > 0) {
          const shockRows = pm.shocks.projected_strikes.map((s) => `
            <tr style="border-bottom:1px solid rgba(255,255,255,0.06);font-family:var(--mono)">
              <td style="padding:4px 6px;font-weight:700">${s.strike}</td>
              <td style="padding:4px 6px;color:var(--go)">₹${s.ce_price ? Number(s.ce_price).toFixed(1) : "-"}</td>
              <td style="padding:4px 6px;color:${(s.ce_shock_pct || 0) >= 0 ? "var(--go)" : "var(--stop)"}">${(s.ce_shock_pct || 0) >= 0 ? "+" : ""}${(s.ce_shock_pct || 0).toFixed(1)}%</td>
              <td style="padding:4px 6px;color:var(--stop)">₹${s.pe_price ? Number(s.pe_price).toFixed(1) : "-"}</td>
              <td style="padding:4px 6px;color:${(s.pe_shock_pct || 0) >= 0 ? "var(--go)" : "var(--stop)"}">${(s.pe_shock_pct || 0) >= 0 ? "+" : ""}${(s.pe_shock_pct || 0).toFixed(1)}%</td>
              <td style="padding:4px 6px;color:var(--spark)">₹${s.straddle_price ? Number(s.straddle_price).toFixed(1) : "-"}</td>
            </tr>
          `).join("");

          const isShockOpen = (window._commShockMatrixOpen !== undefined) ? window._commShockMatrixOpen : false;
          shocksTableHtml = `
            <details class="collapsible-matrix" ontoggle="window._commShockMatrixOpen = this.open" style="margin-top:10px;background:rgba(0,0,0,0.25);border:1px solid var(--line);border-radius:6px;padding:8px 12px" ${isShockOpen ? "open" : ""}>
              <summary style="cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;user-select:none;outline:none">
                <div style="display:flex;align-items:center;gap:8px">
                  <span class="matrix-chevron" style="color:var(--spark);font-size:0.72rem;display:inline-block;transition:transform 0.2s">▼</span>
                  <span style="font-size:0.74rem;font-weight:800;color:var(--spark)">⚡ ATM Straddle &amp; 5-Strike Pre-Market Shock Matrix</span>
                  <span style="font-size:0.65rem;color:var(--ink-faint);background:var(--surface-2);padding:1px 6px;border-radius:4px;border:1px solid var(--line)">Collapse / Expand</span>
                </div>
                <span style="font-size:0.7rem;color:var(--ink-mute);font-family:var(--mono)">ATM Straddle: <b style="color:var(--ink)">₹${pm.shocks.atm_straddle_projected || 0}</b> (<span style="color:${(pm.shocks.straddle_shock_pct || 0) >= 0 ? "var(--go)" : "var(--stop)"}">${(pm.shocks.straddle_shock_pct || 0) >= 0 ? "+" : ""}${(pm.shocks.straddle_shock_pct || 0).toFixed(1)}%</span>)</span>
              </summary>
              <div style="margin-top:8px;overflow-x:auto">
                <table style="width:100%;font-size:0.70rem;border-collapse:collapse;text-align:left">
                  <thead>
                    <tr style="color:var(--ink-mute);border-bottom:1px solid var(--line)">
                      <th style="padding:4px 6px">Strike</th>
                      <th style="padding:4px 6px">CE Theo</th>
                      <th style="padding:4px 6px">CE Shock</th>
                      <th style="padding:4px 6px">PE Theo</th>
                      <th style="padding:4px 6px">PE Shock</th>
                      <th style="padding:4px 6px">Straddle</th>
                    </tr>
                  </thead>
                  <tbody>${shockRows}</tbody>
                </table>
              </div>
            </details>
          `;
        }

        const morning = Boolean(pm.is_morning_briefing) && !pm.is_discovered;
        const openLabel = morning
          ? (pm.snapshot_stale ? "Close withheld" : (pm.snapshot_session_label ? `${pm.snapshot_session_label} close` : "Previous NSE close"))
          : "Discovered Open";
        const openPx = morning ? Number(pm.prev_close || 0) : Number(pm.discovered_spot || state.spot || 0);
        const openTxt = openPx > 0 ? `₹${openPx.toLocaleString("en-IN", { minimumFractionDigits: 1 })}` : "—";
        const gapTxt = morning
          ? "No auction print yet"
          : `${(pm.gap_pts || 0) >= 0 ? "+" : ""}${Number(pm.gap_pts || 0).toFixed(1)} pts (${(pm.gap_pct || 0) >= 0 ? "+" : ""}${Number(pm.gap_pct || 0).toFixed(2)}%)`;
        const gapClr = morning ? "var(--ink-mute)" : ((pm.gap_pts || 0) >= 0 ? "var(--go)" : "var(--stop)");
        const oiShown = pm.oi_as_of_display || formatValidStamp(pm.oi_as_of);
        const stamp = morning
          ? (oiShown ? `OI ${oiShown}` : "OI time unavailable")
          : `${datedClock(pm.discovered_at || "09:07:30", pm.date)} Discovery`;
        const cardTitle = morning ? "Morning briefing · prior-session book" : "Pre-Bell Tactical Baseline &amp; Opening Discovery";
        pmCard.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:1.15rem">🌅</span>
              <b style="font-size:0.95rem;color:var(--warn)">${cardTitle}</b>
              <span style="font-size:0.7rem;color:var(--ink-mute);font-family:var(--mono)">${stamp}</span>
            </div>
            <span class="badge-active" style="background:var(--surface-2);color:var(--warn);border:1px solid var(--warn);font-size:0.72rem">${pm.regime_badge || "PRE-BELL BASELINE"}</span>
          </div>
          <div class="pre-bell-grid">
            <div class="pre-bell-item">
              <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">${openLabel}</small>
              <b style="font-size:1.05rem;font-family:var(--mono);color:var(--ink)">${openTxt}</b>
            </div>
            <div class="pre-bell-item">
              <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Opening Gap</small>
              <b style="font-size:1.05rem;font-family:var(--mono);color:${gapClr}">${gapTxt}</b>
            </div>
            <div class="pre-bell-item">
              <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Gamma strikes</small>
              <b style="font-size:1.0rem;font-family:var(--mono);color:var(--spark)">${pm.walls || "24,600 / 24,400"}</b>
            </div>
            <div class="pre-bell-item">
              <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Favored Opening Play</small>
              <b style="font-size:0.92rem;color:var(--spark)">${pm.favored_play || "Map only"}</b>
            </div>
          </div>
          <div style="font-size:0.8rem;line-height:1.5;color:var(--ink);background:var(--surface-2);padding:8px 12px;border-radius:6px;border:1px solid var(--line);margin-top:8px">
            ${pm.regime_briefing || pm.briefing || "Prior-session gamma strikes. The open is a print on the book, not a fade."}
          </div>
          ${shocksTableHtml}
        `;
      }

      // 2. Render Chronological Story Cards
      renderCommentaryCards();

      // 3. Render Post-Market Settlement & Daily Close Scorecard
      const postCard = document.getElementById("comm-post-market-card");
      if (postCard) {
        const post = data.post_market;
        const isSettled = post && (post.is_post_market || post.post_market_settled);
        if (isSettled) {
          postCard.style.display = "block";
          const settleSpot = (post.settlement_spot && post.settlement_spot > 0) ? post.settlement_spot : state.spot;
          const dateTag = post.date ? ` · ${post.date}` : "";
          postCard.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
              <div style="display:flex;align-items:center;gap:8px">
                <span style="font-size:1.15rem">🏁</span>
                <h3 style="font-size:1.05rem;font-weight:800;color:var(--purple)">Post-Market Settlement &amp; Daily Close Scorecard${dateTag}</h3>
              </div>
              <span class="badge-active" style="background:rgba(168,85,247,0.15);color:var(--purple);font-size:0.72rem">OFFICIAL CLOSE</span>
            </div>
            <div class="pre-bell-grid" style="margin-bottom:12px">
              <div class="pre-bell-item">
                <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Settlement Spot</small>
                <b style="font-size:1.1rem;font-family:var(--mono);color:var(--ink)">₹${Number(settleSpot).toLocaleString("en-IN", { minimumFractionDigits: 1, maximumFractionDigits: 2 })}</b>
              </div>
              <div class="pre-bell-item">
                <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Session Range (H / L)</small>
                <b style="font-size:1.0rem;font-family:var(--mono);color:var(--spark)">${post.day_range || "N/A"}</b>
              </div>
              <div class="pre-bell-item">
                <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Net Day Change</small>
                <b style="font-size:1.05rem;font-family:var(--mono);color:${(post.net_change_pts || 0) >= 0 ? "var(--go)" : "var(--stop)"}">${(post.net_change_pts || 0) >= 0 ? "+" : ""}${Number(post.net_change_pts || 0).toFixed(1)} pts (${(post.net_change_pct || 0) >= 0 ? "+" : ""}${Number(post.net_change_pct || 0).toFixed(2)}%)</b>
              </div>
              <div class="pre-bell-item">
                <small style="font-size:0.64rem;color:var(--ink-mute);text-transform:uppercase;font-weight:700;display:block">Next Session Key Levels</small>
                <b style="font-size:0.88rem;color:var(--spark)">${post.next_key_levels || "Gamma strikes unavailable"}</b>
              </div>
            </div>
            <div style="font-size:0.82rem;line-height:1.6;color:var(--ink);background:var(--surface-2);padding:10px 14px;border-radius:6px;border:1px solid var(--line)">
              ${post.institutional_wrap || post.settle_note || post.status_text || "Session concluded. The official close is a settlement print."}
            </div>
          `;
        } else {
          postCard.style.display = "none";
        }
      }
    } catch (e) {
      console.error("Error loading commentary:", e);
    }
  }

  function renderCommentaryCards() {
    const container = document.getElementById("commentary-stream-container");
    if (!container || !state.commentaryData) return;

    const allCards = state.commentaryData.cards || state.commentaryData.entries || [];
    let filtered = allCards;
    if (state.commentaryFilter === "BITES") {
      filtered = allCards.filter((c) => (c.bites && c.bites.length > 0) || c.bite != null);
    } else if (state.commentaryFilter === "PINNED") {
      filtered = allCards.filter((c) => c.is_pinned || c.pinned_strike);
    }

    container.innerHTML = "";
    if (filtered.length === 0) {
      container.innerHTML = '<div style="color:var(--ink-mute);font-size:0.85rem;padding:16px;text-align:center">No commentary entries match filter.</div>';
      return;
    }

    filtered.forEach((c) => {
      const card = document.createElement("div");
      const cardCls = c.card_class || (c.theme ? `comm-card-${c.theme}` : "comm-card-bullish");
      card.className = `comm-card ${cardCls}`;

      // Suggestion Bite HTML (supports both array 'bites' and single 'bite')
      const bites = c.bites && c.bites.length > 0 ? c.bites : c.bite ? [c.bite] : [];
      let bitesHtml = "";
      if (bites.length > 0) {
        bitesHtml = bites
          .map((b) => {
            const actionType = b.type || b.action || "WATCH";
            const pillCls = b.pill_class || `bite-${actionType.toLowerCase()}`;
            const biteText = b.text || "";
            const refLevel = b.ref_level ? `<span style="font-size:0.68rem;font-family:var(--mono);color:var(--spark)">Ref: ${b.ref_level}</span>` : "";
            const rationale = b.rationale ? `<div style="font-size:0.72rem;color:var(--ink-mute);margin-top:2px">${b.rationale}</div>` : "";
            return `
              <div class="comm-bite-box">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                  <div style="display:flex;align-items:center;gap:6px">
                    <span class="comm-bite-pill ${pillCls}">${actionType}</span>
                    <b style="font-size:0.78rem;color:var(--ink)">${biteText}</b>
                  </div>
                  ${refLevel}
                </div>
                ${rationale}
              </div>
            `;
          })
          .join("");
      }

      // Pinned Strike Tag
      let pinnedHtml = "";
      if (c.pinned_strike) {
        pinnedHtml = `<span style="font-size:0.68rem;background:var(--surface-2);color:var(--spark);padding:2px 8px;border-radius:4px;font-weight:750">PINNED: ${c.pinned_strike}</span>`;
      }

      const topicCls = c.topic_class || (c.topic_theme ? `topic-${c.topic_theme}` : "topic-vwap");
      const timeStr = c.time_str || c.time || "";
      const headline = c.topic || c.headline || "";
      const icon = c.icon ? `<span style="margin-right:4px">${c.icon}</span>` : "";

      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <div style="display:flex;align-items:center;gap:6px">
            <span class="comm-time-badge">${timeStr}</span>
            <span class="comm-topic-badge ${topicCls}">${icon}${c.topic || c.phase || "Structure"}</span>
          </div>
          <div>${pinnedHtml}</div>
        </div>
        <div style="font-size:0.95rem;font-weight:800;color:var(--ink);margin-bottom:4px">${headline}</div>
        <div style="font-size:0.82rem;color:var(--ink-mute);line-height:1.5">${c.body || ""}</div>
        ${bitesHtml}
      `;
      container.appendChild(card);
    });
  }

  window.filterCommentary = function (flt) {
    state.commentaryFilter = flt;
    const btns = ["all", "bites", "pinned"];
    btns.forEach((b) => {
      const btn = document.getElementById(`comm-flt-${b}`);
      if (btn) {
        if (b.toUpperCase() === flt.toUpperCase()) btn.className = "btn btn-primary";
        else btn.className = "btn btn-secondary";
      }
    });
    renderCommentaryCards();
  };

  // -------------------------------------------------------------------------
  // User Actions (Execution, Bookmarking, Sliders)
  // -------------------------------------------------------------------------
  window.toggleBookmark = async function (recId) {
    try {
      const res = await fetch("/api/recommendations/bookmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rec_id: recId }),
      });
      const data = await res.json();
      playAudioTone(600, "sine", 0.15);
      showToast(data.is_bookmarked ? "Setup pinned to Cockpit tab ★" : "Setup unpinned ☆");
      loadRecommendations();
      loadCockpit();
    } catch (e) {
      console.error("Error toggling bookmark:", e);
    }
  };

  window.unpinCard = async function (recId) {
    await window.toggleBookmark(recId);
  };

  window.bookmarkCard = window.toggleBookmark;

  window.executePaperTrade = async function (recId, optSymbol, strike, side, ltp, lots) {
    try {
      const res = await fetch("/api/recommendations/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rec_id: recId,
          symbol: state.symbol,
          lots: lots,
          ltp: ltp,
          strike: strike,
          side: side,
          strategy_name: `⚡ ${optSymbol}`,
          spot: state.spot,
        }),
      });
      const data = await res.json();
      playSuccessChime();
      showToast(data.message, "🚀");

      // Switch to Journal tab automatically
      const journalTabBtn = document.querySelector('[data-tab="tab-journal"]');
      if (journalTabBtn) journalTabBtn.click();
    } catch (e) {
      console.error("Error executing trade:", e);
    }
  };

  window.beginClosePosition = function (tradeId, currentPrem) {
    state.closingTradeId = tradeId;
    const panel = document.getElementById("journal-close-panel");
    if (panel) panel.style.display = "block";
    const idEl = document.getElementById("journal-close-id");
    if (idEl) idEl.textContent = tradeId.slice(-8);
    const prem = document.getElementById("journal-close-prem");
    if (prem) prem.value = currentPrem;
  };

  window.closePosition = async function (tradeId, currentPrem, reason) {
    try {
      await fetch("/api/journal/close-position", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trade_id: tradeId,
          exit_premium: currentPrem,
          reason: reason || "MANUAL",
        }),
      });
      const panel = document.getElementById("journal-close-panel");
      if (panel) panel.style.display = "none";
      playCautionChime();
      showToast(`Position ${tradeId.slice(-6)} closed`);
      loadJournal();
      loadScorecard();
    } catch (e) {
      console.error("Error closing position:", e);
    }
  };

  // -------------------------------------------------------------------------
  // Event Listeners & UI Binding
  // -------------------------------------------------------------------------
  function setupUIEventListeners() {
    // Tab switching
    window.switchTab = function (targetId) {
      const tabBtns = document.querySelectorAll(".tab-btn");
      tabBtns.forEach((b) => {
        if (b.getAttribute("data-tab") === targetId) {
          b.classList.add("active");
        } else {
          b.classList.remove("active");
        }
      });
      document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");

      state.activeTab = targetId;
      refreshActiveTab();

      const hashName = targetId.replace("tab-", "");
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, null, `#${hashName}`);
      }

      if (targetId === "tab-cockpit") {
        setTimeout(() => {
          const mount = document.getElementById("cockpit-gex-canvas-container");
          if (cockpitGexChart && mount) {
            cockpitGexChart.applyOptions({ width: mount.clientWidth || 850 });
          }
        }, 80);
      }

      if (targetId === "tab-sentinel") {
        setTimeout(() => {
          const mount = document.getElementById("sentinel-chart-mount");
          if (state.sentinelChart && mount) {
            state.sentinelChart.applyOptions({ width: mount.clientWidth || 850 });
          }
        }, 80);
      }
    };

    const tabBtns = document.querySelectorAll(".tab-btn");
    tabBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const targetId = btn.getAttribute("data-tab");
        window.switchTab(targetId);
      });
    });

    // Check URL hash on startup
    if (window.location.hash) {
      const hash = window.location.hash.replace("#", "").trim();
      const tabMap = {
        "cockpit": "tab-cockpit",
        "target-engine": "tab-target-engine",
        "target": "tab-target-engine",
        "recommendations": "tab-recommendations",
        "recom": "tab-recommendations",
        "sentinel": "tab-sentinel",
        "sentinel-tape": "tab-sentinel",
        "signal-efficiency": "tab-efficiency",
        "signal": "tab-efficiency",
        "efficiency": "tab-efficiency",
        "market-intelligence": "tab-market-intel",
        "market-intel": "tab-market-intel",
        "intel": "tab-market-intel",
        "options-analytics": "tab-analytics",
        "analytics": "tab-analytics",
        "options": "tab-analytics",
        "journal": "tab-journal",
        "scorecard": "tab-scorecard",
        "ops-calendar": "tab-ops",
        "ops": "tab-ops",
        "session-commentary": "tab-commentary",
        "commentary": "tab-commentary",
        "admin": "tab-admin",
        "admin-data": "tab-admin",
      };
      const initialTab = tabMap[hash] || (document.getElementById(`tab-${hash}`) ? `tab-${hash}` : null);
      if (initialTab) {
        window.switchTab(initialTab);
      }
    }

    // Symbol select
    const symSelect = document.getElementById("index-select");
    if (symSelect) {
      symSelect.addEventListener("change", async (e) => {
        state.symbol = (e.target.value || "NIFTY").toUpperCase();
        cockpitChartState.symbol = state.symbol;
        cockpitChartState.currentBar = null;
        state.lastCockpitDash = null;
        state.tapeLatch = null;
        state.lastTapePaintSig = null;

        // Clear forming bar HUD immediately so previous index values don't linger
        const chlOhlc = document.getElementById("chl-ohlc");
        if (chlOhlc) chlOhlc.textContent = "O: -- H: -- L: -- C: --";
        const chlSym = document.getElementById("chl-sym");
        if (chlSym) chlSym.textContent = state.symbol;

        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          try {
            state.ws.send(JSON.stringify({ action: "subscribe", symbol: state.symbol }));
          } catch (err) {}
        }
        if (typeof renderCockpitChart === "function") {
          renderCockpitChart(state.symbol, cockpitChartState.timeframe, cockpitChartState.engine);
        }
        await fetch("/api/select-index", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: state.symbol, window: state.window }),
        });
        showToast(`Index switched to ${state.symbol}`);
        refreshAll();
      });
    }

    // Window select
    const winSelect = document.getElementById("window-select");
    if (winSelect) {
      winSelect.addEventListener("change", async (e) => {
        state.window = e.target.value;
        await fetch("/api/select-index", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: state.symbol, window: state.window }),
        });
        showToast(`Calculation window updated`);
        refreshAll();
      });
    }

    // Sizing HUD sliders
    const capSlider = document.getElementById("sizer-cap-slider");
    const capVal = document.getElementById("sizer-cap-val");
    if (capSlider && capVal) {
      capSlider.addEventListener("input", (e) => {
        state.capital = parseFloat(e.target.value);
        capVal.textContent = `₹${state.capital.toLocaleString("en-IN")}`;
        updateBudgetDisplay();
        loadRecommendations();
      });
    }

    const riskSlider = document.getElementById("sizer-risk-slider");
    const riskVal = document.getElementById("sizer-risk-val");
    if (riskSlider && riskVal) {
      riskSlider.addEventListener("input", (e) => {
        state.riskPct = parseFloat(e.target.value);
        riskVal.textContent = `${state.riskPct.toFixed(2)}%`;
        updateBudgetDisplay();
        loadRecommendations();
      });
    }

    function updateBudgetDisplay() {
      const budgetEl = document.getElementById("sizer-budget-val");
      if (budgetEl) {
        const budget = (state.capital * (state.riskPct / 100.0)).toFixed(0);
        budgetEl.textContent = `₹${parseInt(budget).toLocaleString("en-IN")} / trade`;
      }
    }

    // What-If Repricer sliders
    const spotShift = document.getElementById("slider-spot-shift");
    const ivShock = document.getElementById("slider-iv-shock");
    const decayMins = document.getElementById("slider-decay-mins");
    if (spotShift && ivShock && decayMins) {
      const updateRepricer = () => {
        const sShift = parseFloat(spotShift.value);
        const ivS = parseFloat(ivShock.value);
        const dM = parseFloat(decayMins.value);

        document.getElementById("val-spot-shift").textContent = `${sShift >= 0 ? "+" : ""}${sShift} pts`;
        document.getElementById("val-iv-shock").textContent = `${ivS >= 0 ? "+" : ""}${ivS.toFixed(1)}%`;
        document.getElementById("val-decay-mins").textContent = `${dM} mins`;

        // Black-76 Greek repricing derived from authentic contract model telemetry
        const baseLtp = (state.teRepriceEntry && state.teRepriceEntry > 0) ? state.teRepriceEntry : 120.0;
        const delta = (state.teRepriceDelta !== undefined) ? state.teRepriceDelta : 0.50;
        const gamma = (state.teRepriceGamma !== undefined) ? state.teRepriceGamma : 0.0028;
        const vega = (state.teRepriceVega !== undefined) ? state.teRepriceVega : 14.5;
        const theta = (state.teRepriceTheta !== undefined) ? state.teRepriceTheta : -14.2;

        const deltaPnL = sShift * delta + 0.5 * gamma * sShift * sShift;
        const vegaPnL = ivS * vega;
        const thetaPnL = (theta / 375.0) * dM;
        const repriced = Math.max(0.5, baseLtp + deltaPnL + vegaPnL + thetaPnL);
        const diff = repriced - baseLtp;
        const pct = baseLtp > 0 ? (diff / baseLtp) * 100.0 : 0.0;

        const ltpEl = document.getElementById("repriced-sample-ltp");
        if (ltpEl) ltpEl.textContent = `₹${repriced.toFixed(2)}`;
        const pnlEl = document.getElementById("repriced-sample-pnl");
        if (pnlEl) {
          pnlEl.textContent = `${diff >= 0 ? "+" : ""}₹${diff.toFixed(2)} · ${diff >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
          pnlEl.style.color = diff >= 0 ? "var(--go)" : "var(--stop)";
        }
      };

      window.updateWhatIfRepricer = updateRepricer;
      spotShift.addEventListener("input", updateRepricer);
      ivShock.addEventListener("input", updateRepricer);
      decayMins.addEventListener("input", updateRepricer);
    }

    // Force Re-sync Snapshot — wakes the background chain ingest; does not block on Dhan.
    const btnSync = document.getElementById("btn-force-refresh");
    if (btnSync) {
      btnSync.addEventListener("click", async () => {
        showToast("Synchronizing live market snapshot...");
        try {
          await fetch("/api/ops/resync-snapshot", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: state.symbol, window: state.window }),
          });
        } catch (e) {}
        refreshAll();
      });
    }

    // Save Dhan Token
    const btnSaveToken = document.getElementById("btn-save-token");
    if (btnSaveToken) {
      btnSaveToken.addEventListener("click", async () => {
        const tokenInput = document.getElementById("dhan-token-input");
        const tok = tokenInput ? tokenInput.value.trim() : "";
        if (!tok) {
          alert("Please enter a valid Dhan Access Token");
          return;
        }
        try {
          const res = await fetch("/api/save-dhan-token", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: tok }),
          });
          const data = await res.json();
          showToast(data.message, "💾");
          if (tokenInput) tokenInput.value = "";
          loadStatus();
        } catch (e) {
          alert("Failed to save token");
        }
      });
    }

    // Sound toggle
    const btnSound = document.getElementById("btn-sound-toggle");
    if (btnSound) {
      btnSound.addEventListener("click", () => {
        state.soundEnabled = !state.soundEnabled;
        btnSound.textContent = state.soundEnabled ? "🔔 Audio Chimes: ON" : "🔕 Audio Chimes: OFF";
        showToast(`Sound alerts ${state.soundEnabled ? "enabled" : "muted"}`);
      });
    }

    // Theme Mode Toggle (Header & Sidebar buttons, plus Ctrl+Shift+D shortcut)
    const btnTheme = document.getElementById("btn-theme-toggle");
    if (btnTheme) {
      btnTheme.addEventListener("click", () => {
        toggleTheme();
      });
    }

    const btnSidebarTheme = document.getElementById("btn-sidebar-theme-toggle");
    if (btnSidebarTheme) {
      btnSidebarTheme.addEventListener("click", () => {
        toggleTheme();
      });
    }

    // Global keyboard shortcut: Ctrl+Shift+D or Cmd+Shift+D to toggle theme
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "D" || e.key === "d")) {
        e.preventDefault();
        toggleTheme();
      }
    });

    // Voice broadcast
    const btnVoice = document.getElementById("btn-play-voice");
    if (btnVoice) {
      btnVoice.addEventListener("click", () => {
        if (!state.speechSynth) {
          alert("Speech synthesis is not supported on this browser.");
          return;
        }
        const headline = document.querySelector("#commentary-stream-container div:first-child div:nth-child(2)");
        const textToSpeak = headline ? headline.textContent : "Call-gamma concentration at 24,500";
        const utterance = new SpeechSynthesisUtterance(textToSpeak);
        const speedSel = document.getElementById("voice-speed-select");
        if (speedSel) utterance.rate = parseFloat(speedSel.value);
        state.speechSynth.speak(utterance);
        showToast("Broadcasting commentary audio...", "🎙️");
      });
    }

    // Manual Trade Logging
    const btnManual = document.getElementById("btn-manual-submit");
    if (btnManual) {
      btnManual.addEventListener("click", async () => {
        const sym = document.getElementById("manual-sym").value;
        const strat = document.getElementById("manual-strat").value;
        const lots = parseInt(document.getElementById("manual-lots").value);
        const prem = parseFloat(document.getElementById("manual-prem").value);

        const idxLots = sym === "SENSEX" ? 20 : (sym === "BANKNIFTY" ? 30 : 65);
        await fetch("/api/journal/manual-trade", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol: sym,
            strategy_name: strat,
            lot_size: lots * idxLots,
            entry_net_premium: prem,
            entry_spot: state.spot,
          }),
        });
        playSuccessChime();
        showToast(`Manual paper trade logged: ${strat}`, "📝");
        loadJournal();
      });
    }

    // Reset CB & Clear Cache
    const btnResetCb = document.getElementById("btn-ops-reset-cb");
    if (btnResetCb) {
      btnResetCb.addEventListener("click", async () => {
        await fetch("/api/ops/reset-circuit-breaker", { method: "POST" });
        showToast("Circuit breaker reset to CLOSED");
        loadOps();
      });
    }

    const btnClearCache = document.getElementById("btn-ops-clear-cache");
    if (btnClearCache) {
      btnClearCache.addEventListener("click", async () => {
        await fetch("/api/ops/clear-cache", { method: "POST" });
        showToast("LKGS memory cache cleared");
        loadOps();
      });
    }
  }

  function refreshActiveTab() {
    switch (state.activeTab) {
      case "tab-cockpit":
        loadCockpit();
        break;
      case "tab-target-engine":
        loadTargetEngine();
        break;
      case "tab-recommendations":
        loadRecommendations();
        break;
      case "tab-sentinel":
        loadSentinelTape();
        break;
      case "tab-efficiency":
        loadSignalEfficiency();
        break;
      case "tab-market-intel":
        loadMarketIntel();
        break;
      case "tab-analytics":
        loadOptionsAnalytics();
        break;
      case "tab-journal":
        loadJournal();
        break;
      case "tab-scorecard":
        loadScorecard();
        break;
      case "tab-ops":
        loadOps();
        break;
      case "tab-commentary":
        loadCommentary();
        break;
      case "tab-admin":
        if (window.loadAdmin) window.loadAdmin();
        break;
    }
  }

  function refreshAll() {
    loadStatus();
    refreshActiveTab();
  }

  function getClientReopenCountdown() {
    const now = new Date();
    // Convert to IST offset UTC+5.5
    const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
    const istDate = new Date(utc + (3600000 * 5.5));
    const day = istDate.getDay(); // 0 = Sun, 1 = Mon, ..., 5 = Fri, 6 = Sat
    const hours = istDate.getHours();
    const mins = istDate.getMinutes();
    const tMin = hours * 60 + mins;

    let targetDayName = "Tomorrow";
    if (day === 5 && tMin >= 15 * 60 + 40) {
      targetDayName = "Mon";
    } else if (day === 6) {
      targetDayName = "Mon";
    } else if (day === 0) {
      targetDayName = "Tomorrow (Mon)";
    } else if (day === 1 && tMin < 9 * 60 + 15) {
      targetDayName = "Today (Mon)";
    } else {
      const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
      const nextDayName = dayNames[(day + 1) % 7];
      targetDayName = `Tomorrow (${nextDayName})`;
    }
    return `Reopens ${targetDayName} 09:15 IST`;
  }

  // -------------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------------
  function startCockpit() {
    setupUIEventListeners();
    // Environment visual badge
    if (window.location.port === "8650" || window.location.search.includes("dev=true")) {
      const sbVersion = document.getElementById("sidebar-version");
      if (sbVersion) {
        sbVersion.innerHTML = `v5.3 <span style="background:rgba(245,158,11,0.15);color:#f59e0b;padding:1px 4px;border-radius:3px;font-size:0.6rem;font-weight:700;border:1px solid rgba(245,158,11,0.3)">DEV</span>`;
      }
    }
    const symSelect = document.getElementById("index-select");
    if (symSelect && symSelect.value) {
      state.symbol = symSelect.value.toUpperCase();
    }
    cockpitChartState.symbol = state.symbol;
    fetch("/api/select-index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.symbol, window: state.window }),
    }).catch(() => {});
    initWebSocket();
    const pollPinAlerts = async () => {
      try {
        const res = await fetch("/api/recommendations/alerts");
        const data = await res.json();
        const alerts = data.alerts || [];
        if (!alerts.length) return;
        alerts.forEach((a) => {
          showToast(`${a.event_type}: ${a.message || a.rec_id}`);
          if (state.soundEnabled) playCautionChime();
        });
        await fetch("/api/recommendations/alerts/ack", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ event_ids: alerts.map((a) => a.event_id) }),
        });
      } catch (e) {}
    };
    pollPinAlerts();
    setInterval(pollPinAlerts, 20000);
    const closeBtn = document.getElementById("journal-close-confirm");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => {
        if (!state.closingTradeId) return;
        const prem = parseFloat(document.getElementById("journal-close-prem").value);
        const reason = document.getElementById("journal-close-reason").value;
        window.closePosition(state.closingTradeId, prem, reason);
      });
    }
    const bellBtn = document.getElementById("alert-bell-toggle-btn");
    if (bellBtn) {
      if (state.alertBellEnabled) {
        bellBtn.textContent = "🔔 Bell: ON";
        bellBtn.className = "tape-ctrl-btn active";
      } else {
        bellBtn.textContent = "🔕 Bell: MUTE";
        bellBtn.className = "tape-ctrl-btn muted";
      }
    }
    // Apply initialized theme state
    applyTheme(state.theme, false);

    if (window.refreshAdminChrome) {
      window.refreshAdminChrome();
      setInterval(window.refreshAdminChrome, 30000);
    }

    // Mount Cockpit TradingView chart immediately
    renderCockpitChart(state.symbol, cockpitChartState.timeframe, cockpitChartState.engine);
    refreshAll();
    setInterval(loadStatus, 5000);
    setInterval(() => {
      if (state.activeTab === "tab-cockpit") loadCockpit();
    }, 20000);
    setInterval(() => {
      if (!isClientPreBellWindow()) syncCockpitPreBellCard(false);
    }, 1000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startCockpit);
  } else {
    startCockpit();
  }
})();
