# 🌅 Pre/Post-Market Microstructure & Recommendations Lifecycle Guide

This comprehensive guide details the architecture, mathematical formulations, data pipelines, and operator workflows for:
1. **Pre-Open Market Microstructure & Pre-Bell Tactical Planning (09:00:00 – 09:15:00 IST)**
2. **Closing Auction Session (CAS) & F&O Extended Close (15:30:00 – 15:40:00 / 16:00:00 IST)**
3. **High-Conviction Recommendations Lifecycle & Bookmarking Persistence**
4. **Conviction Trajectory Microstructure Audit & UI Polish**

---

## 🧭 Master Daily Session Timeline

The Indian financial markets operate under distinct microstructure regimes across equities and derivatives. The Cockpit orchestrates automated polling, alert triggers, audio notifications, and analytical models tailored to each phase:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 INDIAN MARKET MICROSTRUCTURE TIMETABLE                                 │
├───────────────┬─────────────────────────┬──────────────────────┬───────────────────────────────────────┤
│ Time (IST)    │ Session Phase           │ Ingestion Frequency  │ Core Microstructure Mechanics         │
├───────────────┼─────────────────────────┼──────────────────────┼───────────────────────────────────────┤
│ 09:00 – 09:07 │ PRE_MARKET_ENTRY        │ Passive / Paused     │ Order accumulation; no matching       │
│ 09:07:30–09:15│ PRE_MARKET_DISCOVERY    │ Adaptive 3.0s Retry  │ Equilibrium price discovery & Pre-Bell│
│ 09:15 – 15:30 │ REGULAR_SESSION         │ 0ms WS / 3s REST     │ Continuous trading & intraday signals │
│ 15:30 – 15:40 │ CAS_AND_FNO_EXTENDED    │ 3s Continuous        │ 30m VWAP cash auction & F&O final run │
│ 15:40 – 16:00 │ POST_MARKET_SETTLEMENT  │ Reconciliation       │ Settlement divergence & pin scorecard │
│ 16:00 – 09:00 │ OFF_HOURS               │ Static Cache         │ Overnight off-hours & holiday standby │
└───────────────┴─────────────────────────┴──────────────────────┴───────────────────────────────────────┘
```

---

## Part 1: Pre-Open Market Microstructure (09:00 – 09:15 IST)

### 1.1 NSE Pre-Open Auction Architecture
The National Stock Exchange (NSE) executes a single-price call auction for the Cash Equity segment prior to regular market open:
- **09:00:00 – 09:07:59 IST (Order Collection)**: Institutional and retail participants place, modify, and cancel orders in the equity cash book. No trade execution occurs.
- **09:08:00 – 09:12:00 IST (Order Matching & Equilibrium Determination)**: Order books are frozen with random closure between 09:07 and 09:08. The exchange matches orders at the single price that maximizes traded volume (Equilibrium Opening Price).
- **09:12:00 – 09:15:00 IST (Buffer Period)**: Transition window to ensure smooth order queue migration into continuous trading.
- **Derivatives Asymmetry**: The F&O segment does **NOT** participate in the pre-open auction. Equity derivatives (Index Futures and Options) open strictly at **09:15:00 IST**.

---

### 1.2 The 09:07:30 Adaptive Discovery Engine
Rather than polling continually throughout 09:00–09:07 (wasting API rate limits while books are fluctuating), the Cockpit initiates **Adaptive Polling** at **09:07:30 IST**:

```
[09:07:30 IST Trigger] ──▶ Poll Dhan /marketfeed/quote (IDX_I:13 / 25)
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
        [open_px == 0.0]                       [open_px > 0.0 discovered]
                 │                                       │
        Sleep 3.0s & Retry                      Extract Discovered Spot
                                                Calculate Gap Pts & % vs Prev Close
                                                         │
                                                ┌────────┴────────┐
                                                ▼                 ▼
                                        Trigger Audio Chime   Render Pre-Bell
                                        (C5-E5-G5-C6 Bell)   Tactical Briefing
