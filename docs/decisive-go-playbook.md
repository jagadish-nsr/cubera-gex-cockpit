# Decisive GO — Trader Playbook

> **Audience:** Medium–high risk FNO traders on Nifty, Bank Nifty, Sensex  
> **Surface:** Cockpit Home firing lane + Efficiency → Decisive GO Scorecard  
> **Contract:** Decision support only. **You confirm every order.** No auto-execution.

GEX here is a **positioning map** (call wall, put wall, magnet, flip, signed call−put balance). It is not a pin forecast and not “dealers will defend this strike.” Open interest is not buyer-versus-seller flow.

---

## 1. What you are looking at (10 seconds)

On **Cockpit Home**, above the alert tapes:

| Element | Read it as |
|---------|------------|
| **Mode** (Directional / Structure / Writer) | Which persona the GO is built for — change this first |
| **δ** (0.50 / 0.35 / 0.25) | Directional only — ATM vs OTM preference |
| **FIRE / SCOUT / HOLD** pill | Binary posture. FIRE = stageable with size; SCOUT = watch / half; HOLD = no new risk |
| Thesis + levels | One-line map reading + spot vs put wall / flip / call wall |
| **15m digest** | Rollup of recent GO emits, Sentinel lean, commentary. Raw tapes stay collapsed until you ask |
| **Open staged** | Paper-staged GOs with MFE/MAE, trail, and live Greeks |
| Primary GO card | Contracts, credit/debit, BEs, max loss ₹, T1/T2, trail, invalidation, **Why** evidence |
| Ritual chips | Optional Pre-bell / First block / EOD checklist — never blocks FIRE |

If the strip says HOLD, do not invent a trade from a lower tab.

---

## 2. Hard rules (every session)

1. **No broker send from the cockpit.** Stage → you confirm at the broker.
2. **Stops beyond gamma concentrations, never on the magnet.**
3. **Stale OI / placeholder chain → treat as HOLD** even if a score looks hot.
4. **Soft flags** (lunch chop, mild corridor, mixed breadth) **size down** — they do not secretly veto FIRE.
5. **Lot sizes** (verify on exchange calendar): Nifty 65 (Tue), Bank Nifty 30 (monthly last Tue), Sensex 20 (Thu). Holiday → previous trading day.
6. Headlines override the book. De-size when aggregate book and the local side of the flip disagree.

---

## 3. Daily skeleton (IST)

| Window | You do | System helps with |
|--------|--------|-------------------|
| 06:30–09:00 | Read prior walls, flip, \|GEX\|, FII lean, OI timestamp | Offline snapshot / morning briefing |
| 09:07–09:15 | Cash open vs walls; straddle shock; shortlist candidates | Pre-bell banner; mark Pre-bell chip |
| 09:15–10:30 | **First decision block:** pick mode → read posture → Stage or Skip | Primary GO + digest |
| 11:00–13:30 | Prefer manage / SCOUT; avoid forcing new FIRE in lunch | Lunch soft-flag → HALF |
| 13:00–15:10 | Expiry / blast location risk for writers | Blast tag; writer kill-style manages |
| 15:30–16:00 | Journal taken vs skipped; glance Scorecard | Stage outcomes + Efficiency tab |

---

## 4. Choosing a mode

| Mode | Use when | Primary CTA |
|------|----------|-------------|
| **Directional** | You want a naked (or simple) long CE/PE with trail | Stage journal → trail / flatten |
| **Structure** | You want defined-risk multi-leg (IC, vertical, calendar when front IV rich) | Check credit, BEs, max loss, smile, payoff → Stage |
| **Writer** | You sell premium with calm book + IV−RV edge | Margin is a **proxy, not SPAN** — size conservatively |

Wrong mode → wrong GO. Do not leave Directional on and trade an iron condor from a side card.

---

## 5. Directional playbook (naked buyer)

1. Set **Mode = Directional**. Pick **δ** (0.50 near ATM; 0.35 / 0.25 step OTM).
2. Want **FIRE** with a clear enter rule on the card. SCOUT = wait for tape/confirm or half size.
3. Open **Why** — every FIRE/SCOUT lists factors with pass/fail. Recompute from evidence; do not trust the score alone.
4. Confirm premium vs your risk budget. Soft corridor → expect HALF / SCOUT unless a break template.
5. **Stage journal** (paper). Confirm the same legs at the broker yourself.
6. Manage from the card / open panel:
   - Live **Δ Γ Vega Θ** on the proposed leg and on open staged longs
   - **Trail** arms after ~+30% on premium (sticky once armed)
   - Exit on spot invalidation (respect 5m close discipline) or T1/T2
7. EOD: Efficiency → Decisive GO Scorecard — stage/FIRE rate, skip/FIRE, avg MFE/MAE, hour and DOW slices.

