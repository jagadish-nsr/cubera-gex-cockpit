"""Build a self-contained HTML equity-curve chart from one or more strategy_curves JSONs (per book),
with a book toggle. Embeds data inline (Artifact-safe: no external fetch). Writes to the scratchpad."""
from __future__ import annotations
import json, sys
from pathlib import Path

SCR = Path(r"C:\Users\navee\AppData\Local\Temp\claude\C--Users-navee-Python-gex-indian-gex"
           r"\eb204e31-8169-4259-8f28-affcbf0aa699\scratchpad")
# (store-label, json path) pairs; default = NIFTY + BANKNIFTY
BOOKS_IN = [("NIFTY", SCR / "strategy_curves.json"), ("Bank Nifty", SCR / "strategy_curves_bank.json")]
OUT = SCR / "strategy_chart.html"

META = {
    "S1 Short straddle (every cycle)":     {"code": "S1", "hue": "amber"},
    "S2 Short straddle (high IV only)":    {"code": "S2", "hue": "teal"},
    "S3 Short straddle (SIDEWAYS regime)": {"code": "S3", "hue": "rose"},
    "S4 ORB breakout (directional)":       {"code": "S4", "hue": "violet"},
    "S5 Buy & hold Nifty":                 {"code": "S5", "hue": "sky"},
}
DISPLAY = {"S1": "Short straddle · every cycle", "S2": "Short straddle · high-IV only",
           "S3": "Short straddle · SIDEWAYS regime", "S4": "ORB breakout · directional",
           "S5": "Buy & hold index"}

# book-specific plain-language reads (accurate to each book's result)
NOTES = {
 "NIFTY": [
  ["teal","S2 is the only finding-backed edge","Sell the straddle only when implied vol is in its top third: +12% CAGR at half the drawdown of selling blindly — the variance-risk premium, richest exactly when IV is high. This is “sell expensive insurance”, not a GEX signal."],
  ["rose","S3 loses — the regime is not a timing rule","Selling in the SIDEWAYS (high-vanna) regime loses money: high vanna ≈ low IV, so you collect thin premium at the wrong moment. The vanna gauge forecasts realised vol (Study #17), but IV already prices it — you can’t monetise it by selling straddles."],
  ["amber","S1 barely beats cash, 48% drawdown","Selling every week is real but thin net of costs and brutally tail-risky. Short vol without an IV filter isn’t worth the ride."],
  ["violet","S4 is market drift, not an ORB edge","The breakout looks profitable only because a rising index breaks up more often — it tracks buy-and-hold. Study #16 removed the drift and the ORB signal was a coin flip. Lower drawdown only because it sits in cash overnight."],
 ],
 "Bank Nifty": [
  ["amber","Short vol is a LOSER on Bank Nifty","Selling every cycle: −20% CAGR, 77% drawdown — the classic short-gamma blow-up. Bank Nifty is MONTHLY-only (no weeklies since 2024), so the straddle is held ~a month and the fat-tailed index runs it over."],
  ["teal","The IV filter barely rescues it","Even selling only rich IV (S2) just breaks even (~0% CAGR) at a 44% drawdown, on only 19 trades. The Nifty-weekly “sell rich IV” edge does NOT survive Bank Nifty’s monthly hold + fatter tails — the one edge is horizon/index-specific."],
  ["rose","S3 loses worst","SIDEWAYS-regime selling is −26% — the same low-IV timing flaw, amplified by Bank Nifty’s tails."],
  ["violet","S4 is still drift","The ORB curve tracks buy-and-hold (market drift), not an ORB edge — same as Nifty."],
 ],
}


def summary(eq, dates):
    peak = eq[0]; mdd = 0.0
    for v in eq:
        peak = max(peak, v); mdd = max(mdd, (peak - v) / peak)
    yrs = (int(dates[-1][:4]) + int(dates[-1][5:7]) / 12) - (int(dates[0][:4]) + int(dates[0][5:7]) / 12)
    cagr = (eq[-1] / eq[0]) ** (1 / max(yrs, 0.5)) - 1
    trades = sum(1 for i in range(1, len(eq)) if eq[i] != eq[i - 1])
    return {"final": eq[-1], "cagr": cagr * 100, "mdd": mdd * 100, "trades": trades,
            "start": dates[0], "end": dates[-1]}


def thin(dates, eq, step):
    idx = list(range(0, len(eq), step))
    if idx[-1] != len(eq) - 1:
        idx.append(len(eq) - 1)
    return [[dates[i], round(eq[i])] for i in idx]


