"""T8 Persistence Layer for Target Engine.

Stores and queries target runs, scenario probabilities, underlying levels,
candidate structures, and past track records in SQLite.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from target_engine.contracts import (
    FeatureVector,
    IVForecast,
    ScenarioSet,
    TradeCandidate,
    UnderlyingTarget,
)
from target_engine.time_utils import to_ist

log = logging.getLogger("target_engine.storage")
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "journal" / "target_engine.db"


def get_db_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open connection to Target Engine SQLite database with WAL mode."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                spot REAL NOT NULL,
                fut REAL NOT NULL,
                atm_iv REAL,
                dominant_scenario TEXT NOT NULL,
                conviction REAL NOT NULL,
                no_edge INTEGER NOT NULL,
                model_version TEXT NOT NULL,
                p_bull REAL,
                p_base REAL,
                p_bear REAL,
                quality_flags TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_ts ON target_runs(symbol, ts)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                scenario TEXT NOT NULL,
                prob REAL NOT NULL,
                target REAL NOT NULL,
                stop REAL NOT NULL,
                p_touch_target REAL,
                p_touch_stop REAL,
                p_close_beyond REAL,
                iv_at_horizon REAL,
                reason TEXT,
                FOREIGN KEY(run_id) REFERENCES target_runs(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS candidate_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                structure_type TEXT NOT NULL,
                expected_value REAL NOT NULL,
                risk_reward REAL NOT NULL,
                max_loss_inr REAL NOT NULL,
                rank_score REAL NOT NULL,
                passes_filters INTEGER NOT NULL,
                legs_json TEXT,
                explain_json TEXT,
                FOREIGN KEY(run_id) REFERENCES target_runs(id) ON DELETE CASCADE
            )
        """)

    return conn


def persist_target_run(
    feature_vector: FeatureVector,
    scenario_set: ScenarioSet,
    underlying_targets: Dict[str, UnderlyingTarget],
    iv_forecasts: Dict[str, IVForecast],
    candidates: List[TradeCandidate],
    symbol: str = "NIFTY",
    db_path: Path = DB_PATH,
) -> int:
    """Persist complete evaluation run to SQLite."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO target_runs (
                    ts, symbol, spot, fut, atm_iv, dominant_scenario,
                    conviction, no_edge, model_version, p_bull, p_base, p_bear, quality_flags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                feature_vector.ts.isoformat(),
                symbol,
                feature_vector.spot,
                feature_vector.fut,
                feature_vector.features.get("F13_atm_iv", 0.15),
                scenario_set.dominant_scenario,
                scenario_set.conviction,
                1 if scenario_set.no_edge else 0,
                scenario_set.model_version,
                scenario_set.get_prob("bull"),
                scenario_set.get_prob("base"),
                scenario_set.get_prob("bear"),
                json.dumps(feature_vector.quality_flags),
            ))
            run_id = cur.lastrowid

            # Insert scenarios
            scenario_rows = []
            for sc in ["bull", "base", "bear"]:
                target_obj = underlying_targets.get(sc)
                iv_obj = iv_forecasts.get(sc)
                if target_obj:
                    scenario_rows.append((
                        run_id,
                        sc,
                        scenario_set.get_prob(sc),
                        target_obj.target,
                        target_obj.stop,
                        target_obj.p_touch_target,
                        target_obj.p_touch_stop,
                        target_obj.p_close_beyond,
                        iv_obj.iv_at_horizon if iv_obj else 0.15,
                        target_obj.reason,
                    ))

            if scenario_rows:
                conn.executemany("""
                    INSERT INTO target_scenarios (
                        run_id, scenario, prob, target, stop,
                        p_touch_target, p_touch_stop, p_close_beyond,
                        iv_at_horizon, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, scenario_rows)

            # Insert candidates
            cand_rows = []
            for cand in candidates[:5]:  # Top 5 candidates
                legs_dict = [{"strike": l.strike, "type": l.option_type, "action": l.action, "premium": l.entry_premium} for l in cand.legs]
                cand_rows.append((
                    run_id,
                    cand.structure_type,
                    cand.expected_value,
                    cand.risk_reward,
                    cand.max_loss_inr,
                    cand.rank_score,
                    1 if cand.passes_filters else 0,
                    json.dumps(legs_dict),
                    json.dumps(cand.explain),
                ))

            if cand_rows:
                conn.executemany("""
                    INSERT INTO candidate_trades (
                        run_id, structure_type, expected_value, risk_reward,
                        max_loss_inr, rank_score, passes_filters, legs_json, explain_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, cand_rows)

            return run_id
    except Exception as exc:
        log.error(f"Error persisting target run: {exc}")
        return -1
    finally:
        conn.close()


def load_recent_runs(symbol: str = "NIFTY", limit: int = 10, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    """Query recent target engine runs."""
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute("""
            SELECT id, ts, spot, fut, dominant_scenario, conviction, no_edge, p_bull, p_base, p_bear
            FROM target_runs
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT ?
        """, (symbol, limit))
        rows = cur.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "ts": r[1],
                "spot": r[2],
                "fut": r[3],
                "dominant": r[4],
                "conviction": r[5],
                "no_edge": bool(r[6]),
                "p_bull": r[7],
                "p_base": r[8],
                "p_bear": r[9],
            })
        return results
    except Exception as exc:
        log.error(f"Error reading recent runs: {exc}")
        return []
    finally:
        conn.close()