**Skip FIRE** when you pass — that skip is logged so opportunity cost is visible later.

---

## 6. Structure playbook

1. Set **Mode = Structure**.
2. Read **\|GEX\| / vol context first:** calm → short-vol defined risk; wild → long premium / debit; mid → often HOLD.
3. On the card, before Stage:
   - Credit or debit and **breakevens**
   - **Max loss ₹** (and max profit when defined)
   - Net Greeks + **10Δ / 25Δ / ATM smile** strip
   - Expiry payoff sketch + Black-76 what-if shocks
4. Shorts **beyond** walls — never on the magnet.
5. Stage multi-leg. Manage with Adjust / hedge / flatten / roll-wing buttons (all stage-only).
6. Wall shift inside a short → use Adjust, do not hope.
7. Calendar / diagonal appears when **front IV is rich vs back** — still map language, not a pin.

---

## 7. Writer playbook (intentionally incomplete)

1. Set **Mode = Writer**.
2. FIRE only when calm book + **IV−RV &gt; 0** + shorts beyond walls + catalyst clear.
3. Treat **margin estimate as a floor proxy** — not exchange SPAN. Size down.
4. Kill / flatten when wall moves inside, blast is active near the short, or tape breaks the wall.
5. Portfolio strip on Scorecard sums margin proxies across indices — still not SPAN.
6. Do not chase “writer = 100% ready.” Without SPAN, the desk stays conservative by design.

---

## 8. Digest vs raw tapes (anti-hesitation)

- Default: **15m digest** under the strip. One line: GO F/S/H counts, stage/skip, Sentinel lean, commentary count.
- **Show raw tapes** only when you need the flood (alert tape + OrderFlow stream).
- Digest is a rollup — **not a second GO engine.** The primary card remains authoritative.

---

## 9. Stage, skip, trail, webhook

| Action | Effect |
|--------|--------|
| **Stage journal** | Opens paper trade + immutable GO snapshot; starts MFE/MAE ledger |
| **Skip FIRE** | Logs pass-over with decision id (for Scorecard) |
| **Trail / Flatten / Adjust…** | Stages manage intent or journal legs — you still confirm at broker |
| **FIRE webhook** (optional) | Opt-in Telegram/JSON POST on FIRE; deduped per decision fingerprint |

Never leave a staged FIRE without knowing entry premium sign: long debit is negative net; short credit is positive. Favorable MFE = entry − mark on that signed net.

---

## 10. Scorecard — did the process pay?

**Efficiency tab → Decisive GO Scorecard**

- Emits / Staged / Skipped / Edge (avg MFE÷MAE) / median emit→stage seconds
- By mode, by strategy key, writer portfolio proxy
- **Time-of-day (IST hour)** and **day-of-week** tables
- **Lunch-chop cohort** — soft size-down emits vs how many you staged

Use it to answer: Am I hesitating on FIRE? Is lunch killing expectancy? Which template’s edge is real?

---

## 11. Quick regime → action map

| Book / tape context | Directional | Structure | Writer |
|---------------------|-------------|-----------|--------|
| Absorption at put-wall location | SCOUT→FIRE CE if CVD holds | Tighten put wing / bull put credit | Hold shorts; alert if wall shifts in |
| Calm + inside corridor | SCOUT / HALF only | FIRE defined short-vol if geometry clean | FIRE beyond walls if IV−RV &gt; 0 |
| Wild + low \|GEX\| | FIRE long premium / break | FIRE long strangle / debit | HOLD new shorts |
| Strike break with tape | FIRE OTM with break template | Debit toward break | Hedge / flatten threatened short |
| Book vs tape conflict | Prefer tape for FIRE | Prefer \|GEX\| | Prefer \|GEX\|; both conflict → HOLD |

---

## 12. What not to do

- Do not treat FIRE as a broker order.
- Do not write “dealers defend the wall” into your journal thesis.
- Do not size writer risk from the margin proxy as if it were SPAN.
- Do not ignore **Why** evidence when the score is high.
- Do not run Directional δ chooser while managing a four-leg structure — switch mode.

---

## 13. Related docs

| Doc | Role |
|-----|------|
| This playbook (`/guide/decisive_go_playbook`) | How you trade the firing lane |
| `docs/decisive-go-product-spec.md` (repo) | Engineering backlog / acceptance |
| [Options Strategies Guide](/guide/options_strategies_guide) | Older recommendation taxonomy (support) |
| [Signal Efficiency Guide](/guide/signal_efficiency_guide) | Sentinel bar-signal stats (support) |
| [Gamma-Blast Scoring](/guide/gamma_blast_scoring_guide) | Expiry blast risk context |

---

*Decision support only. Not financial advice. You confirm all decisions.*
