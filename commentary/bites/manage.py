"""commentary/bites/manage.py — Position-Aware MANAGE Suggestion Bites for Pinned Recommendations."""

from __future__ import annotations

import datetime as dt
from typing import Any
from commentary.models import SuggestionBite


def generate_manage_bites(
    current_spot: float,
    pinned_records: list[dict[str, Any]],
    as_of: dt.datetime,
) -> list[SuggestionBite]:
    """Inspect active pinned recommendations and generate actionable MANAGE bites.
    
    Advisory guidance includes trailing stops to entry breakeven, taking partial profits
    at Target 1, or alerting on invalidation wall approach.
    """
    bites: list[SuggestionBite] = []

    for rec in pinned_records:
        rec_id = str(rec.get("rec_id", "REC"))
        status = str(rec.get("status", "ACTIVE")).upper()
        if status not in ("ACTIVE", "BOOKMARKED"):
            continue

        side = str(rec.get("side", "")).upper()
        init_spot = float(rec.get("initial_spot", current_spot))
        t1_spot = float(rec.get("target_1_spot", 0.0))
        t2_spot = float(rec.get("target_2_spot", t1_spot))
        inval_spot = float(rec.get("spot_invalidation", 0.0))
        play_type = str(rec.get("play_type", "Option Trade"))

        is_bullish = side in ("CE", "CALL", "LONG")

        # 1. Target 1 Reached / Approached Check
        t1_reached = False
        t1_approached = False
        if is_bullish and t1_spot > 0:
            if current_spot >= t1_spot:
                t1_reached = True
            elif 0 <= (t1_spot - current_spot) <= 15.0:
                t1_approached = True
        elif not is_bullish and t1_spot > 0:
            if current_spot <= t1_spot:
                t1_reached = True
            elif 0 <= (current_spot - t1_spot) <= 15.0:
                t1_approached = True

        if t1_reached or t1_approached:
            gain_pts = abs(current_spot - init_spot)
            headline = (
                f"Pinned {rec_id}: Target 1 {'Reached' if t1_reached else 'Approached'} "
                f"(₹{t1_spot:,.0f}, +{gain_pts:.1f} pts)"
            )
            bite = SuggestionBite(
                id=f"bite-manage-t1-{rec_id}",
                entry_id=f"entry-manage-{rec_id}",
                ts=as_of,
                type="MANAGE",
                headline=headline,
                condition={"field": "spot", "op": ">=" if is_bullish else "<=", "val": t1_spot},
                action_frame="Trail Stop to Entry Breakeven & Lock 50% Profits",
                structure={"rec_id": rec_id, "play_type": play_type, "side": side},
                levels={"trigger": t1_spot, "invalidation": init_spot, "target": t2_spot},
                risk={"max_loss_per_lot": 0.0, "rr": 2.5, "breakeven": init_spot},
                valid_until=as_of + dt.timedelta(minutes=45),
                scenario_link="bull" if is_bullish else "bear",
                badge={"mode": "shadow", "calibrated": True, "pinned": True},
                facts_used=["current_spot", "target_1_spot", "initial_spot"],
                pinned_rec_id=rec_id,
            )
            bites.append(bite)
            continue  # don't emit invalidation warning if already hitting target

        # 2. Invalidation Wall Approach Warning
        inval_warning = False
        if inval_spot > 0:
            if is_bullish and current_spot < init_spot and (current_spot - inval_spot) <= 15.0:
                inval_warning = True
            elif not is_bullish and current_spot > init_spot and (inval_spot - current_spot) <= 15.0:
                inval_warning = True

        if inval_warning:
            dist = abs(current_spot - inval_spot)
            headline = f"Pinned {rec_id}: Invalidation Warning (Within {dist:.1f} pts of ₹{inval_spot:,.0f})"
            bite = SuggestionBite(
                id=f"bite-manage-inval-{rec_id}",
                entry_id=f"entry-inval-{rec_id}",
                ts=as_of,
                type="MANAGE",
                headline=headline,
                condition={"field": "spot", "op": "<=" if is_bullish else ">=", "val": inval_spot},
                action_frame="Tighten Stop or Stand Aside on Wall Invalidation",
                structure={"rec_id": rec_id, "play_type": play_type, "side": side},
                levels={"trigger": inval_spot, "invalidation": inval_spot, "target": init_spot},
                risk={"max_loss_per_lot": float(rec.get("stop_loss", 1500.0)), "rr": 1.0},
                valid_until=as_of + dt.timedelta(minutes=30),
                scenario_link="bear" if is_bullish else "bull",
                badge={"mode": "shadow", "calibrated": True, "pinned": True},
                facts_used=["current_spot", "spot_invalidation"],
                pinned_rec_id=rec_id,
            )
            bites.append(bite)

    return bites
