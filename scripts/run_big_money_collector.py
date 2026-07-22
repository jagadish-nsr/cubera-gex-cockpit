"""Big-money futures tape — the always-on collector ([W2], docs/big-money-tape.md).

Wires the live Dhan feed → decoder → filter → event store: subscribes to the CURRENT-EXPIRY NIFTY +
BankNifty futures at the Full tier, decodes each packet, and appends every qualifying big-money Event
(single PRINT ≥ ₹floor, or a sliced BURST over the window) to data/journal/big_money/<SYMBOL>/<day>.jsonl
— which the dashboard tape fragment tails. Prints each event live too.

  python scripts/run_big_money_collector.py                 # run until Ctrl-C, write + print
  python scripts/run_big_money_collector.py --dry-run       # print only, no store (day-1 calibration)
  python scripts/run_big_money_collector.py --seconds 120   # stop after N seconds (a bounded test run)
  python scripts/run_big_money_collector.py --show-all      # also print a compact heartbeat of ALL ticks

Reconnects on drop (token is 24h-capped — repaste data/.dhan_token and it resumes on the next retry).
Exchange tick time stamps each event. Sign-free; decision-support; NEVER auto-executes. Token not logged."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.big_money import BigMoneyDetector, append_event    # noqa: E402
from data.dhan_feed import SUB_FULL, connect, decode_packet, resolve_futures  # noqa: E402


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _fmt(ev) -> str:
    arrow = {"buy": "▲BUY ", "sell": "▼SELL", None: "  •  "}[ev.side]
    cr = ev.notional / 1e7
    lots = f"{ev.lots:.0f}L" if ev.lots is not None else "?"
    tag = "PRINT" if ev.kind == "print" else "BURST"
    return (f"  {ev.ts_ist[11:]}  {ev.symbol:<9} {arrow} {tag}  {lots:>5}  ₹{cr:>5.2f}cr  @{ev.price:.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only, do not write the store")
    ap.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds (0 = forever)")
    ap.add_argument("--show-all", action="store_true", help="also print a heartbeat of all ticks")
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY")
    ap.add_argument("--print-floor", type=float, default=None,
                    help="₹ single-print floor for ALL symbols (calibration override)")
    ap.add_argument("--burst-floor", type=float, default=None,
                    help="₹ window-burst floor for ALL symbols (calibration override)")
    args = ap.parse_args()

    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    contracts = resolve_futures(symbols)
    if not contracts:
        _print("✗ No futures resolved from the scrip master. Aborting."); return
    for c in contracts.values():
        _print(f"  watching {c['symbol']:<9} id={c['security_id']} lot={c['lot']} exp={c['expiry']}")
    _print(f"  mode: {'DRY-RUN (no store)' if args.dry_run else 'LIVE → data/journal/big_money/'}"
           f"{'  · stop in %.0fs' % args.seconds if args.seconds else '  · Ctrl-C to stop'}\n")

    pf = {s: args.print_floor for s in symbols} if args.print_floor is not None else None
    bf = {s: args.burst_floor for s in symbols} if args.burst_floor is not None else None
    detector = BigMoneyDetector(contracts, print_floor=pf, burst_floor=bf)
    if pf or bf:
        _print(f"  floors overridden: print={args.print_floor} burst={args.burst_floor}\n")
    import websocket

    deadline = time.time() + args.seconds if args.seconds else None
    n_pkt = n_ev = 0
    while deadline is None or time.time() < deadline:
        try:
            ws = connect(contracts, tiers=(SUB_FULL,))
        except RuntimeError as e:
            _print(f"✗ {e}"); return
        except Exception as e:  # noqa: BLE001 - handshake/network → retry
            _print(f"  connect failed ({type(e).__name__}); retrying in 5s…")
            time.sleep(5); continue
        _print("  ✓ connected — streaming.")
        ws.settimeout(2.0)
        try:
            while deadline is None or time.time() < deadline:
                try:
                    opcode, frame = ws.recv_data()
                except websocket.WebSocketTimeoutException:
                    continue
                if opcode == websocket.ABNF.OPCODE_CLOSE:
                    _print("  server closed; reconnecting…"); break
                if not isinstance(frame, (bytes, bytearray)) or len(frame) < 8:
                    continue
                tick = decode_packet(bytes(frame))
                if tick is None:
                    continue
                n_pkt += 1
                if args.show_all and tick.ltp:
                    _print(f"    · {tick.name:<9} {tick.security_id} ltp={tick.ltp:.1f} "
                           f"ltq={tick.ltq} vol={tick.volume}")
                for ev in detector.on_tick(tick):
                    n_ev += 1
                    _print(_fmt(ev))
                    if not args.dry_run:
                        append_event(ev)
        except KeyboardInterrupt:
            _print("\n  stopped by user."); break
        except Exception as e:  # noqa: BLE001 - stream error → reconnect
            _print(f"  stream error ({type(e).__name__}: {str(e)[:120]}); reconnecting…")
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        if deadline is not None and time.time() >= deadline:
            break

    _print(f"\n  done — {n_pkt} packets, {n_ev} big-money events.")


if __name__ == "__main__":
    main()
