"""Simple dashboard — a decision-first, plain-language face of the GEX engine (paper only).

A stripped-down alternate to app/dashboard.py for a non-technical trader: market mood, the
expected day range IN POINTS (tiered by the |vanna|/charm bucket), today's range so far, the
key levels, one 'what fits this regime' line, a simplified positioning footprint, the futures
OI build-up in TWO windows (since-open + rolling last-30-min), and — on expiry days — the
gamma-blast crossover verdict (True-run score, OI change, call/put crossover). Reuses the same
engine as the main dashboard (load_dashboard, the vol-regime gauge, oi_footprint, futures_buildup,
gammablast); it only changes the *presentation*. Docs: docs/simple-dashboard.md.

  streamlit run app/simple_dashboard.py

⚠️ Day high/low (range + blast-compression) come from spot SNAPSHOTS at the refresh cadence, so
they MISS intraday ticks and UNDERSTATE the true range — see docs/simple-dashboard.md. A future
Dhan websocket feed closes this. Decision-support only — the human confirms every trade.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from app.data_access import load_dashboard  # noqa: E402

st.set_page_config(page_title="Simple GEX", page_icon="⚡", layout="wide")

# ── design tokens + layout (ported from the approved mockup) ────────────────────────────────
CSS = """
<style>
:root{
  --ground:#f4f6f9;--surface:#fff;--surface-2:#eef1f6;--inset:#e9edf3;--ink:#111621;--ink-2:#4b5567;
  --ink-mute:#7a8494;--line:#e0e5ec;--spark:#5b54d6;--go:#0f9d6b;--go-bg:#e4f6ee;--warn:#c07d10;
  --warn-bg:#fbf0da;--stop:#cf3f4b;--stop-bg:#fbe6e8;--call:#2f80d8;--put:#b5620c;
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
  --r:14px;--shadow:0 1px 2px rgba(16,22,34,.05),0 8px 24px rgba(16,22,34,.06);
}
@media (prefers-color-scheme:dark){:root{
  --ground:#0c0f16;--surface:#161a22;--surface-2:#1c222d;--inset:#212836;--ink:#eef2f8;--ink-2:#aab4c4;
  --ink-mute:#6d7889;--line:#28303d;--spark:#8e86f7;--go:#2cd49b;--go-bg:#0f2a24;--warn:#f0a83a;
  --warn-bg:#2c2210;--stop:#f0616d;--stop-bg:#2c1418;--call:#3f8fe0;--put:#d17720;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);}}
.stApp{background:var(--ground)}
header[data-testid="stHeader"]{background:transparent}
.simp{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--ink);max-width:980px;margin:0 auto}
.simp .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.simp .grid2 .col{min-width:0}
@media(max-width:760px){.simp .grid2{grid-template-columns:1fr}}
.simp .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.simp .eyebrow{font-size:.68rem;font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--ink-mute)}
.simp .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);margin-top:14px}
.simp h2.lbl{font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-mute);margin:16px 0 6px 2px}
.simp .gauge{display:grid;grid-template-columns:auto 1fr;gap:16px;align-items:center;padding:16px 18px}
.simp .spot .val{font-size:2.1rem;font-weight:700;letter-spacing:-.01em;line-height:1}
.simp .spot .chg{font-size:.82rem;color:var(--ink-mute);margin-top:3px}
.simp .mood{display:flex;flex-direction:column;gap:8px;border-left:1px solid var(--line);padding-left:16px}
.simp .chip{display:inline-flex;align-items:center;gap:7px;font-size:.82rem;font-weight:600;padding:5px 11px;border-radius:999px;width:max-content}
.simp .chip .dot{width:9px;height:9px;border-radius:50%}
.simp .chip.calm{background:var(--go-bg);color:var(--go)}.simp .chip.calm .dot{background:var(--go)}
.simp .chip.wild{background:var(--warn-bg);color:var(--warn)}.simp .chip.wild .dot{background:var(--warn)}
.simp .chip.mid{background:var(--surface-2);color:var(--ink-2)}.simp .chip.mid .dot{background:var(--ink-mute)}
.simp .range{font-size:.9rem;color:var(--ink-2)}.simp .range b{color:var(--ink);font-weight:700}
.simp .range .tier{font-size:.68rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-mute)}
.simp .rangebar{padding:13px 16px;display:flex;flex-direction:column;gap:8px}
.simp .rb-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.simp .rb-vals{font-size:.86rem;color:var(--ink-2)}.simp .rb-vals b{color:var(--ink);font-weight:700}
.simp .rb-track{height:8px;border-radius:999px;background:var(--inset);overflow:hidden}
.simp .rb-fill{height:100%;border-radius:999px;background:var(--spark)}
.simp .rb-note{font-size:.78rem;color:var(--go);font-weight:600}
.simp .rb-cav{font-size:.72rem;color:var(--ink-mute)}
.simp .news{display:flex;gap:11px;align-items:flex-start;padding:12px 15px;border-radius:12px;background:var(--warn-bg);border:1px solid var(--warn);margin-top:14px}
.simp .news .txt{font-size:.86rem}.simp .news .txt b{color:var(--warn)}
.simp .hero{padding:18px}
.simp .verdict{display:flex;align-items:center;gap:12px;margin:8px 0 4px}
.simp .verdict .big{font-size:2.05rem;font-weight:800;letter-spacing:-.02em;line-height:1}
.simp .verdict .ic{font-size:1.6rem}
.simp .lean-warn .big{color:var(--warn)}.simp .lean-go .big{color:var(--go)}.simp .lean-stop .big{color:var(--stop)}
.simp .instruct{font-size:.98rem;margin:6px 0 2px;max-width:52ch}
.simp .subnote{font-size:.8rem;color:var(--ink-mute);margin-top:6px}
.simp .meter{margin:15px 0 4px}
.simp .meter .head{display:flex;justify-content:space-between;align-items:baseline;font-size:.78rem;color:var(--ink-mute);margin-bottom:6px}
.simp .meter .head b{color:var(--ink);font-size:.92rem;font-weight:700}
.simp .track{height:9px;border-radius:999px;background:var(--inset);position:relative;overflow:hidden}
.simp .track .fill{position:absolute;inset:0 auto 0 0;border-radius:999px}
.simp .ticks{display:flex;justify-content:space-between;font-size:.64rem;color:var(--ink-mute);margin-top:5px;text-transform:uppercase}
.simp .blast-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:13px}
.simp .blast-stats>div{background:var(--surface-2);border-radius:10px;padding:9px 12px}
.simp .blast-stats small{font-size:.68rem;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.04em}
.simp .blast-stats b{display:block;font-size:1.05rem;font-weight:700;margin:2px 0 1px}
.simp .blast-stats b.down{color:var(--stop)}
.simp .blast-stats em{font-size:.72rem;color:var(--ink-mute);font-style:normal}
.simp figure{margin:16px 0 0}
.simp .chart-cap{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.simp .chart-cap .t{font-size:.78rem;color:var(--ink-2);font-weight:600}
.simp .legend{display:flex;gap:14px;font-size:.74rem;color:var(--ink-mute)}
.simp .legend span{display:inline-flex;align-items:center;gap:5px}
.simp .legend i{width:15px;height:2.5px;border-radius:2px;display:inline-block}
.simp svg{display:block;width:100%;height:auto;overflow:visible}
.simp .strikes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.simp .strike{padding:14px 15px;margin-top:0}
.simp .strike .role{font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-mute)}
.simp .strike .k{font-size:1.35rem;font-weight:700;margin-top:1px}
.simp .cp{display:flex;gap:14px;margin-top:10px}.simp .cp div{font-size:.78rem;color:var(--ink-mute)}
.simp .cp b{display:block;font-size:1.02rem;color:var(--ink);font-weight:700}
.simp .cp .c b{color:var(--call)}.simp .cp .p b{color:var(--put)}
.simp .state{margin-top:11px;font-size:.78rem;font-weight:600;padding:4px 9px;border-radius:8px;width:max-content;background:var(--surface-2);color:var(--ink-2)}
.simp .state.cross{background:var(--warn-bg);color:var(--warn)}
.simp .play{padding:16px 18px}.simp .play .eyebrow{color:var(--spark)}
.simp .play .verdict-sm{font-size:1.4rem;font-weight:800;margin:7px 0 5px}
.simp .play p{font-size:.92rem;color:var(--ink-2);margin:0 0 10px;max-width:52ch}
.simp .play .play-exit{font-size:.8rem;color:var(--ink-mute);border-top:1px solid var(--line);padding-top:9px}
.simp .levels{padding:2px 0}
.simp .lvl{display:grid;grid-template-columns:26px 1fr auto;gap:10px;align-items:center;padding:9px 15px;border-bottom:1px solid var(--line)}
.simp .lvl:last-child{border-bottom:0}.simp .lvl .ic{text-align:center}
.simp .lvl .name{font-size:.9rem;color:var(--ink-2)}.simp .lvl .name small{color:var(--ink-mute);font-size:.76rem}
.simp .lvl .px{font-size:1.02rem;font-weight:700}
.simp .lvl.now{background:var(--surface-2)}.simp .lvl.now .name{color:var(--ink);font-weight:700}
.simp .lvl.ceil .px{color:var(--stop)}.simp .lvl.floor .px{color:var(--go)}
.simp .foot-note{font-size:.8rem;color:var(--ink-mute);padding:11px 15px 4px}
.simp .fp-row{display:grid;grid-template-columns:1fr auto auto;gap:10px;align-items:baseline;padding:10px 15px;border-bottom:1px solid var(--line)}
.simp .fp-row>span{font-size:.9rem;color:var(--ink-2)}
.simp .fp-row b{font-size:1.02rem;font-weight:700;font-family:var(--mono)}
.simp .fp-row small{font-size:.74rem;color:var(--ink-mute)}
.simp .fp-row.ceil b{color:var(--stop)}.simp .fp-row.floor b{color:var(--go)}
.simp .fp-sum{font-size:.86rem;padding:11px 15px 5px}.simp .fp-sum b{color:var(--warn)}
.simp .fut{padding:15px 16px}
.simp .fut-windows{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px}
.simp .fw{background:var(--surface-2);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:7px;align-items:flex-start}
.simp .fw-lbl{font-size:.66rem;color:var(--ink-mute);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.simp .fut-badge{font-size:.82rem;font-weight:700;padding:5px 12px;border-radius:999px;white-space:nowrap}
.simp .fut-badge.bear{background:var(--stop-bg);color:var(--stop)}
.simp .fut-badge.bull{background:var(--go-bg);color:var(--go)}
.simp .fut-badge.cover{background:var(--warn-bg);color:var(--warn)}
.simp .fw-sub{font-size:.76rem;color:var(--ink-mute)}
.simp .fut-desc{font-size:.9rem;color:var(--ink-2);margin:10px 0 12px;max-width:52ch}.simp .fut-desc b{color:var(--ink)}
.simp .fut-formula{font-size:.78rem;color:var(--ink-mute)}
.simp .winstrip{margin-top:10px;border-top:1px solid var(--line)}
.simp .win-hd{font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-mute);padding:9px 15px 3px}
.simp .winrow{display:grid;grid-template-columns:44px 1fr auto;gap:10px;align-items:center;padding:6px 15px;border-bottom:1px solid var(--line)}
.simp .winrow:last-child{border-bottom:0}
.simp .winrow .wl{font-size:.76rem;color:var(--ink-2);font-weight:700;font-family:var(--mono)}
.simp .winrow.dim{opacity:.55}
.simp .winrow .wd{font-size:.76rem;color:var(--ink-mute);font-family:var(--mono);text-align:right;white-space:nowrap}
.simp .wbadge{font-size:.76rem;font-weight:700;padding:3px 10px;border-radius:999px;width:max-content}
.simp .wbadge.bull{background:var(--go-bg);color:var(--go)}
.simp .wbadge.bear{background:var(--stop-bg);color:var(--stop)}
.simp .wbadge.cover{background:var(--warn-bg);color:var(--warn)}
.simp .wbadge.flat{background:var(--surface-2);color:var(--ink-2)}
.simp .win-cap{font-size:.72rem;color:var(--ink-mute);padding:8px 15px 4px}
.simp .win-cap.warn{color:var(--warn)}
.simp .foot{margin-top:22px;font-size:.76rem;color:var(--ink-mute);text-align:center;line-height:1.6}
@media (max-width:430px){.simp .gauge{grid-template-columns:1fr}.simp .mood{border-left:0;padding-left:0}.simp .strikes,.simp .fut-windows{grid-template-columns:1fr}}
</style>
"""

_MOOD = {"calm": ("calm", "🟢 Calm &amp; range-bound"),
         "wild": ("wild", "🟧 Wild — wider swings"),
         "mid": ("mid", "⬜ Normal / mixed")}
_PLAY = {
    "calm": ("🎯 Sell premium near the walls",
             "Quiet, pinning regime — fade extremes back toward the magnet and sell premium near the "
             "walls. You can size up a little; keep stops just outside the walls."),
    "wild": ("🚀 Trade the break, buy convexity",
             "Trending regime — respect a wall break and don't fade it. Buy options for convexity "
             "rather than selling premium; stops beyond the walls; size down (bigger swings)."),
    "mid": ("🕒 No trade — wait",
            "Neutral, unclear regime — no clear edge either way. De-size and wait for the market to "
            "pick a side before selling or buying premium."),
}


_WINDOWS = [("60m", 60), ("30m", 30), ("15m", 15), ("5m", 5)]   # trailing rolling windows


def f(x, nd=0):
    return f"{x:,.{nd}f}" if x is not None and x == x else "—"


def _svg_crossover(series, strike):
    """Small call-vs-put premium crossover chart from the accumulated anchored-strike series."""
    pts = [(s["call"], s["put"]) for s in series if s.get("call") == s.get("call")
           and s.get("put") == s.get("put") and s.get("call") is not None and s.get("put") is not None]
    if len(pts) < 2:
        return ""
    calls = [c for c, _ in pts]
    puts = [p for _, p in pts]
    lo = min(min(calls), min(puts))
    hi = max(max(calls), max(puts))
    rng = (hi - lo) or 1.0
    n = len(pts)
    W, H, x0, x1, yT, yB = 520, 150, 34, 500, 20, 122

    def xy(i, v):
        x = x0 + (x1 - x0) * (i / (n - 1))
        y = yB - (yB - yT) * ((v - lo) / rng)
        return f"{x:.0f},{y:.0f}"
    cline = " ".join(xy(i, c) for i, c in enumerate(calls))
    pline = " ".join(xy(i, p) for i, p in enumerate(puts))
    ce, pe = calls[-1], puts[-1]
    return f"""
    <figure><div class="chart-cap"><span class="t">Call vs Put premium · strike {strike:,.0f}</span>
      <span class="legend"><span><i style="background:var(--call)"></i>Call</span>
      <span><i style="background:var(--put)"></i>Put</span></span></div>
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Call vs put premium over the session for strike {strike:.0f}.">
      <line x1="{x0}" y1="{yB}" x2="{x1}" y2="{yB}" stroke="var(--line)"/>
      <polyline fill="none" stroke="var(--put)" stroke-width="2.5" stroke-linejoin="round" points="{pline}"/>
      <polyline fill="none" stroke="var(--call)" stroke-width="2.5" stroke-linejoin="round" points="{cline}"/>
      <circle cx="{x1}" cy="{yB-(yB-yT)*((ce-lo)/rng):.0f}" r="4" fill="var(--call)"/>
      <circle cx="{x1}" cy="{yB-(yB-yT)*((pe-lo)/rng):.0f}" r="4" fill="var(--put)"/>
      <text x="{x1+8}" y="{yB-(yB-yT)*((ce-lo)/rng):.0f}" font-size="11" fill="var(--call)" font-weight="700" class="mono">{ce:.0f}</text>
      <text x="{x1+8}" y="{yB-(yB-yT)*((pe-lo)/rng)+11:.0f}" font-size="11" fill="var(--put)" font-weight="700" class="mono">{pe:.0f}</text>
    </svg></figure>"""


def _futures_window(day_df, minutes=30):
    """(label, badge_class, dprice, doi) for the rolling last-``minutes`` futures window, or None."""
    from app.futures_buildup import classify_futures_buildup
    if day_df is None or getattr(day_df, "empty", True):
        return None
    df = day_df.sort_values("time_ist").drop_duplicates("time_ist").reset_index(drop=True)
    if len(df) < 2:
        return None

    def _hm(t):
        try:
            h, m = str(t).split(":"); return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return None
    last = df.iloc[-1]
    tnow = _hm(last["time_ist"])
    prior = df[df["time_ist"].apply(lambda t: (_hm(t) or 0) <= (tnow or 0) - minutes)]
    ref = prior.iloc[-1] if not prior.empty else df.iloc[0]

    def g(r, k):
        v = pd.to_numeric(r.get(k), errors="coerce")
        return float(v) if v == v else None
    p0, p1 = g(ref, "price"), g(last, "price")
    o0, o1 = g(ref, "agg_oi"), g(last, "agg_oi")
    dprice = (p1 - p0) if (p0 is not None and p1 is not None) else None
    doi = (o1 - o0) if (o0 is not None and o1 is not None) else None
    label = classify_futures_buildup(dprice, doi)
    return label, dprice, doi


def _fut_badge(label):
    cls = {"long build-up": "bull", "short build-up": "bear",
           "short covering": "cover", "long unwinding": "cover"}.get(label, "cover")
    ic = {"long build-up": "🐂", "short build-up": "🐻",
          "short covering": "🔄", "long unwinding": "📉"}.get(label, "•")
    return cls, ic, (label or "—").title()


def main():
    with st.sidebar:
        st.header("Simple view")
        symbols = list(config.INDICES)
        symbol = st.selectbox("Index", symbols, index=symbols.index(config.DEFAULT_INDEX),
                              format_func=lambda s: config.INDICES[s].label)
        use_demo = st.checkbox("Demo data (offline)", value=False)
        refresh = st.button("🔄 Refresh", type="primary", width="stretch")
        auto = st.checkbox("⏱️ Auto-refresh", value=False)
        auto_mins = st.selectbox("Interval", [1, 3, 5], index=1, format_func=lambda m: f"{m} min",
                                 disabled=not auto)
        st.divider()
        st.caption("⚡ Gamma-blast")
        preview_expiry = st.checkbox("👁️ Preview expiry mode", value=False,
                                     help="Force the gamma-blast view on a non-expiry day, to eyeball it.")
        anchor_now = st.button("📍 Anchor blast now", width="stretch",
                               help="Freeze the blast strikes at the current moment instead of "
                                    "waiting for the 13:45 IST auto-anchor.")
        st.caption("A simple, paper-only face of the GEX engine. See docs/simple-dashboard.md.")

    if auto:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=auto_mins * 60_000, key="simp_tick")
        except ModuleNotFoundError:
            st.sidebar.warning("Install streamlit-autorefresh for auto mode.")

    st.markdown(CSS, unsafe_allow_html=True)
    idx = config.get_index(symbol)

    if symbol != st.session_state.get("simp_symbol"):
        st.session_state["simp_symbol"] = symbol
        st.session_state.pop("simp_data", None)
        st.session_state.pop("simp_hl", None)
        st.session_state.pop("simp_blast", None)

    if refresh or st.session_state.get("simp_data") is None or auto:
        try:
            st.session_state["simp_data"] = load_dashboard(None, use_demo, symbol)
            st.session_state["simp_err"] = None
        except Exception as e:  # noqa: BLE001
            st.session_state["simp_err"] = str(e)
    data = st.session_state.get("simp_data")
    if data is None:
        st.error(f"Could not load data. {st.session_state.get('simp_err','')}")
        st.stop()

    L = data.levels
    ist = data.fetched_at + timedelta(hours=5, minutes=30)
    nearest = data.expiries[0] if data.expiries else None
    is_expiry = (nearest == ist.date()) if nearest else False
    show_blast = is_expiry or preview_expiry          # preview forces the blast view (dev)
    if anchor_now:
        st.session_state["simp_force_anchor"] = True

    # regime mood + expected day range (points)
    from app.intraday_features import regime_now, regime_daily_move_now
    try:
        vr = regime_now(symbol, data.vanna_now, data.charm_now, data.dte_now)
        dm = regime_daily_move_now(symbol, data.vanna_now, data.charm_now, data.dte_now)
    except Exception:  # noqa: BLE001
        vr = dm = None
    mood_cls, mood_txt = _MOOD.get(vr["tag"] if vr else "mid", _MOOD["mid"])
    exp_pts = (dm["hl_pct"] / 100.0 * L.spot) if (dm and dm.get("hl_pct") == dm.get("hl_pct")) else None
    tier = {"calm": "Calm (mid)", "wild": "Wild", "mid": "Normal"}.get(vr["tag"], "—") if vr else "—"

    # today's range so far (snapshot-derived — see caveat)
    hl = st.session_state.setdefault("simp_hl", {"lo": L.spot, "hi": L.spot, "open": L.spot})
    hl["lo"] = min(hl["lo"], L.spot); hl["hi"] = max(hl["hi"], L.spot)
    span = hl["hi"] - hl["lo"]
    used = (span / exp_pts * 100.0) if exp_pts else 0.0

    vix = data.india_vix or {}
    vtxt = (f"VIX {vix['last']:.1f}" + (f" · {vix['pct_change']:+.1f}%" if vix.get('pct_change') is not None else "")
            ) if vix else ""

    # ── render ──
    H = ['<div class="simp">']
    H.append('<div class="eyebrow" style="color:var(--spark)">'
             f'{idx.label} · Gamma-Blast</div>'
             f'<div class="mono" style="font-size:.8rem;color:var(--ink-mute)">{ist:%a %d %b · %H:%M IST}'
             f'{" · ⚡ EXPIRY DAY" if is_expiry else (" · 👁️ preview" if preview_expiry else "")}</div>')

    # gauge
    rng_line = (f'<span class="tier">Vanna tier: {tier}</span><br>Expected day range '
                f'<b class="mono">≈ {f(exp_pts)} pts</b>' if exp_pts else
                '<span class="tier">Expected range</span><br>building…')
    H.append(f'''<div class="gauge card"><div class="spot"><div class="eyebrow">Spot</div>
      <div class="val mono">{f(L.spot)}</div><div class="chg mono">{vtxt}</div></div>
      <div class="mood"><span class="chip {mood_cls}"><span class="dot"></span>{mood_txt}</span>
      <div class="range">{rng_line}</div></div></div>''')

    # today's range so far
    note = ""
    if is_expiry and exp_pts and L.spot:
        pct = span / L.spot * 100.0
        note = (f'<span class="rb-note">🎯 Still tight ({pct:.1f}% so far) — blast-friendly if a cross fires.</span>'
                if pct < 1.0 else f'<span class="rb-cav">Range already {pct:.1f}% — less blast-friendly.</span>')
    H.append(f'''<div class="rangebar card"><div class="rb-top"><span class="eyebrow">Today's range so far</span>
      <span class="rb-vals mono">{f(hl["lo"])} – {f(hl["hi"])} · <b>{f(span)} pts</b>{f" of ~{f(exp_pts)}" if exp_pts else ""}</span></div>
      <div class="rb-track"><div class="rb-fill" style="width:{min(used,100):.0f}%"></div></div>
      {note}<span class="rb-cav">≈ from {ist:%H:%M} snapshots — can miss fast spikes (true range may be wider).</span></div>''')

    # news override
    news = data.news or {}
    if news.get("impact", 0) and news["impact"] >= 5:
        top = (news.get("top") or "").strip()
        H.append(f'<div class="news"><span>📰</span><div class="txt"><b>Big news day.</b> '
                 f'A headline is driving the market — trust the news over these levels today.'
                 f'{" <i>“"+top[:90]+"”</i>" if top else ""}</div></div>')

    # ── 2-column deck: LEFT = decision (blast/play) + levels, RIGHT = positioning ──
    H.append('<div class="grid2"><div class="col">')
    if show_blast:
        H.append(_blast_hero(data, L, ist))
    else:
        head, body = _PLAY.get(vr["tag"] if vr else "mid", _PLAY["mid"])
        H.append(f'''<div class="card play"><span class="eyebrow">Suggested play · paper only</span>
          <div class="verdict-sm">{head}</div><p>{body}</p>
          <div class="play-exit">Illustrative — you decide. Regime is validated; the specific trade is textbook, not advice.</div></div>''')

    # levels ladder
    H.append('<h2 class="lbl">The map — key levels</h2><div class="levels card">')
    rows = [("ceil", "🔴", "Ceiling", "resistance · call wall", L.call_wall),
            ("", "🧲", "Magnet", "where it's pulled", L.magnet),
            ("", "🎯", "Expiry pin", "max pain", L.max_pain),
            ("now", "▶", "Now", "", L.spot),
            ("floor", "🟢", "Floor", "support · put wall", L.put_wall)]
    for cls, ic, name, sub, px in rows:
        H.append(f'<div class="lvl {cls}"><span class="ic">{ic}</span>'
                 f'<span class="name">{name}{f" <small>{sub}</small>" if sub else ""}</span>'
                 f'<span class="px mono">{f(px)}</span></div>')
    band = f'{f(min(L.put_wall,L.call_wall))} – {f(max(L.put_wall,L.call_wall))}'
    H.append(f'<div class="foot-note">Likely to stay inside <b class="mono">{band}</b>. '
             'Put stops <i>just beyond</i> the ceiling or floor — never at the magnet.</div></div>')

    # right column: positioning (footprint + futures two-window)
    H.append('</div><div class="col">')
    H.append('<h2 class="lbl">Where the money is building</h2>')
    H.append(_footprint_html(data, L, idx, ist, is_expiry))
    H.append(_futures_html(data, symbol, ist, is_expiry))
    H.append('</div></div>')                          # close right col + grid

    H.append(f'<div class="foot">A map and a logbook, not an oracle. <b>Paper trading only</b> — '
             f'you confirm every trade.<br><span class="mono" style="opacity:.7">lot {idx.lot_size} · '
             f'source {data.source} · {ist:%H:%M IST}</span></div>')
    H.append('</div>')
    st.markdown("\n".join(H), unsafe_allow_html=True)


def _blast_hero(data, L, ist):
    """Expiry-day gamma-blast verdict, reusing app.gammablast with session-accumulated series."""
    try:
        from app.gammablast import (blast_score, crossover_state, detect_crossovers,
                                    next_strike, pick_anchors, strike_oi, strike_premiums)
    except Exception:  # noqa: BLE001
        return '<div class="card hero"><div class="eyebrow">Gamma-blast</div><p class="instruct">Blast engine unavailable.</p></div>'
    if data.source != "nse" or not data.oi_strikes:
        return ('<div class="card hero"><span class="eyebrow">Gamma-blast</span>'
                '<p class="instruct">Live NSE only — per-strike premiums aren\'t on demo data.</p></div>')

    ist_today = ist.strftime("%Y-%m-%d")
    anchor_min = 13 * 60 + 45
    ist_min = ist.hour * 60 + ist.minute
    bl = st.session_state.get("simp_blast")
    if not bl or bl.get("date") != ist_today:
        bl = {"date": ist_today, "anchors": None, "points": {}}
        st.session_state["simp_blast"] = bl
    force = st.session_state.pop("simp_force_anchor", False)   # manual "Anchor blast now" button
    if force:
        bl["anchors"] = {**pick_anchors(data.oi_strikes, L.spot, L.magnet), "at": ist.strftime("%H:%M")}
        bl["points"] = {}                                       # strikes changed → reset the series
    elif bl["anchors"] is None and ist_min >= anchor_min:
        bl["anchors"] = {**pick_anchors(data.oi_strikes, L.spot, L.magnet), "at": ist.strftime("%H:%M")}
    if bl["anchors"] is None:
        return ('<div class="card hero"><span class="eyebrow">Gamma-blast · watching</span>'
                '<p class="instruct">Anchors freeze automatically at <b>13:45 IST</b> (strongest window). '
                'Until then, watch the levels and range above.</p></div>')

    a = bl["anchors"]
    ac, ap = strike_premiums(data.oi_strikes, a["atm"])
    mc, mp = strike_premiums(data.oi_strikes, a["magnet"])
    aco, apo = strike_oi(data.oi_strikes, a["atm"])
    mco, mpo = strike_oi(data.oi_strikes, a["magnet"])
    bl["points"][ist.strftime("%H:%M")] = {"atm_call": ac, "atm_put": ap, "magnet_call": mc,
                                           "magnet_put": mp, "atm_coi": aco, "atm_poi": apo}
    series = [{"time_ist": t, "call": v.get("atm_call"), "put": v.get("atm_put")}
              for t, v in sorted(bl["points"].items())]
    evs = detect_crossovers(series)

    K = a["atm"]
    if not evs:
        chart = _svg_crossover(series, K)
        return (f'<div class="card hero"><span class="eyebrow">Gamma-blast · watching {K:,.0f}</span>'
                f'<div class="verdict lean-warn"><span class="ic">👀</span><span class="big">No cross yet</span></div>'
                f'<p class="instruct">Watching the ATM strike <b class="mono">{K:,.0f}</b> — a crossover fires '
                f'when spot crosses it. No blast setup until then.</p>{chart}</div>')

    last = evs[-1]
    direction = "up" if last["to"] == "call_rich" else "down"
    win = [(t, v) for t, v in sorted(bl["points"].items()) if t >= last["time_ist"]]
    crossed = [{"call_oi": v.get("atm_coi"), "put_oi": v.get("atm_poi"),
                "call_ltp": v.get("atm_call"), "put_ltp": v.get("atm_put")} for _, v in win]
    ahead = None
    nb = next_strike(data.oi_strikes, K, direction)
    if nb is not None:
        nco, npo = strike_oi(data.oi_strikes, nb)
        ahead = [{"call_oi": nco, "put_oi": npo, "call_ltp": None, "put_ltp": None}]
    sc = blast_score(direction, crossed, ahead) or {}
    score = sc.get("score", 0)
    grade = sc.get("grade", "")
    run = grade.startswith("TRUE")
    lean = "lean-go" if run else "lean-warn"
    ic = "🚀" if run else "🪤"
    word = "Likely RUN" if run else "Likely FAKE"
    dword = "up" if direction == "up" else "down"
    if run:
        instr = (f'<b>The break is sustaining.</b> Spot crossed {dword} through <b class="mono">{K:,.0f}</b> '
                 f'and writers are covering (fuel). Bias: continuation {dword}. Confirmation, not a fresh entry — act on your plan.')
    else:
        instr = (f'<b>Don\'t chase the {dword}-move.</b> Spot crossed {dword} through <b class="mono">{K:,.0f}</b>, '
                 f'but the score is weak / a wall is likely to cap it. More likely it stalls and slips back toward '
                 f'<b class="mono">{K:,.0f}</b>. If you lean, <i>fade</i> it, small size.')
    cover = sc.get("cover_frac")
    wr = sc.get("wall_ratio")
    fillc = "var(--go)" if run else "var(--warn)"
    chart = _svg_crossover(series, K)
    return f'''<div class="card hero {lean}"><div style="display:flex;justify-content:space-between">
      <span class="eyebrow">Gamma-blast verdict · {K:,.0f}</span>
      <span class="subnote mono">cross {last["time_ist"]}</span></div>
      <div class="verdict"><span class="ic">{ic}</span><span class="big">{word}</span></div>
      <p class="instruct">{instr}</p>
      <div class="meter"><div class="head"><span>True-run score</span>
        <span><b class="mono">{score}</b> / 100 · {grade}</span></div>
        <div class="track"><div class="fill" style="width:{max(3,min(score,100))}%;background:{fillc}"></div></div>
        <div class="ticks"><span>Fake / pin</span><span>Toss-up</span><span>Real run</span></div></div>
      <div class="blast-stats">
        <div><small>{sc.get("itm_side","")}-writer OI change</small>
          <b class="mono">{f(cover*100,0) if cover is not None else "—"}%</b><em>−ve = covering (fuel)</em></div>
        <div><small>Wall ahead</small><b class="down mono">{f(wr,1)+"×" if wr is not None else "—"}</b><em>cap overhead</em></div>
      </div>{chart}</div>'''


def _opt_window_row(lbl, minutes, w, is_expiry):
    """One row of the options rolling-window strip (60/30/15/5-min current-expiry build)."""
    if w is None:
        return (f'<div class="winrow dim"><span class="wl">{lbl}</span>'
                '<span class="wbadge flat">— building…</span><span class="wd"></span></div>')
    cls, txt = {"put-writing": ("bull", "🟢 Puts → support"),
                "call-writing": ("bear", "🔴 Calls → resistance"),
                "balanced": ("flat", "· Balanced"),
                "thin": ("flat", "· Thin build")}.get(w["label"], ("flat", "· —"))
    net = w.get("net") or 0.0
    dtxt = (f'net {"+" if net >= 0 else "−"}{f(abs(net))}'
            if w["label"] in ("put-writing", "call-writing") else "")
    dim = " dim" if (is_expiry and minutes <= 15) else ""     # short windows churn on expiry
    shown = ("≤" + lbl) if w.get("partial") else lbl
    return (f'<div class="winrow{dim}"><span class="wl">{shown}</span>'
            f'<span class="wbadge {cls}">{txt}</span><span class="wd">{dtxt}</span></div>')


def _footprint_html(data, L, idx, ist, is_expiry):
    from app.oi_footprint import build_footprint, window_footprint
    from app.timeseries import load_strikes_day
    if data.source != "nse" or not data.oi_strikes:
        return ''
    band = getattr(idx, "band_pts", None)
    fp = build_footprint(data.oi_strikes, L.spot, band)
    if not fp:
        return ''
    skew = fp.get("net_skew", 0.0)
    who = "call-writing" if skew < 0 else "put-writing"
    sub = ("big players capping the upside" if skew < 0 else "big players supporting the downside")

    # rolling-window strip (current expiry only, from today's per-strike captures)
    day_df = load_strikes_day(ist.strftime("%Y-%m-%d"), idx.symbol)
    now_hm = ist.hour * 60 + ist.minute
    rows = "".join(_opt_window_row(lbl, m,
                   window_footprint(day_df, m, L.spot, band, now_hm=now_hm), is_expiry)
                   for lbl, m in _WINDOWS)
    cap = ('⚠ Near expiry the 5/15-min reads churn (settlement) — lean on 30/60-min.'
           if is_expiry else 'Fresh writing near spot; adds only, current expiry. Context, not a prediction.')
    strip = (f'<div class="winstrip"><div class="win-hd">Fresh writing — trailing windows</div>'
             f'{rows}<div class="win-cap{" warn" if is_expiry else ""}">{cap}</div></div>')

    return f'''<div class="card"><div class="levels">
      <div class="fp-row floor"><span>🟢 Support forming</span><b class="mono">{f(fp.get("support_build"))}</b><small>put writers</small></div>
      <div class="fp-row ceil"><span>🔴 Resistance forming</span><b class="mono">{f(fp.get("resistance_build"))}</b><small>call writers</small></div>
      <div class="fp-sum">Net <b>{who} near spot</b> — {sub}.</div></div>{strip}</div>'''


def _fut_window_row(lbl, minutes, win, is_expiry):
    """One row of the futures rolling-window strip (price×ΔOI over the trailing window)."""
    if win is None:
        return (f'<div class="winrow dim"><span class="wl">{lbl}</span>'
                '<span class="wbadge flat">— building…</span><span class="wd"></span></div>')
    label, dp, doi = win
    cls, icn, lab = _fut_badge(label)
    dtxt = (f'P {"▲" if (dp or 0) >= 0 else "▼"}{f(abs(dp)) if dp is not None else "—"} · '
            f'OI {"▲" if (doi or 0) >= 0 else "▼"}{f(abs(doi)) if doi is not None else "—"}')
    dim = " dim" if (is_expiry and minutes <= 15) else ""
    return (f'<div class="winrow{dim}"><span class="wl">{lbl}</span>'
            f'<span class="wbadge {cls}">{icn} {lab}</span><span class="wd">{dtxt}</span></div>')


def _futures_html(data, symbol, ist, is_expiry):
    from app.futures_buildup import (capture_futures_snapshot, intraday_futures_buildup,
                                    load_futures_intraday_day)
    if data.source != "nse":
        return ''
    date_ist = ist.strftime("%Y-%m-%d")
    try:                                  # best-effort live snapshot, then read the day
        capture_futures_snapshot(symbol, min_gap_minutes=1.0)
    except Exception:  # noqa: BLE001
        pass
    day_df = load_futures_intraday_day(date_ist, symbol)
    since = intraday_futures_buildup(day_df)
    wins = {m: _futures_window(day_df, m) for _, m in _WINDOWS}
    if not since and not any(wins.values()):
        return ('<div class="card fut"><span class="eyebrow">Nifty futures OI build-up</span>'
                '<p class="fut-desc">Waiting for 2+ futures snapshots this session…</p></div>')
    since_cell = ''
    if since:
        cls, icn, lab = _fut_badge(since.get("buildup"))
        dp = since.get("dprice"); doi_pct = since.get("doi_pct")
        since_cell = (f'<div class="fw" style="margin-top:8px"><span class="fw-lbl">Since open</span>'
                      f'<span class="fut-badge {cls}">{icn} {lab}</span>'
                      f'<span class="fw-sub mono">Price {"▲" if (dp or 0)>=0 else "▼"}{f(abs(dp)) if dp is not None else "—"} · '
                      f'OI {f(doi_pct,1)+"%" if doi_pct is not None else "—"}</span></div>')
    rows = "".join(_fut_window_row(lbl, m, wins[m], is_expiry) for lbl, m in _WINDOWS)
    cap = ('⚠ Around monthly expiry the short windows churn (rollover) — lean on 30/60-min.'
           if is_expiry else 'Aggregate OI (rollover-netted); volume = intensity, not direction.')
    return f'''<div class="card fut"><span class="eyebrow">Nifty futures OI build-up</span>
      {since_cell}
      <div class="winstrip"><div class="win-hd">Trailing windows · price × ΔOI</div>{rows}
        <div class="win-cap{" warn" if is_expiry else ""}">{cap}</div></div></div>'''


if __name__ == "__main__":
    main()
