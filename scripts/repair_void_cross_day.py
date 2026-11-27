"""One-off repair: void GO and Sentinel trades that an intraday book carried into a later session.

Until v5.3.173/174 nothing stopped a GO or Sentinel trade left open at the close from being
marked, trail-stopped or "target hit" on a later day's prints: 13-Nov GO rows closed in
17-Nov's pre-open, 20-Nov Sentinel recs hit target on 25-Nov's opening gap. No same-day exit
price survives (the minute journal tracks the nearest expiry; these trades priced another), so
the honest ledger entry is zero.

Voided:
  * go_open rows CLOSED / TRAIL_STOP whose closed_at day differs from their opened_at day
    → status VOID, exit_reason VOID_CROSS_DAY, P&L and costs 0. The original exit is kept
    under decision_json["void_cross_day"].
  * paper_trades closed on a later day that are a GO mirror (trade_id in go_open) or a
    Sentinel row → exit_reason VOID_CROSS_DAY, P&L and costs 0, original exit in notes.
    Book-balance structures may span days by design and are left alone.
  * sentinel_trade_recommendations linked to a voided paper row, or resolved
    (TARGET_HIT / SPOT_INVAL / SL_HIT) on a later day than created → status VOID.

Dry run by default. ``--apply`` writes a timestamped backup of each DB first. Re-running is a no-op.

  python scripts/repair_void_cross_day.py --root ../cubera-gex-cockpit-prod
  # add --apply once the dry-run list looks right
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

REASON = "VOID_CROSS_DAY"
SENTINEL = "SENTINEL_ENGINE"
RESOLVED = ("TARGET_HIT", "SPOT_INVAL", "SL_HIT")


@dataclass
class Plan:
    go: list[dict] = field(default_factory=list)
    paper: list[dict] = field(default_factory=list)
    recs: list[dict] = field(default_factory=list)


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args)]


def build_plan(journal: Path) -> Plan:
    plan = Plan()
    with sqlite3.connect(journal / "go_efficiency.db") as go:
        plan.go = _rows(go, """
            SELECT decision_id, trade_id, symbol, status, opened_at, closed_at, exit_reason,
                   entry_premium, exit_premium, gross_pnl_inr, net_pnl_inr
            FROM go_open
            WHERE status IN ('CLOSED', 'TRAIL_STOP') AND closed_at IS NOT NULL
              AND substr(closed_at, 1, 10) <> substr(opened_at, 1, 10)
            ORDER BY opened_at""")
        go_ids = {r[0] for r in go.execute("SELECT trade_id FROM go_open WHERE trade_id IS NOT NULL")}
    with sqlite3.connect(journal / "paper_trades.db") as pt:
        candidates = _rows(pt, """
            SELECT trade_id, symbol, exit_by, strategy_name, entry_ts, exit_ts, exit_reason,
                   exit_net_premium, net_realized_pnl
            FROM paper_trades
            WHERE status = 'CLOSED' AND exit_ts IS NOT NULL
              AND substr(exit_ts, 1, 10) <> substr(entry_ts, 1, 10)
              AND coalesce(exit_reason, '') NOT LIKE 'VOID_%'
            ORDER BY entry_ts""")
    plan.paper = [r for r in candidates if r["trade_id"] in go_ids or r["exit_by"] == SENTINEL]
    voided_paper = {r["trade_id"] for r in plan.paper}
    with sqlite3.connect(journal / "recommendations.db") as rc:
        recs = _rows(rc, f"""
            SELECT rec_id, symbol, status, created_at, outcome_ts, paper_trade_id
            FROM sentinel_trade_recommendations
            WHERE status IN ({",".join("?" * len(RESOLVED))})
            ORDER BY created_at""", RESOLVED)
    plan.recs = [
        r for r in recs
        if r["paper_trade_id"] in voided_paper
        or (r["outcome_ts"] and str(r["outcome_ts"])[:10] != str(r["created_at"])[:10])
    ]
    return plan


def _backup(db: Path, stamp: str) -> Path:
    dst = db.with_name(f"{db.name}.bak-voidxday-{stamp}")
    with sqlite3.connect(db) as src, sqlite3.connect(dst) as out:
        src.backup(out)
    return dst


def apply_plan(journal: Path, plan: Plan, stamp: str) -> None:
    voided_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    if plan.go:
        db = journal / "go_efficiency.db"
        _backup(db, stamp)
        with sqlite3.connect(db) as con:
            for r in plan.go:
                raw = con.execute("SELECT decision_json FROM go_open WHERE decision_id = ?",
                                  (r["decision_id"],)).fetchone()[0]
                try:
                    dj = json.loads(raw or "{}")
                    dj = dj if isinstance(dj, dict) else {"original": dj}
                except ValueError:
                    dj = {"original": raw}
                dj["void_cross_day"] = {k: r[k] for k in (
                    "status", "exit_reason", "exit_premium", "closed_at", "gross_pnl_inr", "net_pnl_inr")}
                dj["void_cross_day"]["voided_at"] = voided_at
                con.execute("""
                    UPDATE go_open SET status = 'VOID', exit_reason = ?, exit_premium = entry_premium,
                        gross_pnl_inr = 0, brokerage_cost = 0, slippage_cost = 0, net_pnl_inr = 0,
                        decision_json = ?
                    WHERE decision_id = ? AND status IN ('CLOSED', 'TRAIL_STOP')""",
                            (REASON, json.dumps(dj), r["decision_id"]))
    if plan.paper:
        db = journal / "paper_trades.db"
        _backup(db, stamp)
        with sqlite3.connect(db) as con:
            for r in plan.paper:
                note = (f"{REASON} {voided_at}: was {r['exit_reason']} at {r['exit_ts']}, exit "
                        f"{r['exit_net_premium']}, net {r['net_realized_pnl']}. Carried past its entry "
                        "session; no same-day exit price survives.")
                con.execute("""
                    UPDATE paper_trades SET exit_reason = ?, exit_net_premium = entry_net_premium,
                        gross_pnl = 0, brokerage_cost = 0, slippage_cost = 0, net_realized_pnl = 0,
                        notes = CASE WHEN coalesce(notes, '') <> '' THEN notes || char(10) || ? ELSE ? END
                    WHERE trade_id = ? AND coalesce(exit_reason, '') NOT LIKE 'VOID_%'""",
                            (REASON, note, note, r["trade_id"]))
    if plan.recs:
        db = journal / "recommendations.db"
        _backup(db, stamp)
        with sqlite3.connect(db) as con:
            con.executemany(
                f"UPDATE sentinel_trade_recommendations SET status = 'VOID' "
                f"WHERE rec_id = ? AND status IN ({','.join('?' * len(RESOLVED))})",
                [(r["rec_id"], *RESOLVED) for r in plan.recs])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    journal = a.root / "data" / "journal"
    mode = "APPLY" if a.apply else "DRY RUN"
    plan = build_plan(journal)
    for r in plan.go:
        print(f"[{mode}] go_open {r['trade_id']} {r['symbol']} {r['opened_at']} → {r['closed_at']} "
              f"{r['exit_reason']} net {r['net_pnl_inr']}")
    for r in plan.paper:
        print(f"[{mode}] paper   {r['trade_id']} {r['symbol']} {r['entry_ts']} → {r['exit_ts']} "
              f"{r['exit_reason']} net {r['net_realized_pnl']}")
    for r in plan.recs:
        print(f"[{mode}] rec     {r['rec_id']} {r['status']} {r['created_at']} → {r['outcome_ts']}")
    go_net = sum(float(r["net_pnl_inr"] or 0) for r in plan.go)
    paper_net = sum(float(r["net_realized_pnl"] or 0) for r in plan.paper)
    print(f"[{mode}] {len(plan.go)} go_open (net {go_net:+,.1f}), {len(plan.paper)} paper "
          f"(net {paper_net:+,.1f}), {len(plan.recs)} recs")
    if a.apply:
        apply_plan(journal, plan, time.strftime("%Y%m%d-%H%M%S"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
