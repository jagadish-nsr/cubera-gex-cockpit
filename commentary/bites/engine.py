"""commentary/bites/engine.py — Suggestion Bites Generation Engine."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from commentary.models import PointInTimeView, ScoredEvent, SuggestionBite
from commentary.config_loader import get_commentary_config
from commentary.bites.gating import check_risk_gating, apply_mode_and_calibration_gating
from commentary.bites.manage import generate_manage_bites


class BiteEngine:
    """Generates strictly verified, level-anchored Suggestion Bites."""

    def __init__(self, session_count: int = 0, is_calibrated: bool = False) -> None:
        self.session_count = session_count
        self.is_calibrated = is_calibrated
        self.cfg = get_commentary_config()

    def generate_bites(
        self,
        view: PointInTimeView,
        scored_events: list[ScoredEvent],
    ) -> list[SuggestionBite]:
        """Generate, gate, and return valid Suggestion Bites for the current tick."""
        bites: list[SuggestionBite] = []

        # 1. Pinned Position Bites (MANAGE)
        pinned_records = view.pinned_recommendations
        if not pinned_records:
            try:
                from data.journal.recommendations_store import list_bookmarked_records
                pinned_records = list_bookmarked_records(view.symbol)
            except Exception:
                pinned_records = []

        if pinned_records:
            manage_bites = generate_manage_bites(view.spot, pinned_records, view.as_of)
            bites.extend(manage_bites)

        # 2. Market Event Bites (WATCH, PREPARE, ACT-IF, STAND-ASIDE, HEDGE)
        for se in scored_events:
            ev = se.event
            if se.significance < 0.65:
                continue

            etype = ev.type
            f = ev.facts

            # A. Wall Breakout / Reversal
            if etype == "WALL_BREAK":
                wtype = str(f.get("wall_type", "")).upper()
                strike = float(f.get("strike", view.spot))
                if "CALL" in wtype:
                    # Bullish Call Wall Break
                    bite = SuggestionBite(
                        id=f"bite-{uuid.uuid4().hex[:8]}",
                        entry_id=f"entry-{ev.id}",
                        ts=view.as_of,
                        type="ACT-IF",
                        headline=f"Call Wall Breakout above ₹{strike:,.0f}",
                        condition={"field": "close_5m", "op": ">", "val": strike},
                        action_frame=f"Bull Call Spread {strike:.0f}/{strike + 200:.0f} CE",
                        structure={"legs": f"{strike:.0f} CE Long / {strike + 200:.0f} CE Short"},
                        levels={"trigger": strike, "invalidation": strike - 60.0, "target": strike + 150.0},
                        risk={"max_loss_per_lot": 2200.0, "rr": 2.2, "breakeven": strike + 45.0},
                        valid_until=view.as_of + dt.timedelta(minutes=45),
                        scenario_link="bull",
                        badge={"mode": "shadow", "calibrated": self.is_calibrated},
                        facts_used=["spot", "call_wall", "strike"],
                    )
                    bites.append(bite)
                elif "PUT" in wtype:
                    # Bearish Put Wall Break
                    bite = SuggestionBite(
                        id=f"bite-{uuid.uuid4().hex[:8]}",
                        entry_id=f"entry-{ev.id}",
                        ts=view.as_of,
                        type="ACT-IF",
                        headline=f"Put Wall Breakdown below ₹{strike:,.0f}",
                        condition={"field": "close_5m", "op": "<", "val": strike},
                        action_frame=f"Bear Put Spread {strike:.0f}/{strike - 200:.0f} PE",
                        structure={"legs": f"{strike:.0f} PE Long / {strike - 200:.0f} PE Short"},
                        levels={"trigger": strike, "invalidation": strike + 60.0, "target": strike - 150.0},
                        risk={"max_loss_per_lot": 2200.0, "rr": 2.2, "breakeven": strike - 45.0},
                        valid_until=view.as_of + dt.timedelta(minutes=45),
                        scenario_link="bear",
                        badge={"mode": "shadow", "calibrated": self.is_calibrated},
                        facts_used=["spot", "put_wall", "strike"],
                    )
                    bites.append(bite)

            # B. Opening Range Breakout
            elif etype == "ORB_BREAKOUT":
                direction = str(f.get("breakout_direction", "UP")).upper()
                orb_h = float(f.get("orb_high", view.spot))
                orb_l = float(f.get("orb_low", view.spot))
                if direction == "UP":
                    bite = SuggestionBite(
                        id=f"bite-{uuid.uuid4().hex[:8]}",
                        entry_id=f"entry-{ev.id}",
                        ts=view.as_of,
                        type="ACT-IF",
                        headline=f"ORB 15m Upside Expansion above ₹{orb_h:,.0f}",
                        condition={"field": "close_5m", "op": ">", "val": orb_h},
                        action_frame=f"Long Momentum CE / Bull Spread",
                        structure={"legs": f"{round(orb_h, -2):.0f} CE"},
                        levels={"trigger": orb_h, "invalidation": (orb_h + orb_l) / 2.0, "target": view.call_wall},
                        risk={"max_loss_per_lot": 2500.0, "rr": 2.0, "breakeven": orb_h + 35.0},
                        valid_until=view.as_of + dt.timedelta(minutes=60),
                        scenario_link="bull",
                        badge={"mode": "shadow", "calibrated": self.is_calibrated},
                        facts_used=["orb_high", "call_wall"],
                    )
                    bites.append(bite)

            # C. ATM IV Spike / High Volatility -> STAND-ASIDE or HEDGE
            elif etype in ("VOL_ATM_IV_SPIKE", "VOL_INDIA_VIX_SURGE"):
                bite = SuggestionBite(
                    id=f"bite-{uuid.uuid4().hex[:8]}",
                    entry_id=f"entry-{ev.id}",
                    ts=view.as_of,
                    type="STAND-ASIDE",
                    headline="Elevated Volatility & Wide Bid-Ask Spreads",
                    condition={"field": "iv_change", "op": ">", "val": 1.0},
                    action_frame="Stand Aside or Defined-Risk Spreads Only",
                    structure={"guidance": "Prohibit naked options"},
                    levels={"trigger": view.spot, "invalidation": view.spot, "target": view.spot},
                    risk={"max_loss_per_lot": 0.0, "rr": 1.0},
                    valid_until=view.as_of + dt.timedelta(minutes=30),
                    scenario_link="base",
                    badge={"mode": "shadow", "calibrated": True},
                    facts_used=["atm_iv", "india_vix"],
                )
                bites.append(bite)

        # 3. Apply Risk & Mode Gating
        verified_bites: list[SuggestionBite] = []
        for bite in bites:
            passed_risk, _ = check_risk_gating(bite)
            if not passed_risk:
                continue
            gated_bite = apply_mode_and_calibration_gating(
                bite,
                is_calibrated=self.is_calibrated,
                session_count=self.session_count,
            )
            verified_bites.append(gated_bite)

        return verified_bites