books = []
for label, path in BOOKS_IN:
    if not path.exists():
        continue
    curves = json.loads(path.read_text(encoding="utf-8"))["curves"]
    series = []
    for name, c in curves.items():
        eq, dates = c["equity"], c["dates"]
        if not eq:
            continue
        m = META[name]
        step = 5 if len(eq) > 400 else 1
        series.append({"code": m["code"], "label": DISPLAY[m["code"]], "hue": m["hue"],
                       "summary": summary(eq, dates), "pts": thin(dates, eq, step)})
    books.append({"store": label, "series": series, "notes": NOTES.get(label, NOTES["NIFTY"])})

payload = json.dumps({"books": books}, separators=(",", ":"))

HTML = r"""<title>Nifty & Bank Nifty strategy backtest — equity curves</title>
<style>
:root{
  --bg:#0d1017; --panel:#141a22; --panel2:#1a212b; --ink:#e7edf4; --ink2:#aeb9c6; --mut:#6d7a8a;
  --line:#232c38; --grid:#1c242f; --base:#8a97a6; --sel:#1f6feb22; --selb:#2b3948;
  --teal:#16c8b0; --amber:#f0a63a; --rose:#f6607f; --violet:#a78bfa; --sky:#38b6f0;
}
@media (prefers-color-scheme: light){:root{
  --bg:#eef2f6; --panel:#ffffff; --panel2:#f4f7fa; --ink:#141b24; --ink2:#3f4c5a; --mut:#75828f;
  --line:#e2e8ef; --grid:#eceff3; --base:#9aa6b3; --sel:#dcebff; --selb:#c7d6e6;
  --teal:#0c9a86; --amber:#c07d16; --rose:#e0335a; --violet:#7c5cf0; --sky:#0f86c4;
}}
:root[data-theme="dark"]{
  --bg:#0d1017; --panel:#141a22; --panel2:#1a212b; --ink:#e7edf4; --ink2:#aeb9c6; --mut:#6d7a8a;
  --line:#232c38; --grid:#1c242f; --base:#8a97a6; --sel:#1f6feb22; --selb:#2b3948;
  --teal:#16c8b0; --amber:#f0a63a; --rose:#f6607f; --violet:#a78bfa; --sky:#38b6f0;
}
:root[data-theme="light"]{
  --bg:#eef2f6; --panel:#ffffff; --panel2:#f4f7fa; --ink:#141b24; --ink2:#3f4c5a; --mut:#75828f;
  --line:#e2e8ef; --grid:#eceff3; --base:#9aa6b3; --sel:#dcebff; --selb:#c7d6e6;
  --teal:#0c9a86; --amber:#c07d16; --rose:#e0335a; --violet:#7c5cf0; --sky:#0f86c4;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:980px;margin:0 auto;padding:34px 22px 56px}
.eyebrow{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.18em;text-transform:uppercase;color:var(--mut)}
h1{font-size:27px;line-height:1.15;margin:12px 0 6px;font-weight:680;text-wrap:balance;letter-spacing:-.01em}
.lede{color:var(--ink2);font-size:15px;max-width:66ch;margin:0}
.mono{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums}
.toggle{display:inline-flex;gap:3px;margin:22px 0 2px;padding:3px;background:var(--panel);border:1px solid var(--line);border-radius:11px}
.toggle button{font:600 13px system-ui;color:var(--mut);background:none;border:0;padding:7px 16px;border-radius:8px;cursor:pointer}
.toggle button[aria-pressed="true"]{background:var(--sel);color:var(--ink);box-shadow:inset 0 0 0 1px var(--selb)}
.toggle button:focus-visible{outline:2px solid var(--sky);outline-offset:2px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px}
.tbl{margin:14px 0 8px;overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:560px}
th{font:600 10.5px/1 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);text-align:right;padding:0 14px 10px;border-bottom:1px solid var(--line)}
th.l,td.l{text-align:left}
td{padding:11px 14px;border-bottom:1px solid var(--grid);font-size:13.5px}
tr:last-child td{border-bottom:none}
.chip{display:inline-flex;align-items:center;gap:9px;font-weight:600}
.dot{width:11px;height:11px;border-radius:3px;flex:none}
.num{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;text-align:right}
.pos{color:var(--teal)} .neg{color:var(--rose)}
.chartcard{margin-top:16px;padding:18px 16px 10px}
.chart-h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:0 6px 4px}
.chart-h h2{font-size:14px;margin:0;font-weight:640} .chart-h span{color:var(--mut);font-size:12px}
svg{display:block;width:100%;height:auto;touch-action:none}
.gl{stroke:var(--grid);stroke-width:1}
.axl{fill:var(--mut);font:500 11px ui-monospace,monospace;font-variant-numeric:tabular-nums}
.baseln{stroke:var(--base);stroke-width:1;stroke-dasharray:3 4;opacity:.8}
.basel{fill:var(--base);font:600 10.5px ui-monospace,monospace;letter-spacing:.04em}
.ser{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.ser.hi{stroke-width:3}
.endlab{font:600 11px ui-monospace,monospace;font-variant-numeric:tabular-nums}
.cross{stroke:var(--mut);stroke-width:1;stroke-dasharray:2 3;opacity:0}
.tip{position:absolute;pointer-events:none;background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:9px 11px;font-size:12px;opacity:0;transition:opacity .1s;min-width:186px;box-shadow:0 8px 26px rgba(0,0,0,.28)}
.tip .d{color:var(--mut);font:600 10.5px ui-monospace,monospace;letter-spacing:.06em;margin-bottom:6px}
.tip .row{display:flex;justify-content:space-between;gap:14px;padding:1.5px 0}
.tip .row b{font-family:ui-monospace,monospace;font-variant-numeric:tabular-nums;font-weight:600}
.notes{margin-top:26px;display:grid;gap:12px}
.note{padding:13px 16px;border-left:3px solid var(--c);background:var(--panel);border-radius:0 11px 11px 0}
.note b{font-size:13.5px} .note p{margin:3px 0 0;color:var(--ink2);font-size:13px}
.foot{margin-top:26px;color:var(--mut);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <div class="eyebrow">Nifty GEX engine · situational-analysis findings</div>
  <h1>What actually made money — and what didn't</h1>
  <p class="lede">Five strategies drawn from the study set, each compounding a ₹10,000 stake over five
  years of 1-minute data. Every <em>directional</em> idea was dead; the only edge is selling premium
  <em>when implied vol is rich</em> — and even that is horizon-specific: it works on Nifty’s weeklies
  but not Bank Nifty’s monthly, fat-tailed straddles. Toggle the books to compare.</p>

  <div class="toggle" id="toggle" role="tablist"></div>

  <div class="tbl card" style="padding:6px 6px 2px">
    <table id="tbl"><thead><tr>
      <th class="l">Strategy</th><th>Final (₹10k→)</th><th>CAGR</th><th>Max DD</th>
      <th>Trades</th></tr></thead><tbody></tbody></table>
  </div>

  <div class="chartcard card">
    <div class="chart-h"><h2 id="ctitle">Equity curve</h2><span id="span"></span></div>
    <div style="position:relative">
      <svg id="svg" viewBox="0 0 920 460" preserveAspectRatio="none" aria-label="Equity curves"></svg>
      <div class="tip" id="tip"></div>
    </div>
  </div>

  <div class="notes" id="notes"></div>

  <div class="foot">Backtest is <b>gross</b> of real slippage, liquidity, and margin-call risk; short-vol
  curves carry a fat left tail (see max drawdown), and the Bank Nifty book rests on few trades (≤56).
  Sizing: straddles deploy 50% of equity on ~12% margin, 2%/premium cost; ORB unlevered. One straddle
  per expiry cycle, held to expiry. <b>Sign-free</b>, decision-support only — not financial advice.</div>
</div>

<script>
const HUE={amber:'--amber',teal:'--teal',rose:'--rose',violet:'--violet',sky:'--sky'};
const CV=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const D=__DATA__;
const inr=n=>'₹'+Math.round(n).toLocaleString('en-IN');
const pct=n=>(n>=0?'+':'')+n.toFixed(1)+'%';
const ts=s=>{const[y,m,d]=s.split('-').map(Number);return Date.UTC(y,m-1,d);};
const NS='http://www.w3.org/2000/svg';
const el=(t,a)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);return e;};
let cur=0;

/* book toggle */
const tg=document.querySelector('#toggle');
D.books.forEach((b,i)=>{const btn=document.createElement('button');btn.textContent=b.store;
  btn.setAttribute('role','tab');btn.setAttribute('aria-pressed',i===0);
  btn.onclick=()=>{cur=i;[...tg.children].forEach((c,j)=>c.setAttribute('aria-pressed',j===i));render();};
  tg.appendChild(btn);});

function render(){
  const B=D.books[cur],S=B.series;
  document.querySelector('#ctitle').textContent='Equity curve · '+B.store+' · ₹10,000 →';
  document.querySelector('#span').textContent=S[0].summary.start+'  →  '+S[0].summary.end;

  const tb=document.querySelector('#tbl tbody');tb.innerHTML='';
  S.forEach(s=>{const c=CV(HUE[s.hue]),q=s.summary;
    tb.insertAdjacentHTML('beforeend',`<tr>
      <td class="l"><span class="chip"><span class="dot" style="background:${c}"></span>
        <span>${s.code} · ${s.label}</span></span></td>
      <td class="num">${inr(q.final)}</td>
      <td class="num ${q.cagr>=0?'pos':'neg'}">${pct(q.cagr)}</td>
      <td class="num">${q.mdd.toFixed(1)}%</td>
      <td class="num">${q.trades}</td></tr>`);});

  const nd=document.querySelector('#notes');nd.innerHTML='';
  B.notes.forEach(([h,t,p])=>nd.insertAdjacentHTML('beforeend',
    `<div class="note" style="--c:${CV(HUE[h])}"><b>${t}</b><p>${p}</p></div>`));

  /* chart */
  const W=920,H=460,L=52,R=64,T=18,Bm=30;
  let xmin=1e18,xmax=-1e18,ymin=1e18,ymax=-1e18;
  S.forEach(s=>s.pts.forEach(([d,v])=>{const x=ts(d);
    xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,v);ymax=Math.max(ymax,v);}));
  ymin=Math.min(ymin,10000);ymax=Math.max(ymax,10000);
  ymin=Math.floor(ymin/1000)*1000;ymax=Math.ceil(ymax/1000)*1000;
  const X=x=>L+(x-xmin)/(xmax-xmin)*(W-L-R), Y=v=>T+(1-(v-ymin)/(ymax-ymin))*(H-T-Bm);
  const svg=document.querySelector('#svg');svg.innerHTML='';
  const step=(ymax-ymin)>16000?4000:2000;
  for(let v=ymin;v<=ymax;v+=step){const y=Y(v);
    svg.appendChild(el('line',{class:'gl',x1:L,y1:y,x2:W-R,y2:y}));
    const tx=el('text',{class:'axl',x:L-8,y:y+3.5,'text-anchor':'end'});tx.textContent=(v/1000)+'k';svg.appendChild(tx);}
  const y0=Y(10000);
  svg.appendChild(el('line',{class:'baseln',x1:L,y1:y0,x2:W-R,y2:y0}));
  const bl=el('text',{class:'basel',x:L+3,y:y0-5});bl.textContent='₹10,000 start';svg.appendChild(bl);
  const yr0=new Date(xmin).getUTCFullYear()+1,yr1=new Date(xmax).getUTCFullYear();
  for(let yr=yr0;yr<=yr1;yr++){const x=X(Date.UTC(yr,0,1));if(x<L||x>W-R)continue;
    svg.appendChild(el('line',{class:'gl',x1:x,y1:T,x2:x,y2:H-Bm}));
    const tx=el('text',{class:'axl',x:x,y:H-Bm+16,'text-anchor':'middle'});tx.textContent=yr;svg.appendChild(tx);}
  S.forEach(s=>{const c=CV(HUE[s.hue]);
    const dp=s.pts.map((p,i)=>(i?'L':'M')+X(ts(p[0])).toFixed(1)+' '+Y(p[1]).toFixed(1)).join(' ');
    svg.appendChild(el('path',{class:'ser'+(s.code==='S2'?' hi':''),d:dp,stroke:c,
      opacity:(s.code==='S4'||s.code==='S5')?0.85:1}));
    const last=s.pts[s.pts.length-1];
    svg.appendChild(el('circle',{cx:X(ts(last[0])),cy:Y(last[1]),r:s.code==='S2'?3.6:2.8,fill:c}));
    const lt=el('text',{class:'endlab',x:X(ts(last[0]))+6,y:Y(last[1])+3.6,fill:c});lt.textContent=s.code;svg.appendChild(lt);});

  const cross=el('line',{class:'cross',y1:T,y2:H-Bm});svg.appendChild(cross);
  const tip=document.querySelector('#tip');
  const flat=S.map(s=>({code:s.code,label:s.label,hue:CV(HUE[s.hue]),pts:s.pts}));
  const near=(px,arr)=>{let b=arr[0],bd=1e18;for(const p of arr){const d=Math.abs(X(ts(p[0]))-px);if(d<bd){bd=d;b=p;}}return b;};
  svg.onpointermove=ev=>{const r=svg.getBoundingClientRect();const px=(ev.clientX-r.left)/r.width*W;
    if(px<L||px>W-R){cross.style.opacity=0;tip.style.opacity=0;return;}
    cross.setAttribute('x1',px);cross.setAttribute('x2',px);cross.style.opacity=.9;
    const a=near(px,flat[4].pts);let rows='';
    flat.forEach(s=>{const p=near(px,s.pts);rows+=`<div class="row"><span style="color:${s.hue}">${s.code} ${s.label}</span><b>${inr(p[1])}</b></div>`;});
    tip.innerHTML=`<div class="d">${a[0]}</div>${rows}`;tip.style.opacity=1;
    let tx=ev.clientX-r.left+16;if(tx>r.width-200)tx=ev.clientX-r.left-200;
    tip.style.left=tx+'px';tip.style.top=Math.max(4,ev.clientY-r.top-10)+'px';};
  svg.onpointerleave=()=>{cross.style.opacity=0;tip.style.opacity=0;};
}
render();
</script>
"""

OUT.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
print("wrote", OUT, "bytes:", OUT.stat().st_size, "books:", [b["store"] for b in books])
