"""analytics/divergence.py — IOAS v3.0 §3.4 Active Divergence & Shift Alert Engine.

Monitors 5 divergence pairs and generates instant shift alerts for:
  1. Price Higher / PCR Lower  → Bearish Divergence
  2. Price Lower / PCR Higher  → Bullish Divergence
  3. Price Trending Up / ATM IV Spiking → Climactic buying / hedge panic
  4. Call Wall / Put Wall Migrating against Spot Trend
  5. Put-Call Parity Persistent Dislocation (≥3 polls)

Shift Alert Events:
  - Call/Put Wall breach (spot crosses a wall)
  - PCR flip through 1.0
  - 2-sigma OI velocity burst

Separation guarantee: no network, no Streamlit. Stateless per-call — caller
maintains the history list across polls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DivergenceEvent:
    id: str
    type: str                     # PRICE_PCR_BEAR | PRICE_PCR_BULL | PRICE_IV_SPIKE | WALL_MIGRATION | PARITY_DISLOC
    severity: str                 # HIGH | MEDIUM | LOW
    description: str
    detail: str                   # raw numbers
    active: bool = True
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None

    def resolve(self) -> None:
        self.active = False
        self.resolved_at = datetime.now(timezone.utc).isoformat()


@dataclass
class ShiftAlert:
    type: str           # WALL_BREACH | PCR_FLIP | OI_BURST
    description: str
    detail: str
    triggered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class DivergenceReport:
    active_divergences: list[DivergenceEvent] = field(default_factory=list)
    new_alerts: list[ShiftAlert] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Helper: rolling OI velocity z-score (simple, stateless version)
# ---------------------------------------------------------------------------

def _zscore_simple(current: float, history: list[float]) -> float:
    """Return z-score of current vs history. Returns 0 if <3 samples."""
    if len(history) < 3:
        return 0.0
    import math
    n = len(history)
    mean = sum(history) / n
    var = sum((x - mean) ** 2 for x in history) / n
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return (current - mean) / std


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_divergences(
    *,
    spot: float,
    spot_prev: Optional[float],         # spot N polls ago (~15m)
    pcr: Optional[float],
    pcr_prev: Optional[float],          # PCR N polls ago (~15m)
    pcr_prev_session: Optional[float],  # PCR at session open (for flip detection)
    atm_iv: Optional[float],
    atm_iv_prev: Optional[float],
    call_wall: Optional[float],
    put_wall: Optional[float],
    call_wall_prev: Optional[float],    # call wall N polls ago
    put_wall_prev: Optional[float],
    parity_alert_count: int = 0,        # number of consecutive polls with parity breach
    call_doi_total: Optional[float] = None,
    put_doi_total: Optional[float] = None,
    oi_doi_history: Optional[list[float]] = None,  # recent absolute total ΔOI values
    existing_divergences: Optional[list[DivergenceEvent]] = None,
) -> DivergenceReport:
    """Detect active divergences and generate shift alerts.

    This function is designed to be called on every poll. Pass ``existing_divergences``
    from the previous result to maintain state. New divergences are added; resolved ones
    are marked inactive.
    """
    report = DivergenceReport()
    existing = list(existing_divergences or [])

    # --- Helper to find existing divergence by type ---
    def _existing(dtype: str) -> Optional[DivergenceEvent]:
        for d in existing:
            if d.type == dtype and d.active:
                return d
        return None

    # -----------------------------------------------------------------------
    # 1. Price Higher / PCR Lower — Bearish divergence
    # -----------------------------------------------------------------------
    if spot is not None and spot_prev and pcr and pcr_prev:
        price_up = spot > spot_prev * 1.001  # >0.1% move
        pcr_down = pcr < pcr_prev * 0.97     # PCR dropped >3%
        if price_up and pcr_down:
            ev = _existing("PRICE_PCR_BEAR")
            if ev is None:
                ev = DivergenceEvent(
                    id=f"PRICE_PCR_BEAR_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    type="PRICE_PCR_BEAR",
                    severity="HIGH",
                    description="Price rising but PCR falling — bearish divergence",
                    detail=f"Spot {spot_prev:,.0f}→{spot:,.0f} · PCR {pcr_prev:.2f}→{pcr:.2f}",
                )
                existing.append(ev)
        else:
            ev = _existing("PRICE_PCR_BEAR")
            if ev:
                ev.resolve()

    # -----------------------------------------------------------------------
    # 2. Price Lower / PCR Higher — Bullish divergence
    # -----------------------------------------------------------------------
    if spot is not None and spot_prev and pcr and pcr_prev:
        price_down = spot < spot_prev * 0.999
        pcr_up = pcr > pcr_prev * 1.03
        if price_down and pcr_up:
            ev = _existing("PRICE_PCR_BULL")
            if ev is None:
                ev = DivergenceEvent(
                    id=f"PRICE_PCR_BULL_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    type="PRICE_PCR_BULL",
                    severity="MEDIUM",
                    description="Price falling but PCR rising — bullish divergence (put buying into dip)",
                    detail=f"Spot {spot_prev:,.0f}→{spot:,.0f} · PCR {pcr_prev:.2f}→{pcr:.2f}",
                )
                existing.append(ev)
        else:
            ev = _existing("PRICE_PCR_BULL")
            if ev:
                ev.resolve()

    # -----------------------------------------------------------------------
    # 3. Price Trending Up / ATM IV Spiking — Climactic buying / hedge panic
    # -----------------------------------------------------------------------
    if spot is not None and spot_prev and atm_iv and atm_iv_prev:
        price_up = spot > spot_prev * 1.001
        iv_spike = atm_iv > atm_iv_prev * 1.05  # IV up >5% while price also up
        if price_up and iv_spike:
            ev = _existing("PRICE_IV_SPIKE")
            if ev is None:
                ev = DivergenceEvent(
                    id=f"PRICE_IV_SPIKE_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    type="PRICE_IV_SPIKE",
                    severity="HIGH",
                    description="Price rallying AND IV spiking — climactic buying or hedge panic",
                    detail=(
                        f"Spot {spot_prev:,.0f}→{spot:,.0f} · "
                        f"ATM IV {atm_iv_prev*100:.1f}%→{atm_iv*100:.1f}%"
                    ),
                )
                existing.append(ev)
        else:
            ev = _existing("PRICE_IV_SPIKE")
            if ev:
                ev.resolve()

    # -----------------------------------------------------------------------
    # 4. Call Wall / Put Wall migrating against Spot Trend
    # -----------------------------------------------------------------------
    if (call_wall and call_wall_prev and put_wall and put_wall_prev
            and spot and spot_prev):
        spot_up = spot > spot_prev * 1.001
        # Wall migrating DOWN while spot moves UP (or vice versa) is the divergence
        call_wall_down = call_wall < call_wall_prev - 50
        put_wall_down = put_wall < put_wall_prev - 50
        if spot_up and (call_wall_down or put_wall_down):
            direction = "Call" if call_wall_down else "Put"
            ev = _existing("WALL_MIGRATION")
            if ev is None:
                ev = DivergenceEvent(
                    id=f"WALL_MIGRATION_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    type="WALL_MIGRATION",
                    severity="MEDIUM",
                    description=f"{direction} wall migrating DOWN while spot moves UP — unusual repositioning",
                    detail=(
                        f"Spot {spot_prev:,.0f}→{spot:,.0f} · "
                        f"CW {call_wall_prev:,.0f}→{call_wall:,.0f} · "
                        f"PW {put_wall_prev:,.0f}→{put_wall:,.0f}"
                    ),
                )
                existing.append(ev)
        else:
            ev = _existing("WALL_MIGRATION")
            if ev:
                ev.resolve()

    # -----------------------------------------------------------------------
    # 5. Put-Call Parity Persistent Dislocation (≥ 3 polls)
    # -----------------------------------------------------------------------
    if parity_alert_count >= 3:
        ev = _existing("PARITY_DISLOC")
        if ev is None:
            ev = DivergenceEvent(
                id=f"PARITY_DISLOC_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                type="PARITY_DISLOC",
                severity="HIGH",
                description="Put-Call Parity breach persisting ≥3 polls — possible institutional synthetic pressure",
                detail=f"{parity_alert_count} consecutive polls with ≥10bps parity deviation",
            )
            existing.append(ev)
    else:
        ev = _existing("PARITY_DISLOC")
        if ev:
            ev.resolve()

    # -----------------------------------------------------------------------
    # Shift Alerts (instant — no persistence required)
    # -----------------------------------------------------------------------

    # PCR flip through 1.0
    if pcr is not None and pcr_prev is not None:
        if (pcr_prev > 1.0 > pcr) or (pcr_prev < 1.0 < pcr):
            direction = "below" if pcr < 1.0 else "above"
            report.new_alerts.append(ShiftAlert(
                type="PCR_FLIP",
                description=f"PCR flipped {direction} 1.0 — sentiment regime shift",
                detail=f"PCR {pcr_prev:.2f} → {pcr:.2f}",
            ))

    # Call wall / Put wall breach (spot crosses a wall)
    if spot and spot_prev and call_wall:
        if spot_prev < call_wall <= spot:
            report.new_alerts.append(ShiftAlert(
                type="WALL_BREACH",
                description=f"Spot breached Call Wall {call_wall:,.0f} to the upside 🚀",
                detail=f"Spot {spot_prev:,.0f} → {spot:,.0f} crossing CW {call_wall:,.0f}",
            ))
    if spot and spot_prev and put_wall:
        if spot_prev > put_wall >= spot:
            report.new_alerts.append(ShiftAlert(
                type="WALL_BREACH",
                description=f"Spot broke below Put Wall {put_wall:,.0f} 💥",
                detail=f"Spot {spot_prev:,.0f} → {spot:,.0f} crossing PW {put_wall:,.0f}",
            ))

    # 2-sigma OI velocity burst
    if call_doi_total is not None and put_doi_total is not None and oi_doi_history:
        total_doi = abs(call_doi_total or 0) + abs(put_doi_total or 0)
        z = _zscore_simple(total_doi, oi_doi_history)
        if z >= 2.0:
            report.new_alerts.append(ShiftAlert(
                type="OI_BURST",
                description=f"2σ OI velocity burst detected (z={z:.1f}x) — unusual positioning activity",
                detail=f"Total ΔOI={total_doi:,.0f}",
            ))

    # -----------------------------------------------------------------------
    # Build final report
    # -----------------------------------------------------------------------
    active = [d for d in existing if d.active]
    report.active_divergences = active

    if active:
        high = [d for d in active if d.severity == "HIGH"]
        if high:
            report.summary = (
                f"🔴 {len(high)} HIGH severity divergence(s) active: "
                + " | ".join(d.description for d in high[:2])
            )
        else:
            report.summary = (
                f"🟡 {len(active)} divergence(s) active: "
                + " | ".join(d.description for d in active[:2])
            )
    else:
        report.summary = "✅ No active divergences detected"

    return report
