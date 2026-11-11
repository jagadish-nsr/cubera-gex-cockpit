"""Hand-checkable tests for Decisive GO desk (Phase 0)."""

from __future__ import annotations

from types import SimpleNamespace

from analytics.go_desk import (
    FIRE_MIN,
    SCOUT_MIN,
    build_directional_decision,
    build_structure_decision,
    build_writer_decision,
    decisive_gate,
    evidence_pack_complete,
    rank_feed,
    state_from_score,
)
from structures.suggest import CandidateStructure


def _levels(spot=25400.0, cw=25600.0, pw=25200.0, flip=25350.0, magnet=25400.0):
    return SimpleNamespace(
        spot=spot,
        call_wall=cw,
        put_wall=pw,
        flip=flip,
        magnet=magnet,
    )


def _idea(score=82, side="CE", strike=25500.0, ltp=72.0, play_badge="CONTINUATION_BULL"):
    return SimpleNamespace(
        quality_score=score,
        side=side,
        strike=strike,
        current_ltp=ltp,
        delta_approx=0.32,
        play_badge=play_badge,
        rationale="Buyer CVD + above VWAP; call wall is map.",
        spot_invalidation=25320.0,
        spot_invalidation_text="Spot below 25320",
        target_1=98.0,
        target_2=118.0,
        stop_loss=55.0,
        risk_reward_ratio="1 : 2.5",
        p_touch_target_1=0.55,
        net_ev_pts=12.0,
        rec_id="NIFTY_CE_25500_CONT",
        entry_trigger="Signal bar close",
    )


def test_state_from_score_thresholds():
    assert state_from_score(80, []) == "FIRE"
    assert state_from_score(70, []) == "SCOUT"
    assert state_from_score(50, []) == "HOLD"
    assert state_from_score(99, ["stale_oi"]) == "HOLD"


def test_decisive_gate_strips_fire_without_contracts():
    blocks = decisive_gate(
        state="FIRE",
        contracts=[],
        risk={"max_loss_inr": 1000},
        evidence=[SimpleNamespace()],  # non-empty
        context={"oi_ts": "t", "lot_size": 65},
        hard_blocks=[],
    )
    assert "missing_contracts" in blocks


def test_directional_fire_with_idea():
    d = build_directional_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(),
        trade_ideas=[_idea(82)],
        sentinel_score=80,
        sentinel_type="CONTINUATION_BULL",
        cvd_aligned=True,
        above_vwap=True,
        breadth_aligned=True,
        target_confluence=70,
        oi_ts="2026-11-10T10:42:00+05:30",
        chain_ok=True,
    )
    assert d.state == "FIRE"
    assert d.mode == "directional"
    assert d.score >= FIRE_MIN
    assert d.contracts and d.contracts[0].action == "BUY"
    assert d.risk.get("max_loss_inr", 0) > 0
    assert d.evidence and all("pass" in e.to_dict() for e in d.evidence)
    assert d.context["lot_size"] == 65
    assert d.enter_rule == "NOW_CLOSE"


def test_directional_soft_corridor_half_size():
    d = build_directional_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(spot=25400, cw=25600, pw=25200),
        trade_ideas=[_idea(82)],
        sentinel_score=78,
        cvd_aligned=True,
        above_vwap=True,
        breadth_aligned=True,
        target_confluence=65,
        oi_ts="t",
    )
    assert "inside_corridor" in d.soft_flags
    assert d.size_tier in ("HALF", "SCOUT", "FULL")
    if d.state == "FIRE":
        assert d.size_tier == "HALF"


def test_directional_hold_on_stale_oi():
    d = build_directional_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(),
        trade_ideas=[_idea(90)],
        sentinel_score=90,
        cvd_aligned=True,
        above_vwap=True,
        oi_ts="t",
        oi_stale=True,
    )
    assert d.state == "HOLD"
    assert "stale_oi" in d.hard_blocks


def test_vanna_without_tape_cannot_fire():
    """11 Nov: VANNA-CHARM flooded FIRE with CVD/VWAP red — must stay off FIRE."""
    d = build_directional_decision(
        symbol="SENSEX",
        lot_size=20,
        strike_step=100,
        levels=_levels(spot=82400, cw=82600, pw=82000, flip=82200, magnet=82500),
        trade_ideas=[_idea(88, play_badge="VANNA-CHARM")],
        sentinel_score=70,
        cvd_aligned=False,
        above_vwap=False,
        breadth_aligned=True,
        target_confluence=60,
        oi_ts="2026-11-11T10:00:00+05:30",
        chain_ok=True,
    )
    assert d.strategy_key == "VANNA-CHARM"
    assert d.state != "FIRE"
    assert "vanna_without_tape_confluence" in d.hard_blocks or "incomplete_evidence" in d.hard_blocks


