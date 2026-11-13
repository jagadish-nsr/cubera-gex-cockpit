#!/usr/bin/env python3
"""Post-open smoke — health schema, checklist, VWAP blackout, tape.

Exit 0 on pass, 1 on fail. Intended after ~09:16 IST or any time as a desk probe.

  python3 scripts/session_open_smoke.py
  python3 scripts/session_open_smoke.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Cubera session-open smoke")
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    args = parser.parse_args()

    try:
        from web_server import APP_VERSION
    except Exception:
        APP_VERSION = None

    from app.slo_monitor import run_session_open_smoke

    result = run_session_open_smoke(version=APP_VERSION)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = "PASS" if result.get("pass") else "FAIL"
        print(f"session_open_smoke: {status}")
        for c in result.get("checks") or []:
            mark = "✓" if c.get("ok") else "✗"
            print(f"  {mark} {c.get('id')}: {c.get('detail')}")
        if result.get("failed"):
            print(f"failed: {', '.join(result['failed'])}")
    return 0 if result.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
