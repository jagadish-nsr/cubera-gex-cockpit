#!/usr/bin/env python3
"""Run Pillar G fault injections against the chaos host (default :8660).

Each fault must turn the session checklist FAIL. Does not touch live DBs.

Usage:
  ./scripts/run_chaos.sh &          # or already running
  python3 scripts/chaos_drill.py
  python3 scripts/chaos_drill.py --base http://127.0.0.1:8660
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

CASES: list[tuple[str, dict[str, bool], set[str]]] = [
    ("empty_chain", {"empty_chain": True, "force_market_open": True}, {"chain", "readiness"}),
    ("feed_silence", {"feed_silence": True, "force_market_open": True}, {"feed", "readiness"}),
    ("cb_open", {"cb_open": True, "force_market_open": True}, {"circuits"}),
    ("stale_spot", {"stale_spot": True, "force_market_open": True}, {"spot", "readiness"}),
    ("missing_vwap", {"missing_vwap": True, "force_market_open": True}, {"ideas_vwap"}),
]


def _req(base: str, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8660")
    args = parser.parse_args()

    try:
        health = _req(args.base, "GET", "/api/health")
    except urllib.error.URLError as exc:
        print(f"REFUSED: chaos host unreachable at {args.base}: {exc}", file=sys.stderr)
        print("Start with: ./scripts/run_chaos.sh", file=sys.stderr)
        return 2

    if health.get("env") != "chaos":
        print(f"REFUSED: {args.base} is not the chaos host (env={health.get('env')})", file=sys.stderr)
        return 2

    forbidden = health.get("forbidden_live_dbs") or []
    print(f"chaos host ok version={health.get('version')} forbidden_paths={len(forbidden)}")

    _req(args.base, "POST", "/api/chaos/reset")
    failures: list[str] = []
    for name, inject, expect_failed in CASES:
        _req(args.base, "POST", "/api/chaos/reset")
        out = _req(args.base, "POST", "/api/chaos/inject", inject)
        cl = out.get("checklist") or _req(args.base, "GET", "/api/admin/session-checklist")
        failed = set(cl.get("failed") or [])
        ok = (cl.get("pass") is False) and bool(expect_failed & failed)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}: failed={sorted(failed)} expect∩={sorted(expect_failed & failed)}")
        if not ok:
            failures.append(name)

    _req(args.base, "POST", "/api/chaos/reset")
    if failures:
        print(f"DRILL FAIL: {', '.join(failures)}", file=sys.stderr)
        return 1
    print("DRILL PASS: all injected faults failed the checklist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
