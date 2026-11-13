"""Hard decision gates — refuse loud, never invent fillers.

Each gate lists the *required context* that must be wired by the caller.
The 12-Nov Ideas blackout happened because the VWAP gate was correct but the
publisher never passed ``latest_bar``. Use ``require_context`` at call sites
and keep the regression tests in ``tests/test_hard_gates.py`` green.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class HardGate:
    name: str
    refuse_reason: str
    required_context: tuple[str, ...]
    owner: str  # module/function that must supply context


# Registry — add a row whenever a new fail-loud gate ships.
GATES: tuple[HardGate, ...] = (
    HardGate(
        name="ideas_session_vwap",
        refuse_reason="Missing session VWAP",
        required_context=("latest_bar", "sentinel_bars"),
        owner="web_server._rec_publisher_loop → _load_rec_sentinel_context",
    ),
    HardGate(
        name="ideas_placeholder_chain",
        refuse_reason="placeholder chain",
        required_context=("chain_spot", "quote_spot"),
        owner="web_server._rec_publisher_loop → config.withhold_placeholder_ideas",
    ),
    HardGate(
        name="paper_execute_ltp_strike",
        refuse_reason="ltp and strike are required",
        required_context=("ltp", "strike"),
        owner="web_server.execute_trade",
    ),
    HardGate(
        name="te_reprice_live_spot",
        refuse_reason="No live spot for this book",
        required_context=("spot_or_future",),
        owner="web_server.reprice_target_engine",
    ),
    HardGate(
        name="te_reprice_expiry",
        refuse_reason="No resolvable expiry for reprice",
        required_context=("active_exps_or_calendar",),
        owner="web_server.reprice_target_engine",
    ),
    HardGate(
        name="go_atm_iv",
        refuse_reason="live ATM IV unavailable",
        required_context=("atm_iv",),
        owner="analytics.go_desk / payoff routes",
    ),
    HardGate(
        name="go_fire_premium",
        refuse_reason="missing_premium",
        required_context=("contracts_ltp",),
        owner="analytics.go_desk.decisive_gate",
    ),
    HardGate(
        name="te_strike_iv",
        refuse_reason="implied_vol_unavailable",
        required_context=("strike_iv",),
        owner="web_server.reprice_target_engine",
    ),
    HardGate(
        name="te_reprice_ltp",
        refuse_reason="No live LTP for reprice entry",
        required_context=("entry_ltp",),
        owner="web_server.reprice_target_engine",
    ),
    HardGate(
        name="te_run_spot",
        refuse_reason="target_engine requires live spot — refusing default 25000",
        required_context=("spot",),
        owner="target_engine.runner.run_target_engine",
    ),
    HardGate(
        name="te_run_atm_iv",
        refuse_reason="target_engine requires live ATM IV — refusing default 0.15",
        required_context=("atm_iv",),
        owner="target_engine.runner.run_target_engine",
    ),
    HardGate(
        name="te_run_expiry",
        refuse_reason="target_engine requires an expiry — refusing hardcoded date",
        required_context=("expiry",),
        owner="target_engine.runner.run_target_engine",
    ),
    HardGate(
        name="blast_equal_walls",
        refuse_reason="pin_geometry",  # soft-flag, not a hard refuse
        required_context=("call_wall", "put_wall"),
        owner="app.gammablast.evaluate_dashboard_gamma_blast",
    ),
)

# Banner source keys the SPA must wire through noteFetchResult (Pillar E3).
TAB_BANNER_SOURCES: tuple[str, ...] = (
    "GO Desk",
    "Sentinel",
    "Recommendations",
    "Cockpit",
)


def require_context(gate_name: str, provided: dict[str, Any]) -> list[str]:
    """Return missing context keys for ``gate_name``. Empty list = OK."""
    gate = next((g for g in GATES if g.name == gate_name), None)
    if gate is None:
        raise KeyError(f"unknown hard gate: {gate_name}")
    missing = []
    for key in gate.required_context:
        val = provided.get(key)
        if val is None:
            missing.append(key)
        elif isinstance(val, (list, tuple)) and len(val) == 0:
            missing.append(key)
        elif isinstance(val, (int, float)) and val == 0 and key.endswith(
            ("spot", "ltp", "strike", "iv", "wall")
        ):
            missing.append(key)
        elif key == "contracts_ltp":
            # Expect a positive float (ATM / leg premium) or True when priced.
            try:
                if val is True:
                    continue
                if float(val) <= 0:
                    missing.append(key)
            except (TypeError, ValueError):
                missing.append(key)
    return missing


def gate_names() -> list[str]:
    return [g.name for g in GATES]


def assert_publisher_wires_vwap(source: str) -> None:
    """Static guard: publisher body must call context loader and pass latest_bar."""
    needed = (
        "_load_rec_sentinel_context",
        "latest_bar=latest_bar",
        "sentinel_bars=sentinel_bars",
    )
    missing = [n for n in needed if n not in source]
    if missing:
        raise AssertionError(
            "rec publisher missing VWAP wiring (12-Nov regression): " + ", ".join(missing)
        )


def assert_go_fire_premium_gated(source: str) -> None:
    """FIRE completeness must hard-block when leg premium is missing."""
    needed = ("missing_premium", "decisive_gate")
    # Source may be go_desk module text — require the block token.
    if "missing_premium" not in source:
        raise AssertionError(
            "go_desk missing FIRE premium hard-block (Pillar E1): expected 'missing_premium'"
        )
    del needed  # documented for readers


def assert_te_refuses_without_iv(source: str) -> None:
    """Target Engine reprice must refuse when strike IV is absent — no default fill."""
    needed = ("implied_vol_unavailable", "strike_iv")
    missing = [n for n in needed if n not in source]
    if missing:
        raise AssertionError(
            "TE reprice missing IV refuse wiring: " + ", ".join(missing)
        )


def assert_blast_flags_equal_walls(source: str) -> None:
    """Gamma blast must soft-flag collapsed call/put walls as pin_geometry."""
    needed = ("pin_geometry", "call_wall", "put_wall")
    missing = [n for n in needed if n not in source]
    if missing:
        raise AssertionError(
            "gammablast missing equal-walls soft-flag wiring: " + ", ".join(missing)
        )


def assert_tab_banner_sources(cockpit_js: str) -> None:
    """SPA must route money-adjacent tabs through noteFetchResult with known keys."""
    missing = []
    for key in TAB_BANNER_SOURCES:
        needle = f'noteFetchResult("{key}"'
        if needle not in cockpit_js:
            missing.append(key)
    if missing:
        raise AssertionError(
            "cockpit.js missing noteFetchResult wiring for: " + ", ".join(missing)
        )