```

#### Polling Rules:
1. **Target Feed**: Dhan Quote REST endpoint querying `IDX_I` (`securityId: 13` for NIFTY, `25` for BANKNIFTY).
2. **Interval**: Fixed **3.0 seconds** during `PRE_MARKET_DISCOVERY` (bypassing normal 60s off-hours sleep).
3. **Discovery Condition**:
   $$\text{is\_discovered} = \left(S_{\text{open}} > 0\right) \land \left(|S_{\text{open}} - S_{\text{prev\_close}}| > 0.01 \lor \text{time} \ge 09:08:00\right)$$
4. **Cache Longevity**: Once discovered, the opening price is cached in `_PRE_MARKET_CACHE` for the remainder of the session.

---

### 1.3 Morning Chime Bell Notification
When the discovery condition evaluates to `True`, the Cockpit automatically sounds an audio chime to alert the trader:
- **Audio Synthesis**: Pure client-side Web Audio API oscillator sequence (no external MP3/WAV dependencies).
- **Musical Structure**: Ascending harmonic 4-tone sequence:
  - Tone 1: $C_5 = 523.25\text{ Hz}$ at $t = 0.00\text{s}$
  - Tone 2: $E_5 = 659.25\text{ Hz}$ at $t = 0.12\text{s}$
  - Tone 3: $G_5 = 783.99\text{ Hz}$ at $t = 0.24\text{s}$
  - Tone 4: $C_6 = 1046.50\text{ Hz}$ at $t = 0.36\text{s}$
- **Envelope**: Exponential decay over 400ms per note with 0.22 master gain.
- **Deduplication**: Monitored by `_CHIME_TRIGGERED_DAYS` keyed on `(symbol, YYYY-MM-DD)` to ensure it triggers exactly once per day.

---

### 1.4 The 7-Minute Pre-Bell Alpha Window (09:08 – 09:15 IST)
Between ~09:08:15 and 09:15:00 IST, an exploitable information asymmetry exists:
> **The Cash Index opening equilibrium is known, but Option Chain prices are frozen at yesterday's close.**

The Cockpit converts this 7-minute window into high-value tactical preparedness:

#### A. Opening Gap Classification Taxonomy
The engine compares the discovered opening price ($S_{\text{disc}}$) against structural GEX levels:
- **Call Wall ($CW$)**: Strike with highest dealer net positive gamma.
- **Put Wall ($PW$)**: Strike with highest dealer net negative gamma.
- **Gamma Flip ($GF$)**: Zero-gamma transition boundary.

```
┌──────────────────────────┬───────────────────────────────┬─────────────────────────────────────────────┐
│ Gap Regime               │ Classification Condition      │ Strategic Briefing & Favored Play           │
├──────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────┤
│ SHORT_GAMMA_WALL_TEST    │ S >= Call Wall - 15 pts       │ Call Wall directly challenged; dealers face │
│                          │                               │ short gamma squeeze. Play: MOMENTUM CALLS   │
├──────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────┤
│ PUT_WALL_BREAKDOWN_TEST  │ S <= Put Wall + 15 pts        │ Support breakdown risk; option writers      │
│                          │                               │ absorb volume. Play: PUT REVERSAL / FADE    │
├──────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────┤
│ GAP_UP_RUNAWAY           │ Gap % >= +0.40%               │ Bullish runaway momentum; watch for 09:15   │
│                          │                               │ opening 3m range hold. Play: CE MOMENTUM    │
├──────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────┤
│ GAP_DOWN_TRAP            │ Gap % <= -0.40%               │ Bearish expansion dominant; look for bounce │
│                          │                               │ failure into prev close. Play: PE MOMENTUM  │
├──────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────┤
│ AUCTION_BALANCED         │ |Gap %| <= 0.15%              │ Flat open; initial two-sided mean reversion │
│                          │                               │ around VWAP. Play: RANGE ACCUMULATION       │
├──────────────────────────┼───────────────────────────────┼─────────────────────────────────────────────┤
│ MODERATE_DRIFT           │ 0.15% < |Gap %| < 0.40%       │ Mild rotation; structural continuation.     │
│                          │                               │ Play: CE / PE STRUCTURAL                    │
└──────────────────────────┴───────────────────────────────┴─────────────────────────────────────────────┘
```

#### B. Synthetic Pre-Bell Option MTM Shock Modeling
Because option contracts cannot trade until 09:15, traders often enter the open blind to expected option prices. The Cockpit calculates synthetic opening option prices using Taylor-expansion Delta-Gamma adjustments:

$$\Delta S = S_{\text{disc}} - S_{\text{prev}}$$

$$\text{Shock}_{\text{model}} = \left(\Delta \cdot \Delta S\right) \pm \left(\frac{1}{2}\Gamma \cdot (\Delta S)^2\right)$$

$$\text{LTP}_{\text{est}} = \max\left(\text{LTP}_{\text{prev}} + \text{Shock}_{\text{model}}, \text{Intrinsic Floor}\right)$$

Where:
- $\text{Intrinsic Floor}_{\text{CE}} = \max(0, S_{\text{disc}} - K) + 0.50$
- $\text{Intrinsic Floor}_{\text{PE}} = \max(0, K - S_{\text{disc}}) + 0.50$
- $\Gamma_{\text{est}} \approx 0.0015$ (near-the-money gamma curvature coefficient)

This yields a pre-bell tactical pricing matrix displayed directly in the Cockpit banner:
- Expected Opening Premium ($\text{₹}$)
- Expected Absolute Change ($\text{₹}$)
- Expected Percentage Jump ($\%$)

---

## Part 2: Closing Auction Session (CAS) & F&O Extended Close (15:30 – 16:00 IST)

### 2.1 The Two Closing Milestones (15:30 vs 15:40 IST)
In the Indian microstructure:
1. **15:30:00 IST**: Regular continuous trading in Equity Cash halts. Continuous option prices freeze.
2. **15:30 – 15:35/15:40 IST (Cash Closing Auction Session - CAS)**:
   - Cash market enters a closing call auction.
   - The exchange calculates the **Official Settlement Price** as the **30-minute volume-weighted average price (VWAP)** between 15:00:00 and 15:30:00 IST.
3. **15:40:00 IST (F&O Extended Close)**:
   - Under the NSE circular framework (effective August 3, 2026), equity derivatives trade until **15:40:00 IST** (`FNO_CLOSE_MIN = 15*60 + 40`).
   - This provides derivatives traders a 10-minute window to hedge positions directly against the cash CAS settlement indications.

---

### 2.2 Post-Market Settlement & Pinning Scorecard
Once the session concludes after 15:30/15:40 IST, the Cockpit renders the **Post-Market Settlement Scorecard**:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              POST-MARKET SETTLEMENT SCORECARD                          │
├──────────────────────────┬──────────────────────────────┬──────────────────────────────┤
│ Metric                   │ Calculation Formula          │ Institutional Significance   │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Official Settlement Spot │ 30-min Cash VWAP (from Dhan) │ Final contract expiry mark   │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Settlement Divergence    │ S_settle - S_continuous     │ Measures closing auction slip│
│                          │                              │ vs continuous 15:30 price    │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Dealer Pin Efficiency    │ 100 - (|S_settle - Wall|/30) │ How accurately market makers │
│                          │                              │ pinned price to Call/Put Wall│
└──────────────────────────┴──────────────────────────────┴──────────────────────────────┘
```

