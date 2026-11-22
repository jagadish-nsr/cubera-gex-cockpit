#!/usr/bin/env python3
"""Minimal CLI: replay recorded ticks through the live feature path.

Example:
  python3 -m crypto.replay_cli crypto/fixtures/btc_chain.json
  python3 -m crypto.replay_cli tests/cryptocubera/fixtures/replay_bundle.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _ser(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _ser(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_ser(v) for v in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CryptoCubera feature-path replay")
    parser.add_argument("bundle", type=Path, help="JSON chain/trades/perp bundle")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON (default: short summary)",
    )
    args = parser.parse_args(argv)

    from crypto.engine.replay import replay_recorded_file

    result = replay_recorded_file(args.bundle)
    if args.json:
        slim = {
            "bundle": result.get("bundle"),
            "gex": {
                k: result["gex"][k]
                for k in (
                    "spot",
                    "flip",
                    "call_wall",
                    "put_wall",
                    "regime",
                    "convention_sign",
                    "flow_sign",
                    "models_agree",
                    "flow_mode",
                )
                if k in result["gex"]
            },
            "orderflow": {
                k: result["orderflow"][k]
                for k in (
                    "cvd_1m",
                    "cvd_5m",
                    "cvd_15m",
                    "book_imbalance",
                    "oi_delta",
                    "funding_rate",
                    "basis_bps",
                )
                if k in result["orderflow"]
            },
            "signal": {
                k: result["signal"][k]
                for k in (
                    "status",
                    "setup",
                    "side",
                    "score",
                    "veto_reasons",
                    "model_version",
                    "rules_version",
                )
                if k in result["signal"]
            },
            "inference": result.get("inference"),
            "flow_inventory": result.get("flow_inventory"),
        }
        print(json.dumps(_ser(slim), indent=2))
        return 0

    gex = result["gex"]
    card = result["signal"]
    print(f"bundle: {args.bundle}")
    print(
        f"gex: regime={gex.get('regime')} agree={gex.get('models_agree')} "
        f"flow_mode={gex.get('flow_mode')} flip={gex.get('flip')}"
    )
    print(
        f"signal: {card.get('status')} setup={card.get('setup')} "
        f"score={card.get('score')} model={card.get('model_version')}"
    )
    if card.get("veto_reasons"):
        print(f"vetoes: {card['veto_reasons']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