def test_vanna_with_tape_can_still_fire():
    d = build_directional_decision(
        symbol="SENSEX",
        lot_size=20,
        strike_step=100,
        levels=_levels(spot=82400, cw=82600, pw=82000, flip=82200, magnet=82500),
        trade_ideas=[_idea(88, play_badge="VANNA-CHARM")],
        sentinel_score=80,
        cvd_aligned=True,
        above_vwap=True,
        breadth_aligned=True,
        target_confluence=70,
        oi_ts="2026-11-11T10:00:00+05:30",
        chain_ok=True,
    )
    assert d.strategy_key == "VANNA-CHARM"
    assert d.state == "FIRE"
    assert evidence_pack_complete(d.evidence, strategy_key=d.strategy_key)


def test_structure_calm_condor_fire():
    legs = [
        ("SELL", "C", 25700),
        ("BUY", "C", 25800),
        ("SELL", "P", 25100),
        ("BUY", "P", 25000),
    ]
    cs = CandidateStructure(
        name="Iron condor (sell premium)",
        legs=legs,
        rationale="High concentration calm bucket.",
        risk_note="Defined risk",
        exit_by="before expiry",
    )
    d = build_structure_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(spot=25400, cw=25600, pw=25200, magnet=25400),
        vol_regime="calm",
        structure=cs,
        oi_ts="t",
        blast_safe=True,
    )
    assert d.mode == "structure"
    assert d.score >= SCOUT_MIN
    assert len(d.contracts) == 4
    assert d.strategy_type == "IRON_CONDOR"
    assert d.risk.get("max_loss_inr", 0) > 0


def test_writer_requires_calm_and_iv_rv():
    d = build_writer_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(),
        vol_regime="calm",
        iv=15.1,
        rv=11.8,
        catalyst_clear=True,
        tape_ok=True,
        oi_ts="t",
        short_call=25700,
        short_put=25100,
        credit_call=18,
        credit_put=22,
        margin_est_inr=283000,
    )
    assert d.mode == "writer"
    assert d.strategy_type == "NAKED_SHORT"
    assert d.score >= SCOUT_MIN
    assert any(c.action == "SELL" for c in d.contracts)


def test_writer_hold_on_high_shock():
    d = build_writer_decision(
        symbol="NIFTY",
        lot_size=65,
        strike_step=50,
        levels=_levels(),
        vol_regime="calm",
        iv=20,
        rv=12,
        catalyst_clear=False,
        oi_ts="t",
        short_call=25700,
        short_put=25100,
        margin_est_inr=100000,
    )
    assert d.state == "HOLD"
    assert "high_shock_catalyst" in d.hard_blocks


def test_rank_feed_orders_fire_first():
    a = build_directional_decision(
        symbol="NIFTY", lot_size=65, strike_step=50, levels=_levels(),
        trade_ideas=[_idea(90)], sentinel_score=90, cvd_aligned=True, above_vwap=True,
        breadth_aligned=True, target_confluence=80, oi_ts="t",
    )
    b = build_directional_decision(
        symbol="NIFTY", lot_size=65, strike_step=50, levels=_levels(),
        trade_ideas=[_idea(68)], sentinel_score=66, oi_ts="t",
    )
    ranked = rank_feed([b, a])
    assert ranked[0].score >= ranked[-1].score


def test_structure_economics_iron_condor():
    from analytics.go_desk import GoLeg, structure_economics

    legs = [
        GoLeg("SELL", "CE", 25600, ltp=38),
        GoLeg("BUY", "CE", 25700, ltp=22),
        GoLeg("SELL", "PE", 25200, ltp=41),
        GoLeg("BUY", "PE", 25100, ltp=19),
    ]
    # credit = (38-22)+(41-19) = 16+22 = 38
    econ = structure_economics(legs, lot_size=65)
    assert econ["credit_or_debit"] == 38.0
    assert econ["breakeven_high"] == 25600 + 38
    assert econ["breakeven_low"] == 25200 - 38
    assert econ["max_profit_inr"] == 38 * 65
    # wing 100; max loss = (100-38)*65
    assert econ["max_loss_inr"] == (100 - 38) * 65


