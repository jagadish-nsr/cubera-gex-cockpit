"""Decisive GO desk — unify Trade Ideas + Sentinel + structures into FIRE/SCOUT/HOLD.

Phase 0 façade. No auto-execution. GEX is a map, not a dealer-defense forecast.
Every FIRE/SCOUT carries an evidence pack the trader can double-check.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

GoState = Literal["FIRE", "SCOUT", "HOLD"]
GoMode = Literal["directional", "structure", "writer"]
EnterRule = Literal["NOW_CLOSE", "VWAP_RECLAIM", "WALL_HOLD", "OPEN_AUCTION_PLAN"]
SizeTier = Literal["FULL", "HALF", "SCOUT"]

FIRE_MIN = 75
SCOUT_MIN = 65

DIRECTIONAL_WEIGHTS = {
    "sentinel_or_idea": 0.40,
    "trade_idea": 0.25,
    "cvd_vwap": 0.15,
    "breadth": 0.10,
    "target_engine": 0.10,
}

STRUCTURE_WEIGHTS = {
    "abs_gex_fit": 0.45,
    "corridor": 0.20,
    "iv_context": 0.15,
    "sentinel_fit": 0.10,
    "blast_safety": 0.10,
}

WRITER_WEIGHTS = {
    "abs_gex_calm": 0.35,
    "iv_rv": 0.25,
    "distance_beyond_walls": 0.15,
    "catalyst_clear": 0.15,
    "tape_ok": 0.10,
}

STRATEGY_TYPES = (
    "NAKED_LONG",
    "NAKED_SHORT",
    "VERTICAL",
    "IRON_CONDOR",
    "STRADDLE",
    "STRANGLE",
    "CALENDAR",
    "DIAGONAL",
    "CUSTOM",
)


@dataclass
class EvidenceItem:
    factor: str
    label: str
    value: Any
    threshold: str
    pass_: bool
    weight: float
    source_ref: str
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pass"] = d.pop("pass_")
        return d


@dataclass
class GoLeg:
    action: str  # BUY | SELL
    opt_type: str  # CE | PE
    strike: float
    expiry: str = ""
    delta_target: float | None = None
    ltp: float | None = None
    bid: float | None = None
    ask: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoDecision:
    id: str
    state: GoState
    mode: GoMode
    symbol: str
    score: float
    score_weights: dict[str, float]
    strategy_key: str
    thesis: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    soft_flags: list[str] = field(default_factory=list)
    hard_blocks: list[str] = field(default_factory=list)
    invalidation: dict = field(default_factory=dict)
    enter_rule: EnterRule = "NOW_CLOSE"
    size_tier: SizeTier = "FULL"
    contracts: list[GoLeg] = field(default_factory=list)
    risk: dict = field(default_factory=dict)
    targets: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    deep_links: dict = field(default_factory=dict)
    expires_at: str = ""
    emitted_at: str = ""
    strategy_type: str = "CUSTOM"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state,
            "mode": self.mode,
            "symbol": self.symbol,
            "score": round(float(self.score), 1),
            "score_weights": dict(self.score_weights),
            "strategy_key": self.strategy_key,
            "thesis": self.thesis,
            "evidence": [e.to_dict() for e in self.evidence],
            "soft_flags": list(self.soft_flags),
            "hard_blocks": list(self.hard_blocks),
            "invalidation": dict(self.invalidation),
            "enter_rule": self.enter_rule,
            "size_tier": self.size_tier,
            "contracts": [c.to_dict() for c in self.contracts],
            "risk": dict(self.risk),
            "targets": dict(self.targets),
            "context": dict(self.context),
            "sources": dict(self.sources),
            "deep_links": dict(self.deep_links),
            "expires_at": self.expires_at,
            "emitted_at": self.emitted_at,
            "strategy_type": self.strategy_type,
        }


def _ist_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def _iso(dt: datetime | None = None) -> str:
    return (dt or _ist_now()).isoformat(timespec="seconds")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _ev(
    factor: str,
    label: str,
    value: Any,
    threshold: str,
    passed: bool,
    weight: float,
    source_ref: str,
    note: str = "",
) -> EvidenceItem:
    return EvidenceItem(
        factor=factor,
        label=label,
        value=value,
        threshold=threshold,
        pass_=bool(passed),
        weight=float(weight),
        source_ref=source_ref,
        note=note,
    )


def _evidence_pass(item: Any) -> bool:
    if isinstance(item, EvidenceItem):
        return bool(item.pass_)
    if isinstance(item, dict):
        return bool(item.get("pass", item.get("pass_", False)))
    return bool(getattr(item, "pass_", getattr(item, "pass", False)))


def _evidence_factor(item: Any) -> str:
    if isinstance(item, EvidenceItem):
        return str(item.factor or "")
    if isinstance(item, dict):
        return str(item.get("factor") or "")
    return str(getattr(item, "factor", "") or "")


def _evidence_weight(item: Any) -> float:
    if isinstance(item, EvidenceItem):
        return float(item.weight or 0.0)
    if isinstance(item, dict):
        return float(item.get("weight") or 0.0)
    return float(getattr(item, "weight", 0.0) or 0.0)


def evidence_pack_complete(evidence: list[Any] | None, *, strategy_key: str = "") -> bool:
    """True when weighted evidence that matters for FIRE is actually green.

    Empty pack = incomplete. Primary setup must pass. VANNA-CHARM also needs
    CVD/VWAP alignment (11 Nov: VANNA flooded FIRE with tape factors red).
    """
    if not evidence:
        return False
    by_factor = {_evidence_factor(e): e for e in evidence}
    primary = by_factor.get("sentinel_or_idea") or by_factor.get("trade_idea")
    if primary is not None and not _evidence_pass(primary):
        return False
    weighted = [e for e in evidence if _evidence_weight(e) > 0]
    if weighted:
        passed = sum(_evidence_weight(e) for e in weighted if _evidence_pass(e))
        total = sum(_evidence_weight(e) for e in weighted)
        if total > 0 and (passed / total) < 0.50:
            return False
    sk = (strategy_key or "").upper().replace(" ", "_")
    if "VANNA" in sk:
        tape = by_factor.get("cvd_vwap")
        if tape is not None and not _evidence_pass(tape):
            return False
    return True


def decisive_gate(
    *,
    state: GoState,
    contracts: list[GoLeg],
    risk: dict,
    evidence: list[EvidenceItem],
    context: dict,
    hard_blocks: list[str],
    strategy_key: str = "",
) -> list[str]:
    """Return hard blocks that strip FIRE. SCOUT/HOLD may keep incomplete packs."""
    blocks = list(hard_blocks)
    if state != "FIRE":
        return blocks
    if not contracts:
        blocks.append("missing_contracts")
    max_loss = risk.get("max_loss_inr")
    if max_loss is None or float(max_loss) <= 0:
        # Writers may use margin_est; allow if margin present
        if risk.get("margin_est_inr") is None and risk.get("credit_or_debit") is None:
            blocks.append("missing_risk")
    if not evidence:
        blocks.append("missing_evidence")
    elif not evidence_pack_complete(evidence, strategy_key=strategy_key):
        blocks.append("incomplete_evidence")
    if not context.get("oi_ts") and context.get("oi_stale") is True:
        blocks.append("stale_oi")
    if not context.get("lot_size"):
        blocks.append("missing_lot_size")
    sk = (strategy_key or "").upper().replace(" ", "_")
    if "VANNA" in sk:
        # Charm map without tape confluence is SCOUT material, not FIRE.
        by_factor = {_evidence_factor(e): e for e in (evidence or [])}
        tape = by_factor.get("cvd_vwap")
        if tape is not None and not _evidence_pass(tape):
            blocks.append("vanna_without_tape_confluence")
    return blocks


def state_from_score(score: float, hard_blocks: list[str]) -> GoState:
    if hard_blocks:
        return "HOLD"
    if score >= FIRE_MIN:
        return "FIRE"
    if score >= SCOUT_MIN:
        return "SCOUT"
    return "HOLD"


def _inside_corridor(spot: float, put_wall: float, call_wall: float, step: float) -> bool:
    if put_wall <= 0 or call_wall <= 0 or step <= 0:
        return False
    lo, hi = min(put_wall, call_wall), max(put_wall, call_wall)
    return (lo + step) < spot < (hi - step)


def _pick_best_idea(ideas: list[Any]) -> Any | None:
    if not ideas:
        return None
    return max(ideas, key=lambda i: int(getattr(i, "quality_score", 0) or 0))


def build_directional_decision(
    *,
    symbol: str,
    lot_size: int,
    strike_step: int,
    levels: Any,
    trade_ideas: list[Any] | None = None,
    sentinel_score: float | None = None,
    sentinel_type: str | None = None,
    cvd_aligned: bool | None = None,
    above_vwap: bool | None = None,
    breadth_aligned: bool | None = None,
    target_confluence: float | None = None,
    oi_ts: str | None = None,
    oi_stale: bool = False,
    chain_ok: bool = True,
    lunch_chop: bool = False,
    blast_tag: str | None = None,
    delta_pref: float = 0.50,
    alert_headline: str | None = None,
    mtf_leans: dict[str, str] | None = None,
    unusual_strike: dict | None = None,
) -> GoDecision:
    """Build directional GO from idea/sentinel confluence inputs."""
    weights = dict(DIRECTIONAL_WEIGHTS)
    evidence: list[EvidenceItem] = []
    soft: list[str] = []
    hard: list[str] = []

    spot = float(getattr(levels, "spot", 0) or 0)
    cw = float(getattr(levels, "call_wall", 0) or 0)
    pw = float(getattr(levels, "put_wall", 0) or 0)
    flip = float(getattr(levels, "flip", 0) or 0)
    magnet = float(getattr(levels, "magnet", 0) or 0)

    if not chain_ok or spot <= 0:
        hard.append("placeholder_or_missing_spot")
    if oi_stale:
        hard.append("stale_oi")

    idea = _pick_best_idea(list(trade_ideas or []))
    idea_score = float(getattr(idea, "quality_score", 0) or 0) if idea else 0.0
    sent = float(sentinel_score or 0.0)
    primary = max(idea_score, sent)
    side_guess = str(getattr(idea, "side", "") or "").upper()
    if not side_guess and sentinel_type:
        side_guess = "CE" if "BULL" in str(sentinel_type).upper() else "PE"
    if not side_guess:
        side_guess = "CE"
    evidence.append(
        _ev(
            "sentinel_or_idea",
            "Primary setup score",
            round(primary, 1),
            f"≥ {FIRE_MIN} FIRE / ≥ {SCOUT_MIN} SCOUT",
            primary >= SCOUT_MIN,
            weights["sentinel_or_idea"],
            "trade_ideas|sentinel",
            note=str(sentinel_type or getattr(idea, "play_badge", "") or ""),
        )
    )
    evidence.append(
        _ev(
            "trade_idea",
            "Trade Ideas quality",
            round(idea_score, 1),
            "≥ 65 contributes",
            idea_score >= SCOUT_MIN,
            weights["trade_idea"],
            "analytics.trade_ideas",
        )
    )

    cvd_ok = bool(cvd_aligned) if cvd_aligned is not None else False
    vwap_ok = bool(above_vwap) if above_vwap is not None else False
    tape_pts = 100.0 if (cvd_ok and vwap_ok) else (55.0 if (cvd_ok or vwap_ok) else 20.0)
    evidence.append(
        _ev(
            "cvd_vwap",
            "CVD / VWAP alignment",
            {"cvd": cvd_aligned, "above_vwap": above_vwap},
            "both preferred",
            cvd_ok and vwap_ok,
            weights["cvd_vwap"],
            "sentinel.tape",
        )
    )
    br_ok = bool(breadth_aligned) if breadth_aligned is not None else False
    br_pts = 100.0 if br_ok else 40.0
    evidence.append(
        _ev(
            "breadth",
            "Sector breadth alignment",
            breadth_aligned,
            "aligned with side",
            br_ok,
            weights["breadth"],
            "market_intelligence",
        )
    )
    te = float(target_confluence or 0.0)
    evidence.append(
        _ev(
            "target_engine",
            "Target Engine confluence",
            round(te, 1),
            "≥ 50 contributes / ≥ 65 strong",
            te >= 50,
            weights["target_engine"],
            "target_engine",
        )
    )

    mtf = multi_tf_confluence_boost(side=side_guess, tf_leans=mtf_leans)
    evidence.append(
        _ev(
            "multi_tf",
            "Multi-TF lean confluence",
            mtf.get("leans"),
            f"≥2 aligned with {mtf.get('want')} (soft +{MTF_BOOST_MAX:.0f} max)",
            bool(mtf.get("pass")),
            0.0,  # soft boost outside weights
            "sentinel.multi_tf",
            note=f"aligned {mtf.get('aligned')}/{mtf.get('known')} boost +{mtf.get('boost')}",
        )
    )
    if unusual_strike and unusual_strike.get("pass"):
        evidence.append(
            _ev(
                "unusual_oi_vol",
                "Unusual OI/volume at strike",
                unusual_strike.get("detail"),
                "OI or vol ≫ session median",
                True,
                0.0,
                "chain.oi_volume",
            )
        )
        soft.append("unusual_oi_vol")

    score = _clamp(
        weights["sentinel_or_idea"] * primary
        + weights["trade_idea"] * idea_score
        + weights["cvd_vwap"] * tape_pts
        + weights["breadth"] * br_pts
        + weights["target_engine"] * te
        + float(mtf.get("boost") or 0.0)
    )

    step = float(strike_step or 50)
    in_corr = _inside_corridor(spot, pw, cw, step)
    if in_corr and not (sentinel_type and "BREAK" in str(sentinel_type).upper()):
        soft.append("inside_corridor")
    if lunch_chop:
        soft.append("lunch_chop")
    if blast_tag and str(blast_tag).upper() in ("ACTIVE", "COILING"):
        soft.append(f"blast_{str(blast_tag).lower()}")

    contracts: list[GoLeg] = []
    targets: dict = {}
    invalidation: dict = {}
    risk: dict = {}
    strategy_key = "HOLD"
    thesis = "No directional setup cleared the decisive gate."
    enter: EnterRule = "NOW_CLOSE"
    sources: dict = {}
    strategy_type = "NAKED_LONG"

    if idea is not None:
        side = str(getattr(idea, "side", "CE") or "CE").upper()
        strike = float(getattr(idea, "strike", 0) or 0)
        # Soft delta preference: nudge one step OTM when δ≈0.35/0.25 requested
        if delta_pref <= 0.30 and strike > 0 and step > 0:
            strike = strike + step if side == "CE" else strike - step
        elif delta_pref <= 0.40 and strike > 0 and step > 0:
            # mild OTM
            strike = strike + step if side == "CE" else strike - step
        ltp = float(getattr(idea, "current_ltp", 0) or 0)
        contracts = [
            GoLeg(
                action="BUY",
                opt_type=side,
                strike=strike,
                delta_target=float(getattr(idea, "delta_approx", 0) or 0) or delta_pref,
                ltp=ltp or None,
            )
        ]
        strategy_key = str(getattr(idea, "play_badge", "MOMENTUM") or "MOMENTUM").replace(" ", "_")
        thesis = str(getattr(idea, "rationale", "") or "")[:220] or (
            f"{strategy_key} {side} — map + tape confluence."
        )
        invalidation = {
            "rule": str(getattr(idea, "spot_invalidation_text", "") or "spot invalidation"),
            "spot_level": float(getattr(idea, "spot_invalidation", 0) or 0),
        }
        targets = {
            "t1": float(getattr(idea, "target_1", 0) or 0),
            "t2": float(getattr(idea, "target_2", 0) or 0),
            "sl": float(getattr(idea, "stop_loss", 0) or 0),
            "touch_p": float(getattr(idea, "p_touch_target_1", 0) or 0),
            "ev": float(getattr(idea, "net_ev_pts", 0) or 0),
            "trail_rule": "after_+30pct_premium",
        }
        max_loss = max(ltp, float(getattr(idea, "stop_loss", 0) or 0)) * float(lot_size)
        if max_loss <= 0 and ltp > 0:
            max_loss = ltp * float(lot_size)
        risk = {
            "max_loss_inr": round(max_loss, 0),
            "credit_or_debit": -ltp if ltp else None,
            "rr": getattr(idea, "risk_reward_ratio", None),
        }
        sources = {
            "trade_idea_id": getattr(idea, "rec_id", None),
            "sentinel_type": sentinel_type,
            "alert_tape_headline": alert_headline,
        }
        # Enter rule heuristic
        trigger = str(getattr(idea, "entry_trigger", "") or "").lower()
        if "vwap" in trigger:
            enter = "VWAP_RECLAIM"
        elif "wall" in trigger:
            enter = "WALL_HOLD"
        else:
            enter = "NOW_CLOSE"
    elif sent >= SCOUT_MIN and sentinel_type:
        side = "CE" if "BULL" in str(sentinel_type).upper() else "PE"
        atm = round(spot / step) * step if step else spot
        contracts = [GoLeg(action="BUY", opt_type=side, strike=float(atm), delta_target=delta_pref)]
        strategy_key = str(sentinel_type).replace(" ", "_")
        thesis = f"Sentinel {sentinel_type} without a Trade Ideas latch — SCOUT until premium filled."
        risk = {"max_loss_inr": None}
        sources = {"sentinel_type": sentinel_type, "alert_tape_headline": alert_headline}

    size: SizeTier = "FULL"
    if "inside_corridor" in soft or "lunch_chop" in soft:
        size = "HALF"
    if score < FIRE_MIN:
        size = "SCOUT"

    provisional = state_from_score(score, [])
    context = {
        "oi_ts": oi_ts,
        "oi_stale": oi_stale,
        "lot_size": int(lot_size),
        "call_wall": cw,
        "put_wall": pw,
        "flip": flip,
        "magnet": magnet,
        "spot": spot,
        "abs_gex_bucket": None,
        "blast_tag": blast_tag,
        "dealer_sign_model": "short_call_short_put",
    }
    # Only run FIRE completeness gate when score would otherwise FIRE
    gate_state: GoState = "FIRE" if provisional == "FIRE" else provisional
    hard = decisive_gate(
        state=gate_state,
        contracts=contracts,
        risk=risk,
        evidence=evidence,
        context=context,
        hard_blocks=hard,
        strategy_key=strategy_key,
    )
    state = state_from_score(score, hard)
    if state == "HOLD":
        size = "SCOUT"
        thesis = thesis if hard else "Score below SCOUT — stand aside."
        if hard:
            thesis = f"HOLD — {', '.join(hard)}. " + thesis

    return GoDecision(
        id=str(uuid.uuid4()),
        state=state,
        mode="directional",
        symbol=symbol.upper(),
        score=score,
        score_weights=weights,
        strategy_key=strategy_key,
        thesis=thesis[:280],
        evidence=evidence,
        soft_flags=soft,
        hard_blocks=hard,
        invalidation=invalidation,
        enter_rule=enter,
        size_tier=size,
        contracts=contracts,
        risk=risk,
        targets=targets,
        context=context,
        sources=sources,
        deep_links={
            "sentinel": "/?view=sentinel",
            "target_engine": "/?view=target_engine",
            "recommendations": "/?view=recommendations",
        },
        expires_at=_iso(_ist_now() + timedelta(minutes=5)),
        emitted_at=_iso(),
        strategy_type=strategy_type,
    )


def build_structure_decision(
    *,
    symbol: str,
    lot_size: int,
    strike_step: int,
    levels: Any,
    vol_regime: str,
    structure: Any | None,
    oi_ts: str | None = None,
    oi_stale: bool = False,
    iv_rank_ok: bool | None = None,
    sentinel_range_ok: bool | None = None,
    blast_safe: bool = True,
    alert_headline: str | None = None,
    leg_ltps: dict[tuple[str, float], float] | None = None,
    # Optional keyed by (opt, strike, expiry_tag) for calendars; falls back to (opt, strike)
    leg_ltps_dated: dict[tuple[str, float, str], float] | None = None,
    near_atm_iv: float | None = None,
    next_atm_iv: float | None = None,
) -> GoDecision:
    weights = dict(STRUCTURE_WEIGHTS)
    evidence: list[EvidenceItem] = []
    soft: list[str] = []
    hard: list[str] = []
    ltp_map = leg_ltps or {}
    ltp_dated = leg_ltps_dated or {}

    spot = float(getattr(levels, "spot", 0) or 0)
    cw = float(getattr(levels, "call_wall", 0) or 0)
    pw = float(getattr(levels, "put_wall", 0) or 0)
    flip = float(getattr(levels, "flip", 0) or 0)
    magnet = float(getattr(levels, "magnet", 0) or 0)
    step = float(strike_step or 50)
    vr = (vol_regime or "mid").lower()

    if spot <= 0:
        hard.append("placeholder_or_missing_spot")
    if oi_stale:
        hard.append("stale_oi")

    fit = 100.0 if vr in ("calm", "wild") else 35.0
    evidence.append(
        _ev("abs_gex_fit", "Absolute GEX bucket", vr, "calm|wild", vr in ("calm", "wild"), weights["abs_gex_fit"], "structures.suggest")
    )
    corr = _inside_corridor(spot, pw, cw, step) if vr == "calm" else True
    if vr == "calm" and not corr:
        soft.append("spot_outside_corridor")
    evidence.append(
        _ev("corridor", "Corridor geometry", {"spot": spot, "pw": pw, "cw": cw}, "inside for calm short-vol", corr, weights["corridor"], "gex.levels")
    )
    iv_ok = True if iv_rank_ok is None else bool(iv_rank_ok)
    term_edge = None
    if near_atm_iv is not None and next_atm_iv is not None:
        n = float(near_atm_iv)
        x = float(next_atm_iv)
        if n > 1.5:
            n /= 100.0
        if x > 1.5:
            x /= 100.0
        term_edge = round((n - x) * 100.0, 1)
        if term_edge >= 1.0:
            iv_ok = True
    evidence.append(
        _ev(
            "iv_context",
            "IV / term context",
            {"iv_rank_ok": iv_rank_ok, "front_rich_pts": term_edge},
            "optional · front-rich ≥1pt enables calendar alt",
            iv_ok,
            weights["iv_context"],
            "iv_skew",
        )
    )
    sent_ok = True if sentinel_range_ok is None else bool(sentinel_range_ok)
    evidence.append(
        _ev("sentinel_fit", "Tape fit for structure", sentinel_range_ok, "range/break agrees", sent_ok, weights["sentinel_fit"], "sentinel")
    )
    evidence.append(
        _ev("blast_safety", "Blast safety", blast_safe, "not active near magnet short", blast_safe, weights["blast_safety"], "gammablast")
    )
    if not blast_safe:
        hard.append("blast_active_near_short")

    score = _clamp(
        weights["abs_gex_fit"] * fit
        + weights["corridor"] * (100.0 if corr else 25.0)
        + weights["iv_context"] * (100.0 if iv_ok else 40.0)
        + weights["sentinel_fit"] * (100.0 if sent_ok else 40.0)
        + weights["blast_safety"] * (100.0 if blast_safe else 0.0)
    )

    contracts: list[GoLeg] = []
    risk: dict = {}
    strategy_key = "NO_TRADE"
    thesis = "Absolute GEX mid — wait."
    strategy_type = "CUSTOM"
    name = getattr(structure, "name", "") if structure else ""

    if structure and getattr(structure, "legs", None):
        for leg in structure.legs:
            action, typ, strike = leg[0], leg[1], leg[2]
            exp_tag = str(leg[3]) if len(leg) > 3 else ""
            opt = "CE" if str(typ).upper().startswith("C") else "PE"
            k = float(strike)
            px = None
            if exp_tag and (opt, k, exp_tag) in ltp_dated:
                px = ltp_dated[(opt, k, exp_tag)]
            if px is None:
                px = ltp_map.get((opt, k))
            if px is None:
                px = ltp_map.get((opt[0], k))
            contracts.append(
                GoLeg(
                    action=str(action).upper(),
                    opt_type=opt,
                    strike=k,
                    expiry=exp_tag,
                    ltp=float(px) if px is not None else None,
                )
            )
        strategy_key = str(name).upper().replace(" ", "_")
        thesis = str(getattr(structure, "rationale", "") or name)[:220]
        econ = structure_economics(contracts, int(lot_size))
        risk = {
            "max_loss_inr": econ.get("max_loss_inr"),
            "max_profit_inr": econ.get("max_profit_inr"),
            "credit_or_debit": econ.get("credit_or_debit"),
            "breakeven_low": econ.get("breakeven_low"),
            "breakeven_high": econ.get("breakeven_high"),
            "call_width": econ.get("call_width"),
            "put_width": econ.get("put_width"),
            "risk_note": getattr(structure, "risk_note", ""),
            "exit_by": getattr(structure, "exit_by", ""),
            "term_edge_pts": term_edge,
        }
        if risk.get("max_loss_inr") is None:
            width = abs(float(contracts[0].strike) - float(contracts[1].strike)) if len(contracts) >= 2 else float(strike_step)
            # Calendars: same strike → max loss ≈ |debit| * lot when priced
            if width < 1 and econ.get("credit_or_debit") is not None and float(econ["credit_or_debit"]) < 0:
                risk["max_loss_inr"] = round(abs(float(econ["credit_or_debit"])) * float(lot_size), 0)
            else:
                risk["max_loss_inr"] = round(width * float(lot_size), 0)
        if "CONDOR" in strategy_key:
            strategy_type = "IRON_CONDOR"
        elif "STRANGLE" in strategy_key:
            strategy_type = "STRANGLE"
        elif "CALENDAR" in strategy_key:
            strategy_type = "CALENDAR"
        elif "DIAGONAL" in strategy_key:
            strategy_type = "DIAGONAL"
        elif "SPREAD" in strategy_key:
            strategy_type = "VERTICAL"
        # shorts on magnet → hard block
        for leg in contracts:
            if leg.action == "SELL" and magnet and abs(leg.strike - magnet) < step * 0.5:
                hard.append("short_on_magnet")

    size: SizeTier = "FULL" if score >= FIRE_MIN else ("HALF" if score >= SCOUT_MIN else "SCOUT")
    provisional = state_from_score(score, [])
    context = {
        "oi_ts": oi_ts,
        "oi_stale": oi_stale,
        "lot_size": int(lot_size),
        "call_wall": cw,
        "put_wall": pw,
        "flip": flip,
        "magnet": magnet,
        "spot": spot,
        "abs_gex_bucket": vr,
        "blast_tag": None if blast_safe else "ACTIVE",
        "dealer_sign_model": "short_call_short_put",
    }
    hard = decisive_gate(
        state="FIRE" if provisional == "FIRE" else provisional,
        contracts=contracts,
        risk=risk,
        evidence=evidence,
        context=context,
        hard_blocks=hard,
        strategy_key=strategy_key,
    )
    if "No trade" in str(name) or not contracts:
        hard.append("no_structure")
    state = state_from_score(score, hard)
    if state == "HOLD" and hard:
        thesis = f"HOLD — {', '.join(hard)}. " + thesis

    return GoDecision(
        id=str(uuid.uuid4()),
        state=state,
        mode="structure",
        symbol=symbol.upper(),
        score=score,
        score_weights=weights,
        strategy_key=strategy_key,
        thesis=thesis[:280],
        evidence=evidence,
        soft_flags=soft,
        hard_blocks=hard,
        invalidation={"rule": "corridor break, flip cross, or blast active"},
        enter_rule="NOW_CLOSE",
        size_tier=size if state != "HOLD" else "SCOUT",
        contracts=contracts,
        risk=risk,
        targets={
            "breakeven_low": risk.get("breakeven_low"),
            "breakeven_high": risk.get("breakeven_high"),
            "max_profit_inr": risk.get("max_profit_inr"),
        },
        context=context,
        sources={"structure_name": name, "alert_tape_headline": alert_headline},
        deep_links={"recommendations": "/?view=recommendations"},
        expires_at=_iso(_ist_now() + timedelta(minutes=10)),
        emitted_at=_iso(),
        strategy_type=strategy_type,
    )


def build_writer_decision(
    *,
    symbol: str,
    lot_size: int,
    strike_step: int,
    levels: Any,
    vol_regime: str,
    iv: float | None = None,
    rv: float | None = None,
    catalyst_clear: bool = True,
    tape_ok: bool = True,
    oi_ts: str | None = None,
    oi_stale: bool = False,
    blast_safe: bool = True,
    short_call: float | None = None,
    short_put: float | None = None,
    credit_call: float | None = None,
    credit_put: float | None = None,
    margin_est_inr: float | None = None,
    alert_headline: str | None = None,
) -> GoDecision:
    weights = dict(WRITER_WEIGHTS)
    evidence: list[EvidenceItem] = []
    soft: list[str] = []
    hard: list[str] = []

    spot = float(getattr(levels, "spot", 0) or 0)
    cw = float(getattr(levels, "call_wall", 0) or 0)
    pw = float(getattr(levels, "put_wall", 0) or 0)
    flip = float(getattr(levels, "flip", 0) or 0)
    magnet = float(getattr(levels, "magnet", 0) or 0)
    step = float(strike_step or 50)
    vr = (vol_regime or "mid").lower()

    if spot <= 0:
        hard.append("placeholder_or_missing_spot")
    if oi_stale:
        hard.append("stale_oi")
    if not catalyst_clear:
        hard.append("high_shock_catalyst")
    if not blast_safe:
        hard.append("blast_active_near_short")

    calm = vr == "calm"
    evidence.append(
        _ev("abs_gex_calm", "Calm |GEX| bucket", vr, "calm", calm, weights["abs_gex_calm"], "structures.suggest")
    )
    iv_v = float(iv) if iv is not None else None
    rv_v = float(rv) if rv is not None else None
    iv_rv = (iv_v - rv_v) if (iv_v is not None and rv_v is not None) else None
    iv_ok = bool(iv_rv is not None and iv_rv > 0)
    evidence.append(
        _ev("iv_rv", "IV − RV edge", None if iv_rv is None else round(iv_rv, 2), "> 0", iv_ok, weights["iv_rv"], "realized_vol")
    )

    # default shorts one step beyond walls
    sc = float(short_call) if short_call is not None else (cw + step if cw else 0)
    sp = float(short_put) if short_put is not None else (pw - step if pw else 0)
    dist_ok = sc > cw and sp < pw if (cw and pw) else False
    evidence.append(
        _ev(
            "distance_beyond_walls",
            "Shorts beyond walls",
            {"short_call": sc, "short_put": sp, "cw": cw, "pw": pw},
            "beyond call/put concentration",
            dist_ok,
            weights["distance_beyond_walls"],
            "gex.levels",
        )
    )
    evidence.append(
        _ev("catalyst_clear", "Catalyst clear", catalyst_clear, "not HIGH_SHOCK", catalyst_clear, weights["catalyst_clear"], "market_intelligence")
    )
    evidence.append(
        _ev("tape_ok", "Tape not breaking walls", tape_ok, "no continuation through wall", tape_ok, weights["tape_ok"], "sentinel")
    )

    score = _clamp(
        weights["abs_gex_calm"] * (100.0 if calm else 25.0)
        + weights["iv_rv"] * (100.0 if iv_ok else (50.0 if iv_rv is None else 10.0))
        + weights["distance_beyond_walls"] * (100.0 if dist_ok else 20.0)
        + weights["catalyst_clear"] * (100.0 if catalyst_clear else 0.0)
        + weights["tape_ok"] * (100.0 if tape_ok else 20.0)
    )

    contracts: list[GoLeg] = []
    if sc > 0:
        contracts.append(GoLeg(action="SELL", opt_type="CE", strike=sc, ltp=credit_call))
    if sp > 0:
        contracts.append(GoLeg(action="SELL", opt_type="PE", strike=sp, ltp=credit_put))
    for leg in contracts:
        if magnet and abs(leg.strike - magnet) < step * 0.5:
            hard.append("short_on_magnet")

    credit = (float(credit_call or 0) + float(credit_put or 0)) or None
    risk = {
        "max_loss_inr": None,
        "margin_est_inr": margin_est_inr,
        "credit_or_debit": credit,
        "iv_rv": iv_rv,
    }
    thesis = (
        f"Write beyond walls. IV−RV {iv_rv:+.1f}." if iv_rv is not None else "Write beyond walls when calm."
    )
    if not calm:
        thesis = "Not calm |GEX| — new naked shorts suppressed."

    provisional = state_from_score(score, [])
    context = {
        "oi_ts": oi_ts,
        "oi_stale": oi_stale,
        "lot_size": int(lot_size),
        "call_wall": cw,
        "put_wall": pw,
        "flip": flip,
        "magnet": magnet,
        "spot": spot,
        "abs_gex_bucket": vr,
        "blast_tag": None if blast_safe else "ACTIVE",
        "dealer_sign_model": "short_call_short_put",
    }
    hard = decisive_gate(
        state="FIRE" if provisional == "FIRE" else provisional,
        contracts=contracts,
        risk=risk,
        evidence=evidence,
        context=context,
        hard_blocks=hard,
        strategy_key="WRITE_OTM",
    )
    state = state_from_score(score, hard)
    if state == "HOLD" and hard:
        thesis = f"HOLD — {', '.join(hard)}. " + thesis

    return GoDecision(
        id=str(uuid.uuid4()),
        state=state,
        mode="writer",
        symbol=symbol.upper(),
        score=score,
        score_weights=weights,
        strategy_key="WRITE_OTM",
        thesis=thesis[:280],
        evidence=evidence,
        soft_flags=soft,
        hard_blocks=hard,
        invalidation={"rule": "wall shift inside short, blast active, or CVD break through wall"},
        enter_rule="NOW_CLOSE",
        size_tier="FULL" if state == "FIRE" else "SCOUT",
        contracts=contracts,
        risk=risk,
        targets={},
        context=context,
        sources={"alert_tape_headline": alert_headline},
        deep_links={"analytics": "/?view=analytics"},
        expires_at=_iso(_ist_now() + timedelta(minutes=10)),
        emitted_at=_iso(),
        strategy_type="NAKED_SHORT",
    )


def rank_feed(decisions: list[GoDecision]) -> list[GoDecision]:
    order = {"FIRE": 0, "SCOUT": 1, "HOLD": 2}
    return sorted(decisions, key=lambda d: (order.get(d.state, 9), -d.score))


def structure_economics(
    contracts: list[GoLeg],
    lot_size: int,
) -> dict:
    """Net credit/debit, wing widths, breakevens, max loss/profit in ₹.

    Uses leg LTPs when present. Missing LTP → economics partial (widths only).
    Hand-checkable: iron condor / vertical / long strangle.
    """
    lot = int(lot_size or 0)
    if not contracts or lot <= 0:
        return {}

    priced = all(c.ltp is not None and float(c.ltp) >= 0 for c in contracts)
    net_pts = 0.0
    if priced:
        for c in contracts:
            px = float(c.ltp or 0)
            net_pts += px if c.action == "SELL" else -px

    calls = [c for c in contracts if c.opt_type.startswith("C")]
    puts = [c for c in contracts if c.opt_type.startswith("P")]
    short_calls = sorted([c for c in calls if c.action == "SELL"], key=lambda x: x.strike)
    long_calls = sorted([c for c in calls if c.action == "BUY"], key=lambda x: x.strike)
    short_puts = sorted([c for c in puts if c.action == "SELL"], key=lambda x: x.strike)
    long_puts = sorted([c for c in puts if c.action == "BUY"], key=lambda x: x.strike)

    call_width = put_width = None
    if short_calls and long_calls:
        call_width = abs(float(long_calls[-1].strike) - float(short_calls[0].strike))
    if short_puts and long_puts:
        put_width = abs(float(short_puts[-1].strike) - float(long_puts[0].strike))

    out: dict[str, Any] = {
        "credit_or_debit": round(net_pts, 2) if priced else None,
        "call_width": call_width,
        "put_width": put_width,
    }

    if not priced:
        # Width-only max loss (credit unknown) — conservative wing * lot
        wing = max([w for w in (call_width, put_width) if w is not None], default=None)
        if wing is not None:
            out["max_loss_inr"] = round(float(wing) * lot, 0)
        return out

    credit = net_pts  # >0 credit, <0 debit
    wing = max([w for w in (call_width, put_width) if w is not None], default=None)

    if credit >= 0 and wing is not None:
        # Short premium defined-risk
        max_profit = credit * lot
        max_loss = max(wing - credit, 0.0) * lot
        out["max_profit_inr"] = round(max_profit, 0)
        out["max_loss_inr"] = round(max_loss, 0)
        if short_calls:
            out["breakeven_high"] = round(float(short_calls[0].strike) + credit, 1)
        if short_puts:
            out["breakeven_low"] = round(float(short_puts[-1].strike) - credit, 1)
    elif credit < 0:
        # Long premium — max loss is debit paid
        debit = abs(credit)
        out["max_loss_inr"] = round(debit * lot, 0)
        out["max_profit_inr"] = None  # uncapped / width-limited separately
        if long_calls and not short_calls:
            # long call only or strangle — BE ≈ strike + debit for single; skip multi
            if len(contracts) == 1:
                out["breakeven_high"] = round(float(long_calls[0].strike) + debit, 1)
        if long_puts and not short_puts and len(contracts) == 1:
            out["breakeven_low"] = round(float(long_puts[0].strike) - debit, 1)
        if long_calls and short_calls and call_width is not None:
            # debit call spread
            out["max_profit_inr"] = round(max(call_width - debit, 0.0) * lot, 0)
            out["breakeven_high"] = round(float(long_calls[0].strike) + debit, 1)
        if long_puts and short_puts and put_width is not None:
            out["max_profit_inr"] = round(max(put_width - debit, 0.0) * lot, 0)
            out["breakeven_low"] = round(float(long_puts[-1].strike) - debit, 1)

    return out


def evaluate_trail(
    *,
    entry_ltp: float,
    mark_ltp: float,
    arm_pct: float = 0.30,
    lock_pct_of_entry: float = 0.10,
    peak_ltp: float | None = None,
    prior_stop: float | None = None,
    giveback_pct_from_peak: float = 0.25,
) -> dict:
    """Directional premium trail with ratchet (B5).

    Arms after +arm_pct from entry. Floor lock = entry*(1+lock). Once armed the
    stop ratchets with the peak: max(floor, peak*(1−giveback), prior_stop).

    11 Nov Sensex VANNA −₹3.2k: entry ~398, MFE +328 (peak ~726), fixed stop
    stuck at entry*1.10 (~438) then settled through the stop at ~241. Ratchet +
    fill-at-stop in ``mark_open`` are the fix.

    Example: entry 72, arm 30% → arm at 93.6; floor lock 10% → 79.2. Peak 120 →
    stop max(79.2, 120*0.75)=90.
    """
    entry = float(entry_ltp or 0)
    mark = float(mark_ltp or 0)
    if entry <= 0:
        return {
            "armed": False,
            "pnl_pct": None,
            "trail_stop": None,
            "stopped": False,
            "reason": "no_entry",
        }
    peak = max(entry, mark, float(peak_ltp) if peak_ltp is not None else mark)
    pnl_pct = (mark - entry) / entry
    peak_pnl = (peak - entry) / entry
    armed = bool(prior_stop is not None) or peak_pnl >= float(arm_pct)
    floor_stop = entry * (1.0 + float(lock_pct_of_entry))
    peak_stop = peak * (1.0 - float(giveback_pct_from_peak))
    trail_stop = None
    if armed:
        candidates = [floor_stop, peak_stop]
        if prior_stop is not None:
            try:
                candidates.append(float(prior_stop))
            except (TypeError, ValueError):
                pass
        trail_stop = round(max(candidates), 2)
    stopped = bool(armed and trail_stop is not None and mark <= float(trail_stop))
    return {
        "armed": armed,
        "pnl_pct": round(pnl_pct * 100.0, 1),
        "trail_stop": trail_stop,
        "stopped": stopped,
        "arm_pct": arm_pct,
        "lock_pct_of_entry": lock_pct_of_entry,
        "giveback_pct_from_peak": giveback_pct_from_peak,
        "peak_ltp": round(peak, 2),
    }


def update_excursion(
    *,
    entry_premium: float,
    mark_premium: float,
    mfe: float | None,
    mae: float | None,
) -> tuple[float, float]:
    """Update max favorable / adverse excursion in premium points from entry."""
    entry = float(entry_premium or 0)
    mark = float(mark_premium or 0)
    fav = mark - entry
    adv = entry - mark
    mfe_v = float(mfe) if mfe is not None else 0.0
    mae_v = float(mae) if mae is not None else 0.0
    if fav > mfe_v:
        mfe_v = fav
    if adv > mae_v:
        mae_v = adv
    return round(mfe_v, 2), round(mae_v, 2)


def _intrinsic(spot: float, strike: float, opt_type: str) -> float:
    ot = str(opt_type or "").upper()
    s, k = float(spot), float(strike)
    if ot.startswith("C"):
        return max(0.0, s - k)
    if ot.startswith("P"):
        return max(0.0, k - s)
    return 0.0


def _net_credit_pts(contracts: list[GoLeg]) -> float | None:
    if not contracts or any(c.ltp is None for c in contracts):
        return None
    net = 0.0
    for c in contracts:
        px = float(c.ltp or 0)
        net += px if str(c.action).upper() == "SELL" else -px
    return round(net, 2)


def structure_payoff_curve(
    contracts: list[GoLeg],
    *,
    lot_size: int,
    spot: float | None = None,
    n_points: int = 41,
    pad_steps: int = 4,
    strike_step: float = 50.0,
) -> dict:
    """Expiry P&L curve in ₹ for multi-leg GO. Hand-checkable on verticals/IC.

    pnl_inr(S) = (credit_or_debit + Σ BUY intrinsic − Σ SELL intrinsic) × lot
    """
    lot = int(lot_size or 0)
    if not contracts or lot <= 0:
        return {"points": [], "spot": spot, "credit_or_debit": None}

    strikes = [float(c.strike) for c in contracts]
    step = float(strike_step or 50.0)
    lo = min(strikes) - pad_steps * step
    hi = max(strikes) + pad_steps * step
    if spot is not None and float(spot) > 0:
        lo = min(lo, float(spot) - pad_steps * step)
        hi = max(hi, float(spot) + pad_steps * step)
    n = max(5, int(n_points))
    credit = _net_credit_pts(contracts)
    credit_v = float(credit) if credit is not None else 0.0

    points = []
    for i in range(n):
        s = lo + (hi - lo) * (i / (n - 1))
        intrinsic_net = 0.0
        for c in contracts:
            inn = _intrinsic(s, c.strike, c.opt_type)
            if str(c.action).upper() == "BUY":
                intrinsic_net += inn
            else:
                intrinsic_net -= inn
        pnl_pts = credit_v + intrinsic_net
        points.append({
            "spot": round(s, 1),
            "pnl_pts": round(pnl_pts, 2),
            "pnl_inr": round(pnl_pts * lot, 0),
        })

    be_crossings = []
    for a, b in zip(points, points[1:]):
        if a["pnl_pts"] == 0:
            be_crossings.append(a["spot"])
        elif a["pnl_pts"] * b["pnl_pts"] < 0:
            # linear interpolate
            t = abs(a["pnl_pts"]) / (abs(a["pnl_pts"]) + abs(b["pnl_pts"]))
            be_crossings.append(round(a["spot"] + t * (b["spot"] - a["spot"]), 1))

    return {
        "points": points,
        "spot": float(spot) if spot is not None else None,
        "credit_or_debit": credit,
        "breakevens": be_crossings,
        "max_pnl_inr": max((p["pnl_inr"] for p in points), default=None),
        "min_pnl_inr": min((p["pnl_inr"] for p in points), default=None),
        "lot_size": lot,
    }


def net_greeks(
    contracts: list[GoLeg],
    *,
    spot: float,
    lot_size: int,
    iv: float = 0.14,
    dte_days: float = 3.0,
    r: float = 0.065,
) -> dict:
    """Signed net Black-76 Greeks for the package (BUY +, SELL −), × lot.

    IV is decimal. Missing LTP does not block Greek calc. VERIFY: F≈spot proxy.
    """
    from target_engine.pricer import greeks as bs_greeks

    lot = int(lot_size or 0)
    s = float(spot or 0)
    if not contracts or lot <= 0 or s <= 0:
        return {}
    T = max(float(dte_days) / 365.0, 1e-4)
    sigma = float(iv) if float(iv) < 1.5 else float(iv) / 100.0
    sigma = max(sigma, 0.05)

    tot = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    per_leg = []
    for c in contracts:
        g = bs_greeks(s, float(c.strike), T, sigma, r=r, option_type=c.opt_type)
        sign = 1.0 if str(c.action).upper() == "BUY" else -1.0
        leg = {
            "action": c.action,
            "opt_type": c.opt_type,
            "strike": c.strike,
            "delta": round(sign * float(g.delta) * lot, 2),
            "gamma": round(sign * float(g.gamma) * lot, 4),
            "vega": round(sign * float(g.vega) * lot, 2),
            "theta": round(sign * float(g.theta) * lot, 2),
        }
        per_leg.append(leg)
        for k in tot:
            tot[k] += leg[k]

    credit = _net_credit_pts(contracts)
    return {
        "net": {k: round(v, 4 if k == "gamma" else 2) for k, v in tot.items()},
        "per_leg": per_leg,
        "lot_size": lot,
        "iv_used": round(sigma, 4),
        "dte_days": dte_days,
        "spot": s,
        "credit_or_debit": credit,
        "note": "F≈spot Black-76 proxy; not exchange SPAN",
    }


def mark_mtm(
    contracts: list[GoLeg],
    *,
    mark_ltps: dict[tuple[str, float], float] | None = None,
    lot_size: int,
    entry_credit: float | None = None,
) -> dict:
    """Multi-leg MTM vs entry credit/debit. mark_ltps keyed by (CE|PE, strike)."""
    lot = int(lot_size or 0)
    if not contracts or lot <= 0:
        return {}
    entry = entry_credit if entry_credit is not None else _net_credit_pts(contracts)
    marks: list[GoLeg] = []
    for c in contracts:
        px = c.ltp
        if mark_ltps:
            key = (str(c.opt_type).upper(), float(c.strike))
            alt = (str(c.opt_type).upper()[:1], float(c.strike))
            if key in mark_ltps:
                px = mark_ltps[key]
            elif alt in mark_ltps:
                px = mark_ltps[alt]
        marks.append(GoLeg(c.action, c.opt_type, c.strike, ltp=px))
    mark_net = _net_credit_pts(marks)
    if entry is None or mark_net is None:
        return {"entry_credit": entry, "mark_credit": mark_net, "pnl_pts": None, "pnl_inr": None}
    # Short credit: profit when mark_net falls; long debit: profit when mark_net rises (more neg → wait)
    # Unified with go_efficiency: pnl_pts = entry − mark for signed SELL−BUY net
    pnl_pts = float(entry) - float(mark_net)
    return {
        "entry_credit": entry,
        "mark_credit": mark_net,
        "pnl_pts": round(pnl_pts, 2),
        "pnl_inr": round(pnl_pts * lot, 0),
        "pct_of_credit": (
            round(100.0 * pnl_pts / abs(float(entry)), 1) if entry else None
        ),
    }


def suggest_manage_actions(
    decision: dict | GoDecision,
    *,
    open_row: dict | None = None,
    strike_step: float = 50.0,
) -> list[dict]:
    """Stageable manage actions — never auto-execute. ₹ impact is estimate."""
    if isinstance(decision, GoDecision):
        d = decision.to_dict()
    else:
        d = dict(decision or {})
    mode = str(d.get("mode") or "")
    risk = d.get("risk") or {}
    contracts = d.get("contracts") or []
    step = float(strike_step or 50)
    actions: list[dict] = []

    if mode == "directional":
        actions.append({
            "action": "trail",
            "label": "Arm / refresh trail",
            "detail": "Trail after +30% premium; lock +10% of entry",
            "impact_note": "Journal note only until broker confirm",
        })
        actions.append({
            "action": "flatten",
            "label": "Flatten long",
            "detail": "Stage opposite SELL of current contract",
            "impact_note": "Closes paper GO; confirm at broker",
        })
    elif mode == "structure":
        actions.append({
            "action": "flatten",
            "label": "Flatten structure",
            "detail": "Stage reverse of all legs",
            "impact_note": f"Max loss ref ₹{risk.get('max_loss_inr') or '—'}",
        })
        # Widen short wing one step if IC / credit structure
        shorts = [c for c in contracts if str(c.get("action") if isinstance(c, dict) else c.action).upper() == "SELL"]
        if shorts:
            actions.append({
                "action": "roll_wing",
                "label": "Roll short wing +1 step",
                "detail": f"Nudge short strikes {step:.0f} pts further OTM",
                "impact_note": "Usually costs debit; stage only",
            })
        actions.append({
            "action": "adjust",
            "label": "Convert / tighten",
            "detail": "Stage defined-risk adjust (buy wing / roll)",
            "impact_note": "Uses current corridor; confirm fills",
        })
    elif mode == "writer":
        actions.append({
            "action": "hedge",
            "label": "Buy wing hedge",
            "detail": "Stage long wing one step beyond short",
            "impact_note": "Converts toward defined-risk; margin ↓ estimate",
        })
        actions.append({
            "action": "flatten",
            "label": "Flatten shorts",
            "detail": "Stage buy-to-close of short strikes",
            "impact_note": "Kill latency path — confirm at broker",
        })
        actions.append({
            "action": "roll_wing",
            "label": "Roll out / away",
            "detail": "Stage roll shorts further OTM or next expiry hint",
            "impact_note": "DTE roll when ≤7d (hint only)",
        })

    if open_row and open_row.get("trail_armed"):
        actions.insert(0, {
            "action": "trail",
            "label": "Trail armed — manage stop",
            "detail": f"Stop {open_row.get('trail_stop')} · MFE {open_row.get('mfe')} / MAE {open_row.get('mae')}",
            "impact_note": "Mark via /api/go/mark; flatten if hit",
        })

    # % max profit take for credit structures
    max_profit = risk.get("max_profit_inr")
    if open_row and max_profit and open_row.get("mfe") is not None and float(max_profit) > 0:
        # mfe is premium pts; rough % if credit known
        credit = risk.get("credit_or_debit")
        if credit and float(credit) > 0:
            pct = 100.0 * float(open_row["mfe"]) / float(credit)
            if pct >= 50:
                actions.insert(0, {
                    "action": "flatten",
                    "label": f"Take profit ~{pct:.0f}% of credit",
                    "detail": "MFE ≥ 50% of entry credit — consider flatten",
                    "impact_note": "Alert only; stage flatten to act",
                })

    return actions


def build_manage_legs(decision: dict, action: str, *, strike_step: float = 50.0) -> list[dict]:
    """Build opposite / hedge legs for staging. No broker send."""
    contracts = decision.get("contracts") or []
    step = float(strike_step or 50)
    act = str(action or "").lower()
    out: list[dict] = []

    if act == "flatten":
        for c in contracts:
            out.append({
                "action": "SELL" if str(c.get("action")).upper() == "BUY" else "BUY",
                "opt_type": c.get("opt_type"),
                "strike": c.get("strike"),
                "ltp": c.get("ltp"),
            })
    elif act == "hedge":
        for c in contracts:
            if str(c.get("action")).upper() != "SELL":
                continue
            ot = str(c.get("opt_type") or "").upper()
            k = float(c.get("strike") or 0)
            wing = k + step if ot.startswith("C") else k - step
            out.append({"action": "BUY", "opt_type": ot, "strike": wing, "ltp": None})
    elif act == "roll_wing":
        for c in contracts:
            if str(c.get("action")).upper() != "SELL":
                continue
            ot = str(c.get("opt_type") or "").upper()
            k = float(c.get("strike") or 0)
            new_k = k + step if ot.startswith("C") else k - step
            out.append({"action": "BUY", "opt_type": ot, "strike": k, "ltp": c.get("ltp")})  # close
            out.append({"action": "SELL", "opt_type": ot, "strike": new_k, "ltp": None})  # open further
    elif act == "adjust":
        # Buy missing wings one step beyond shorts
        return build_manage_legs(decision, "hedge", strike_step=step)
    elif act == "trail":
        return []  # trail is mark/state, not new legs
    return out


# --- Phase 3 helpers ---------------------------------------------------------

MTF_BOOST_MAX = 10.0  # soft points added to directional score (not a hard gate)


def multi_tf_confluence_boost(
    *,
    side: str,
    tf_leans: dict[str, str] | None = None,
) -> dict:
    """Soft score boost when 1m/5m/15m leans agree with the GO side.

    Not extra UI homework — evidence factor + small score add only.
    Leans: UP/DOWN/FLAT/NA. CE expects UP; PE expects DOWN.
    """
    side_u = str(side or "CE").upper()
    want = "UP" if side_u.startswith("C") else "DOWN"
    leans = tf_leans or {}
    keys = ["1m", "5m", "15m"]
    aligned = 0
    known = 0
    detail = {}
    for k in keys:
        lean = str(leans.get(k) or "NA").upper()
        detail[k] = lean
        if lean in ("UP", "DOWN"):
            known += 1
            if lean == want:
                aligned += 1
    if known == 0:
        return {"boost": 0.0, "aligned": 0, "known": 0, "want": want, "leans": detail, "pass": False}
    # 2/2 or 3/3 → full boost; 2/3 → partial; 1 → tiny
    if aligned >= 3 or (aligned == 2 and known == 2):
        boost = MTF_BOOST_MAX
    elif aligned == 2:
        boost = 6.0
    elif aligned == 1 and known >= 2:
        boost = 3.0
    else:
        boost = 0.0
    return {
        "boost": boost,
        "aligned": aligned,
        "known": known,
        "want": want,
        "leans": detail,
        "pass": aligned >= 2,
    }


def scenario_whatif(
    contracts: list[GoLeg],
    *,
    spot: float,
    lot_size: int,
    iv: float = 0.14,
    dte_days: float = 3.0,
    shocks: list[float] | None = None,
    r: float = 0.065,
) -> dict:
    """Black-76 package MTM at spot shocks. shocks are fractional (e.g. -0.01, 0, +0.01).

    Returns pnl_inr vs entry credit (SELL−BUY) at each shock. VERIFY: F≈spot proxy.
    """
    from target_engine.pricer import price as bs_price

    lot = int(lot_size or 0)
    s0 = float(spot or 0)
    if not contracts or lot <= 0 or s0 <= 0:
        return {"scenarios": [], "spot": s0}
    entry = _net_credit_pts(contracts)
    entry_v = float(entry) if entry is not None else 0.0
    T = max(float(dte_days) / 365.0, 1e-4)
    sigma = float(iv) if float(iv) < 1.5 else float(iv) / 100.0
    sigma = max(sigma, 0.05)
    shock_list = shocks if shocks is not None else [-0.015, -0.01, -0.005, 0.0, 0.005, 0.01, 0.015]
    rows = []
    for sh in shock_list:
        s = s0 * (1.0 + float(sh))
        mark = 0.0
        for c in contracts:
            px = float(bs_price(s, float(c.strike), T, sigma, r=r, option_type=c.opt_type))
            mark += px if str(c.action).upper() == "SELL" else -px
        pnl_pts = entry_v - mark
        rows.append({
            "shock_pct": round(float(sh) * 100.0, 2),
            "spot": round(s, 1),
            "mark_credit": round(mark, 2),
            "pnl_pts": round(pnl_pts, 2),
            "pnl_inr": round(pnl_pts * lot, 0),
        })
    return {
        "spot": s0,
        "entry_credit": entry,
        "iv_used": round(sigma, 4),
        "dte_days": dte_days,
        "scenarios": rows,
        "note": "F≈spot Black-76 what-if; not a forecast",
    }


def fire_template_slice(
    events: list[dict],
    open_rows: list[dict] | None = None,
) -> dict:
    """Descriptive backtest-style slice of FIRE templates from efficiency ledger.

    Point-in-time: uses only recorded emit/stage/skip + open MFE/MAE. No look-ahead
    fill simulation beyond stored marks.
    """
    by_key: dict[str, dict] = {}
    for ev in events or []:
        if str(ev.get("state") or "") != "FIRE" and str(ev.get("event_type")) == "EMIT":
            continue
        key = str(ev.get("strategy_key") or "—")
        mode = str(ev.get("mode") or "")
        bucket = by_key.setdefault(key, {
            "strategy_key": key,
            "mode": mode,
            "emit_fire": 0,
            "staged": 0,
            "skipped": 0,
        })
        et = str(ev.get("event_type") or "")
        if et == "EMIT" and str(ev.get("state")) == "FIRE":
            bucket["emit_fire"] += 1
            if not bucket.get("mode"):
                bucket["mode"] = mode
        elif et == "STAGE":
            bucket["staged"] += 1
        elif et == "SKIP":
            bucket["skipped"] += 1

    mfe_by: dict[str, list[float]] = {}
    mae_by: dict[str, list[float]] = {}
    for row in open_rows or []:
        sk = str(row.get("strategy_key") or "—")
        mfe_by.setdefault(sk, []).append(float(row.get("mfe") or 0))
        mae_by.setdefault(sk, []).append(float(row.get("mae") or 0))

    templates = []
    for key, b in by_key.items():
        mfes = mfe_by.get(key) or []
        maes = mae_by.get(key) or []
        avg_mfe = round(sum(mfes) / len(mfes), 2) if mfes else None
        avg_mae = round(sum(maes) / len(maes), 2) if maes else None
        edge = (
            round(avg_mfe / avg_mae, 2)
            if avg_mfe is not None and avg_mae and avg_mae > 0
            else None
        )
        templates.append({
            **b,
            "stage_rate": round(b["staged"] / b["emit_fire"], 3) if b["emit_fire"] else None,
            "avg_mfe": avg_mfe,
            "avg_mae": avg_mae,
            "edge_ratio": edge,
            "n_marked": len(mfes),
        })
    templates.sort(key=lambda x: (-(x["emit_fire"]), x["strategy_key"]))
    return {
        "templates": templates,
        "fire_emits": sum(t["emit_fire"] for t in templates),
        "staged": sum(t["staged"] for t in templates),
        "skipped": sum(t["skipped"] for t in templates),
    }


def unusual_oi_volume_flag(
    *,
    strike: float,
    oi: float | None,
    volume: float | None,
    median_oi: float | None,
    median_vol: float | None,
    oi_mult: float = 2.5,
    vol_mult: float = 3.0,
) -> dict:
    """Evidence helper: strike OI/volume unusual vs session median (soft factor)."""
    oi_v = float(oi) if oi is not None else None
    vol_v = float(volume) if volume is not None else None
    moi = float(median_oi) if median_oi not in (None, 0) else None
    mvol = float(median_vol) if median_vol not in (None, 0) else None
    oi_flag = bool(oi_v is not None and moi and oi_v >= oi_mult * moi)
    vol_flag = bool(vol_v is not None and mvol and vol_v >= vol_mult * mvol)
    return {
        "strike": float(strike),
        "oi": oi_v,
        "volume": vol_v,
        "oi_unusual": oi_flag,
        "vol_unusual": vol_flag,
        "pass": oi_flag or vol_flag,
        "detail": (
            f"OI×{oi_v / moi:.1f}" if oi_flag and moi else
            (f"Vol×{vol_v / mvol:.1f}" if vol_flag and mvol else "normal")
        ),
    }
