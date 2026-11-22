"""Rules-only GO / NO-GO card builder.

Full contract: instrument, side, entry/target/stop, size cap, score,
three reasons, invalidation, valid-until, model/rules version stamp.
Hard vetoes sit above setups. Numbers are starting parameters — not edge.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from crypto.config import get_flag, get_quote_currency, load_config
from crypto.engine.setups import SetupCandidate, engine_versions, evaluate_setups
from crypto.engine.vetoes import VetoContext, apply_stub_hooks, evaluate_vetoes
from crypto.providers.mock.fixtures import load_fixture
from crypto.providers.types import PerpSnapshot


def _fixture_underlying(gex: dict[str, Any], perp: PerpSnapshot) -> str:
    u = str(gex.get("underlying") or "").strip().upper()
    if u:
        return u
    sym = (perp.symbol or "").upper()
    for token in ("-PERPETUAL", "-PERP", "USD", "USDT"):
        sym = sym.replace(token, "")
    return (sym.split("-")[0] if sym else "BTC") or "BTC"


def _card_instrument(gex: dict[str, Any], perp: PerpSnapshot) -> str:
    """Delta quote pair for Manual Decision (BTCUSD / ETHUSD / SOLUSD)."""
    return get_quote_currency(_fixture_underlying(gex, perp))


def build_signal_card(
    gex: dict[str, Any],
    perp: PerpSnapshot,
    *,
    orderflow: Optional[dict[str, Any]] = None,
    news_blackout: bool = False,
    chain_fetched_at: datetime | None = None,
    live_signal_path: bool = False,
    risk_daily_loss_hit: bool = False,
    risk_margin_cap_hit: bool = False,
    venue_not_operational: bool = False,
    scheduled_event_within_30m: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    # Point-in-time ``now`` keeps replay from false-stale vetoes on historical bars.
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    versions = engine_versions()
    eng = load_config().get("engine") or {}
    size_cap = float(eng.get("size_cap_usd") or 2500)
    valid_mins = int(eng.get("card_valid_minutes") or 20)

    if not get_flag("engine.signal_rules", default=True):
        return _nogo(
            "signal rules flag off",
            gex,
            perp,
            now,
            ["engine.signal_rules disabled"],
            versions=versions,
        )

    of = orderflow or {
        "mark": perp.mark,
        "cvd_1m": perp.cvd_1m,
        "cvd_5m": perp.cvd_5m,
        "cvd_15m": perp.cvd_15m,
        "book_imbalance": perp.book_imbalance,
        "oi_delta": 0.0,
        "funding_rate": perp.funding_rate,
    }

    ctx = apply_stub_hooks(
        VetoContext(
            now=now,
            gex=gex,
            chain_fetched_at=chain_fetched_at,
            perp_fetched_at=perp.fetched_at,
            live_signal_path=live_signal_path,
            news_blackout=news_blackout,
            risk_daily_loss_hit=risk_daily_loss_hit,
            risk_margin_cap_hit=risk_margin_cap_hit,
            venue_not_operational=venue_not_operational,
            scheduled_event_within_30m=scheduled_event_within_30m,
        )
    )
    veto = evaluate_vetoes(ctx)

    src = gex.get("source") or ""
    use_fixture_tmpl = (
        "mock" in src or "fixture" in src or "stub" in src
    ) and not live_signal_path

    if use_fixture_tmpl:
        tmpl = load_fixture(_fixture_underlying(gex, perp)).get("signal_template") or {}
        if veto.blocked:
            return _nogo("hard veto", gex, perp, now, veto.vetoes, tmpl, versions=versions)
        if gex.get("models_agree") and gex.get("regime") == "positive_gamma":
            valid_until = now + timedelta(minutes=int(tmpl.get("valid_minutes") or valid_mins))
            reasons = list(tmpl.get("reasons") or [])
            while len(reasons) < 3:
                reasons.append("Fixture demo reason.")
            return _go_card(
                setup=str(tmpl.get("setup") or "positive_gamma_fade"),
                instrument=str(tmpl.get("instrument") or _card_instrument(gex, perp)),
                side=str(tmpl.get("side") or "sell"),
                entry_zone=list(tmpl.get("entry_zone") or [perp.mark, perp.mark * 1.001]),
                target=tmpl.get("target"),
                stop=tmpl.get("stop"),
                size_cap_usd=float(tmpl.get("size_cap_usd") or size_cap),
                score=int(tmpl.get("score") or 70),
                reasons=reasons[:3],
                invalidation=str(tmpl.get("invalidation") or ""),
                valid_until=valid_until,
                now=now,
                gex=gex,
                versions=versions,
            )

    candidate = evaluate_setups(gex, of, mark=perp.mark)

    if veto.blocked or candidate is None:
        reasons = list(veto.vetoes)
        if candidate is None and not veto.blocked:
            reasons.append("no setup conditions met")
        return _nogo("no go", gex, perp, now, reasons, versions=versions)

    return _card_from_candidate(
        candidate, gex, perp, now, size_cap, valid_mins, versions
    )


def _card_from_candidate(
    cand: SetupCandidate,
    gex: dict[str, Any],
    perp: PerpSnapshot,
    now: datetime,
    size_cap: float,
    valid_mins: int,
    versions: dict[str, str],
) -> dict[str, Any]:
    reasons = list(cand.reasons)
    while len(reasons) < 3:
        reasons.append(f"Setup {cand.setup} matched configurable rules.")
    return _go_card(
        setup=cand.setup,
        instrument=_card_instrument(gex, perp),
        side=cand.side,
        entry_zone=cand.entry_zone,
        target=cand.target,
        stop=cand.stop,
        size_cap_usd=size_cap,
        score=cand.score,
        reasons=reasons[:3],
        invalidation=cand.invalidation,
        valid_until=now + timedelta(minutes=valid_mins),
        now=now,
        gex=gex,
        versions=versions,
    )


def _go_card(
    *,
    setup: str,
    instrument: str,
    side: str,
    entry_zone: list[float],
    target: Any,
    stop: Any,
    size_cap_usd: float,
    score: int,
    reasons: list[str],
    invalidation: str,
    valid_until: datetime,
    now: datetime,
    gex: dict[str, Any],
    versions: dict[str, str],
) -> dict[str, Any]:
    return {
        "status": "GO",
        "setup": setup,
        "instrument": instrument,
        "side": side,
        "entry_zone": entry_zone,
        "target": target,
        "stop": stop,
        "size_cap_usd": size_cap_usd,
        "score": score,
        "reasons": reasons[:3],
        "invalidation": invalidation,
        "valid_until": valid_until,
        "created_at": now,
        "veto_reasons": [],
        "model_version": versions["model_version"],
        "rules_version": versions["rules_version"],
        "mode": "advisory",
        "gex_regime": gex.get("regime"),
        "spot": gex.get("spot"),
        "flip": gex.get("flip"),
        "call_wall": gex.get("call_wall"),
        "put_wall": gex.get("put_wall"),
        "flow_mode": gex.get("flow_mode"),
    }


def _nogo(
    why: str,
    gex: dict[str, Any],
    perp: PerpSnapshot,
    now: datetime,
    vetoes: list[str],
    tmpl: dict[str, Any] | None = None,
    *,
    versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    versions = versions or engine_versions()
    return {
        "status": "NO-GO",
        "setup": (tmpl or {}).get("setup") or "none",
        "instrument": _card_instrument(gex, perp),
        "side": "",
        "entry_zone": [],
        "target": None,
        "stop": None,
        "size_cap_usd": 0,
        "score": 0,
        "reasons": [why, *vetoes[:3]],
        "invalidation": "",
        "valid_until": now + timedelta(minutes=5),
        "created_at": now,
        "veto_reasons": vetoes,
        "model_version": versions["model_version"],
        "rules_version": versions["rules_version"],
        "mode": "advisory",
        "gex_regime": gex.get("regime"),
        "spot": gex.get("spot"),
        "flip": gex.get("flip"),
        "call_wall": gex.get("call_wall"),
        "put_wall": gex.get("put_wall"),
        "flow_mode": gex.get("flow_mode"),
    }