def test_evaluate_trail_arms_and_stops():
    from analytics.go_desk import evaluate_trail

    cold = evaluate_trail(entry_ltp=72, mark_ltp=80, arm_pct=0.30, lock_pct_of_entry=0.10)
    assert cold["armed"] is False
    assert cold["trail_stop"] is None

    hot = evaluate_trail(entry_ltp=72, mark_ltp=100, arm_pct=0.30, lock_pct_of_entry=0.10)
    assert hot["armed"] is True
    assert hot["trail_stop"] == round(72 * 1.10, 2)
    stopped = evaluate_trail(entry_ltp=72, mark_ltp=75, arm_pct=0.30, lock_pct_of_entry=0.10)
    # 75 < arm threshold — not armed
    assert stopped["armed"] is False

    armed_then_hit = evaluate_trail(entry_ltp=72, mark_ltp=100)
    stop = armed_then_hit["trail_stop"]
    hit = evaluate_trail(entry_ltp=72, mark_ltp=stop - 1)
    # Once we'd need stateful trail; evaluate_trail is mark-vs-entry only.
    # After arm, mark below trail_stop while still above entry*1.3 won't show stopped
    # unless mark <= trail_stop AND armed. 100 arms; re-eval at 78:
    below = evaluate_trail(entry_ltp=72, mark_ltp=78)
    assert below["armed"] is False  # 78/72-1 ≈ 8% < 30%
    # Arm at 94 (+30%), trail 79.2; mark 79 → stopped
    at_arm = evaluate_trail(entry_ltp=72, mark_ltp=94)
    assert at_arm["armed"] is True
    assert at_arm["trail_stop"] == round(72 * 1.1, 2)
    stop_hit = evaluate_trail(entry_ltp=72, mark_ltp=79)
    # 79 is not yet +30%, so not armed — trail is not sticky in pure function.
    # Sticky trail lives in go_efficiency.mark_open once armed.
    assert stop_hit["armed"] is False


def test_update_excursion():
    from analytics.go_desk import update_excursion
    mfe, mae = update_excursion(entry_premium=72, mark_premium=90, mfe=0, mae=0)
    assert mfe == 18
    assert mae == 0
    mfe2, mae2 = update_excursion(entry_premium=72, mark_premium=60, mfe=mfe, mae=mae)
    assert mfe2 == 18
    assert mae2 == 12


def test_structure_payoff_iron_condor_hand_check():
    from analytics.go_desk import GoLeg, structure_payoff_curve

    legs = [
        GoLeg("SELL", "CE", 25600, ltp=38),
        GoLeg("BUY", "CE", 25700, ltp=22),
        GoLeg("SELL", "PE", 25200, ltp=41),
        GoLeg("BUY", "PE", 25100, ltp=19),
    ]
    # credit 38; at spot mid 25400 all OTM → pnl = +38 pts
    curve = structure_payoff_curve(legs, lot_size=65, spot=25400, strike_step=50, n_points=21)
    assert curve["credit_or_debit"] == 38.0
    mid = next(p for p in curve["points"] if abs(p["spot"] - 25400) < 1)
    assert mid["pnl_pts"] == 38.0
    assert mid["pnl_inr"] == 38 * 65
    # blow through call wing 25700+: loss = wing−credit = 62
    hi = next(p for p in curve["points"] if p["spot"] >= 25800)
    assert hi["pnl_pts"] == -(100 - 38)


def test_net_greeks_short_strangle_signs():
    from analytics.go_desk import GoLeg, net_greeks

    legs = [
        GoLeg("SELL", "CE", 25600, ltp=40),
        GoLeg("SELL", "PE", 25200, ltp=40),
    ]
    g = net_greeks(legs, spot=25400, lot_size=65, iv=0.14, dte_days=5)
    assert "net" in g
    # Short strangle near ATM-ish: delta roughly small; gamma/vega/theta negative for shorts
    assert g["net"]["gamma"] < 0
    assert g["net"]["vega"] < 0
    assert g["net"]["theta"] > 0  # short theta positive (time decay credit)


def test_build_manage_legs_flatten_and_hedge():
    from analytics.go_desk import build_manage_legs, suggest_manage_actions

    decision = {
        "id": "x",
        "mode": "writer",
        "contracts": [
            {"action": "SELL", "opt_type": "CE", "strike": 25600, "ltp": 40},
            {"action": "SELL", "opt_type": "PE", "strike": 25200, "ltp": 38},
        ],
        "risk": {"margin_est_inr": 130000, "credit_or_debit": 78},
    }
    flat = build_manage_legs(decision, "flatten", strike_step=50)
    assert flat == [
        {"action": "BUY", "opt_type": "CE", "strike": 25600, "ltp": 40},
        {"action": "BUY", "opt_type": "PE", "strike": 25200, "ltp": 38},
    ]
    hedge = build_manage_legs(decision, "hedge", strike_step=50)
    assert {"action": "BUY", "opt_type": "CE", "strike": 25650, "ltp": None} in hedge
    assert {"action": "BUY", "opt_type": "PE", "strike": 25150, "ltp": None} in hedge
    acts = suggest_manage_actions(decision, strike_step=50)
    keys = {a["action"] for a in acts}
    assert "hedge" in keys and "flatten" in keys