#### Dealer Pinning Efficiency Index:
$$\text{Efficiency}_{\text{pin}} = \max\left(0, 100 - \frac{|S_{\text{settle}} - \text{Target Wall}|}{\text{Step Size}} \times 100\right)$$

- **$\ge 90\%$ (Perfect Pin)**: Dealers successfully neutralized delta/gamma risk at the structural wall, collecting maximum premium.
- **$< 50\%$ (Gamma Blast Unpin)**: Dealers were overrun into the close, triggering forced gamma hedging liquidation.

---

## Part 3: High-Conviction Recommendations Lifecycle

### 3.1 Pinned Cards Retention Rules
Previously, bookmarked recommendation cards could be cleared across intraday restarts or daily scans. The updated lifecycle model enforces strict operational persistence:

```
[Card Generated & Bookmarked by User]
                  │
                  ▼
   Card Stored in Persistent SQLite DB
                  │
                  ▼
        Check Daily & On Each Scan:
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
[User Clicks 'Unpin']    [today > contract_expiry]
       │                     │
       └──────────┬──────────┘
                  ▼
         Card Evicted / Expired
```

#### Retention Guarantees:
1. **Cross-Session Persistence**: Pinned cards remain active across application restarts, browser refreshes, and multi-day holds.
2. **Contract Expiry Enforcement**:
   - Evaluated by `is_contract_expired(record)` in [`data/pre_post_market.py`](file:///Users/jagadish/MyProjects/Applications/cubera-gex-cockpit/data/pre_post_market.py#L360-L402).
   - Card remains active until the end of the contract expiry date.
   - Once `today > contract_expiry_date`, the card is automatically retired.
3. **Multi-Format Parsing**:
   - Supports ISO format: `payload["expiry"] = "2026-10-15"`
   - Supports formatted symbol string: `"NIFTY 13 OCT 22700 CE"` $\to$ extracts Day (`13`), Month (`OCT`), and Year (`2026`).

---

### 3.2 Recommendation Card Visual Enhancements

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 🟢 NIFTY 13 OCT 22700 CE  [NEW] ★ Pinned                           Grade A+ (95/100)  │
│ ────────────────────────────────────────────────────────────────────────────────────── │
│ Entry: ₹45.2   |   T1: ₹68.0 (+50%)   |   T2: ₹90.0 (+99%)   |   SL: ₹28.0 (-38%)      │
│ 📦 2 Lots (50 Qty) · Outlay: ₹2,260 (0.9% Port) · Conservative Risk Budget             │
│ 🛑 Spot Invalidation: ₹22,640 (Testing Call Wall 22,700)                               │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Card Borderlines**: Clean, high-contrast borderlines (`1.5px solid rgba(34,197,94,.4)` for CE, `rgba(239,68,68,.4)` for PE) with subtle glassmorphic elevation.
2. **Blinking "NEW" Pill**:
   - Rendered on newly generated recommendations whose detection timestamp is within **5 minutes** (`age_seconds < 300`).
   - Uses CSS animation `rec-pulse-badge` (1.2s infinite pulse) that automatically subsides after 5 minutes.
3. **Full Contract Notation**:
   - Replaced abbreviated `NIFTY 22700 CE` with full official derivative format:
     $$\text{SYMBOL} \quad \text{DD} \quad \text{MMM} \quad \text{STRIKE} \quad \text{TYPE}$$
     Example: `NIFTY 13 OCT 22700 CE` or `BANKNIFTY 15 OCT 55200 PE`.

---

### 3.3 Conviction Trajectory Audit Redesign
The **Conviction Trajectory Audit Table** tracks how an option recommendation's score evolved as market microstructure shifted:

#### Key Fixes & Upgrades:
1. **Reverse Chronological Sorting**:
   - The latest audit snapshot is strictly on top (`records.sort(key=lambda x: x["timestamp"], reverse=True)`).
2. **Granular Microstructure Reasoning**:
   - Replaced generic static strings with specific causal attribution:
     - *Directional Bias Shifts*: `"Directional bias strengthened +0.6 (from +3.2 to +3.8)"`
     - *Wall Proximity Shifts*: `"Testing Call Wall ₹22,500 (103 pts proximity)"`
     - *Gamma Buffer Changes*: `"Positive gamma buffer (+3 pts above Flip)"`
3. **Compact & Legible Typography**:
   - Padding reduced to `4px 8px` with tabular monospace numerals.
   - Green color coding for positive conviction updates (`+5`), red for fades (`-10`), and neutral for steady conviction.

---

## Part 4: Market Calendar & Holiday Suppression

To prevent phantom chart bars or stale data processing during exchange holidays:
1. **`is_trading_day()` Verification**:
   - Checks NSE holiday calendar against `data/holidays.py`.
   - Any weekday declared an official NSE trading holiday (e.g., Diwali, Independence Day, Gandhi Jayanti) is automatically routed to `OFF_HOURS`.
2. **Weekend Suppression**:
   - Saturdays and Sundays are hard-routed to `OFF_HOURS` (`weekday >= 5`).
3. **Chart Guarding**:
   - OrderFlow Sentinel and Candlestick Tape chart queries suppress synthetic live updates when `is_trading_day(today)` is false.

---

## Part 5: Operator Checklist & Quick Reference

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              OPERATOR DAILY ACTION CHECKLIST                           │
├─────────────┬──────────────────────────────────────────────────────────────────────────┤
│ 09:00 IST   │ Terminal initializes; Pre-Market Order Entry status displayed.           │
│ 09:07:30 IST│ Adaptive 3-second polling automatically engages. Stand by for chime.    │
│ 09:08:15 IST│ 🔔 Morning Bell Sounds! Opening price discovered.                        │
│ 09:08–09:14 │ Review Pre-Bell Tactical Briefing:                                       │
│             │ • Check Opening Gap Regime (Wall breach vs Balanced Auction)             │
│             │ • Note Synthetic Option Opening LTPs (Delta shock expected jumps)        │
│             │ • Align with Favored Play (MOMENTUM vs REVERSAL)                         │
│ 09:15:00 IST│ Bell rings! Execute trade ideas that hold above spot invalidation.      │
│ 15:30:00 IST│ Continuous equity trading closes. CAS 30m VWAP calculation begins.       │
│ 15:30–15:40 │ F&O Extended Window: Manage final hedges prior to 15:40 derivatives close│
│ 15:40:00 IST│ Review Post-Market Settlement Scorecard & Dealer Pin Efficiency Index.   │
└─────────────┴──────────────────────────────────────────────────────────────────────────┘
```

---

## 🔗 Related Technical Documentation
- [🎯 Options Recommendation Strategies & Alpha Playbook](/?view=options_strategies_guide)
- [⚡ OrderFlow Sentinel Stream Signals Field Guide](/?view=orderflow_guide)
- [📊 Quantitative Calculations & Formulas Reference](/?view=data_dictionary)
- [📈 Signal Efficiency & MFE/MAE Performance Guide](/?view=signal_efficiency_guide)
- [📘 Master Cockpit User Guide](/?view=user_guide)
