#!/usr/bin/env python3
"""CLI: inspect / export a CryptoCubera paper recording session.

Local paper only — never enables exec.live.

Examples::

    python3 -m crypto.session_cli status
    python3 -m crypto.session_cli summary
    python3 -m crypto.session_cli summary --format csv -o paper_session.csv
    python3 -m crypto.session_cli list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CryptoCubera paper session summary")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show store path + current session")
    p_sum = sub.add_parser("summary", help="Print or export session summary")
    p_sum.add_argument("--session-id", default=None)
    p_sum.add_argument("--format", choices=("json", "csv"), default="json")
    p_sum.add_argument("-o", "--output", type=Path, default=None)
    p_list = sub.add_parser("list", help="List recent sessions")
    p_list.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)

    from crypto.store.db import init_db, store_status
    from crypto.store.session import (
        build_summary,
        current_session,
        export_summary_csv,
        export_summary_json,
        list_sessions,
    )

    init_db()

    if args.cmd == "status":
        payload = {
            "store": store_status(),
            "session": current_session(),
            "note": "Local paper recording. Keep exec.live off.",
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.cmd == "list":
        print(json.dumps({"sessions": list_sessions(limit=args.limit)}, indent=2))
        return 0

    if args.cmd == "summary":
        sid = args.session_id or ((current_session() or {}).get("session_id"))
        if args.format == "csv":
            body = export_summary_csv(session_id=sid)
        else:
            body = export_summary_json(session_id=sid)
        if args.output:
            args.output.write_text(body, encoding="utf-8")
            # Also print a one-line pointer
            summary = build_summary(session_id=sid)
            print(
                json.dumps(
                    {
                        "wrote": str(args.output),
                        "session_id": summary.get("session_id"),
                        "go_count": summary.get("go_count"),
                        "confirms": summary.get("confirms"),
                        "realized_pnl_usd": summary.get("realized_pnl_usd"),
                    },
                    indent=2,
                )
            )
        else:
            sys.stdout.write(body)
            if not body.endswith("\n"):
                sys.stdout.write("\n")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