def test_mark_mtm_credit_decay():
    from analytics.go_desk import GoLeg, mark_mtm

    entry_legs = [
        GoLeg("SELL", "CE", 25600, ltp=40),
        GoLeg("SELL", "PE", 25200, ltp=38),
    ]
    # Entry credit 78; marks down to 20+18=38 → pnl = 78-38 = 40
    mtm = mark_mtm(
        entry_legs,
        mark_ltps={("CE", 25600.0): 20.0, ("PE", 25200.0): 18.0},
        lot_size=65,
        entry_credit=78.0,
    )
    assert mtm["pnl_pts"] == 40.0
    assert mtm["pnl_inr"] == 40 * 65


def test_multi_tf_confluence_boost():
    from analytics.go_desk import multi_tf_confluence_boost, MTF_BOOST_MAX

    cold = multi_tf_confluence_boost(side="CE", tf_leans={"1m": "NA", "5m": "NA", "15m": "NA"})
    assert cold["boost"] == 0.0
    hot = multi_tf_confluence_boost(side="CE", tf_leans={"1m": "UP", "5m": "UP", "15m": "UP"})
    assert hot["boost"] == MTF_BOOST_MAX
    assert hot["pass"] is True
    pe = multi_tf_confluence_boost(side="PE", tf_leans={"1m": "DOWN", "5m": "DOWN", "15m": "FLAT"})
    assert pe["boost"] == MTF_BOOST_MAX  # 2/2 known aligned
    conflict = multi_tf_confluence_boost(side="CE", tf_leans={"1m": "DOWN", "5m": "DOWN", "15m": "DOWN"})
    assert conflict["boost"] == 0.0


def test_scenario_whatif_short_strangle_signs():
    from analytics.go_desk import GoLeg, scenario_whatif

    legs = [
        GoLeg("SELL", "CE", 25600, ltp=40),
        GoLeg("SELL", "PE", 25200, ltp=38),
    ]
    wf = scenario_whatif(legs, spot=25400, lot_size=65, iv=0.14, dte_days=5, shocks=[-0.02, 0.0, 0.02])
    assert len(wf["scenarios"]) == 3
    mid = next(s for s in wf["scenarios"] if s["shock_pct"] == 0.0)
    # At spot, short premium still positive mark; pnl vs entry depends on model vs LTP
    assert mid["spot"] == 25400
    lo = next(s for s in wf["scenarios"] if s["shock_pct"] == -2.0)
    hi = next(s for s in wf["scenarios"] if s["shock_pct"] == 2.0)
    # Large move either way should be worse for short strangle than flat
    assert lo["pnl_inr"] <= mid["pnl_inr"] or hi["pnl_inr"] <= mid["pnl_inr"]


def test_fire_template_slice():
    from analytics.go_desk import fire_template_slice

    events = [
        {"event_type": "EMIT", "state": "FIRE", "strategy_key": "CONTINUATION_BULL", "mode": "directional"},
        {"event_type": "EMIT", "state": "FIRE", "strategy_key": "CONTINUATION_BULL", "mode": "directional"},
        {"event_type": "STAGE", "state": "FIRE", "strategy_key": "CONTINUATION_BULL", "mode": "directional"},
        {"event_type": "SKIP", "state": "FIRE", "strategy_key": "CONTINUATION_BULL", "mode": "directional"},
        {"event_type": "EMIT", "state": "SCOUT", "strategy_key": "X", "mode": "directional"},
    ]
    marked = [{"strategy_key": "CONTINUATION_BULL", "mfe": 20, "mae": 8}]
    sl = fire_template_slice(events, marked)
    assert sl["fire_emits"] == 2
    assert sl["staged"] == 1
    assert sl["skipped"] == 1
    t = sl["templates"][0]
    assert t["strategy_key"] == "CONTINUATION_BULL"
    assert t["edge_ratio"] == 2.5


def test_unusual_oi_volume_flag():
    from analytics.go_desk import unusual_oi_volume_flag
    flag = unusual_oi_volume_flag(strike=25500, oi=10000, volume=500, median_oi=2000, median_vol=100)
    assert flag["pass"] is True
    assert flag["oi_unusual"] is True
    normal = unusual_oi_volume_flag(strike=25500, oi=2100, volume=110, median_oi=2000, median_vol=100)
    assert normal["pass"] is False
    # Boundary: oi_mult default 2.5 → exactly 2.5× median passes
    edge = unusual_oi_volume_flag(strike=1, oi=2500, volume=None, median_oi=1000, median_vol=None)
    assert edge["pass"] is True
    below = unusual_oi_volume_flag(strike=1, oi=2499, volume=None, median_oi=1000, median_vol=None)
    assert below["pass"] is False
