"""PHASE 0 (big-money tape) — VERIFY-LIVE whether this Dhan token can open the LIVE Market-Feed
WEBSOCKET, and see WHICH packet tier / fields actually arrive. This is the single gate before any
of the futures-tape build ([W2]); we've only ever used the historical REST endpoint, so the live
feed entitlement is an UNKNOWN (CLAUDE.md: verify-live, never guess).

What it does — connects to wss://api-feed.dhan.co (v2), subscribes to the CURRENT-EXPIRY NIFTY and
BANKNIFTY futures (ids resolved from the scrip master via near_month_future_id), and listens for a
few seconds. It subscribes at ALL THREE tiers (Ticker / Quote / Full) and tallies which packet
codes come back, so we learn the max tier available:
  • Ticker (code 2) → LTP + last-trade-time only
  • Quote  (code 4) → LTP + **LTQ** + volume + OHLC          ← our abs-₹ filter needs LTQ
  • Full   (code 8) → the above + OI + 5-level **bid/ask**    ← aggressor-side (Lee-Ready) needs this

Best-effort decodes the header + LTP (auto-detects byte order by which gives a sane price) and prints
one raw packet in hex for offset verification. The token/clientId are NEVER printed (URL is masked).
Throwaway — safe to delete.

  python scripts/dhan_ws_probe.py            # ~20s listen
  python scripts/dhan_ws_probe.py 30         # custom listen seconds

⚠️ Futures only trade 09:15–15:30 IST on weekdays. Outside that you may CONNECT fine (entitlement OK)
but receive few/no TRADE ticks — the verdict distinguishes "auth failed" from "connected, no data"."""

from __future__ import annotations

import json
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.dhan import DHAN_SEGMENT, near_month_future_id, token_status  # noqa: E402
from data.dhan import _secret  # noqa: E402  (reuse the same non-logging secret loader)

WS_URL = "wss://api-feed.dhan.co"
TARGETS = ["NIFTY", "BANKNIFTY"]
# v2 subscribe request codes → the packet tier they ask for.
SUB_CODES = [(15, "Ticker"), (17, "Quote"), (21, "Full")]
# v2 feed response codes (first byte of each binary packet).
RESP = {2: "Ticker", 4: "Quote", 5: "OI", 6: "PrevClose", 7: "MktStatus", 8: "Full", 50: "Disconnect"}
LTP_MIN, LTP_MAX = 50.0, 300000.0            # a sane index-future price band (endianness sanity check)


def _print(s: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    print(s, flush=True)


def _mask(s: str) -> str:
    """Show only the first/last 2 chars of a secret — never the whole thing."""
    s = s or ""
    return f"{s[:2]}…{s[-2:]} (len {len(s)})" if len(s) > 5 else "…"


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _market_open(ist) -> bool:
    try:
        from data.holidays import is_trading_day
        if not is_trading_day(ist.date()):
            return False
    except Exception:
        if ist.weekday() >= 5:
            return False
    mins = ist.hour * 60 + ist.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30


def _decode(data: bytes) -> dict:
    """Best-effort decode of a Dhan binary feed packet: header (code/len/seg/securityId) + LTP.
    Auto-detects byte order by whichever yields a price in the sane band. Returns a dict; ``ltp`` /
    ``endian`` are None if neither order looks sane (offsets may differ — the raw hex is printed)."""
    if len(data) < 8:
        return {"code": data[0] if data else None, "len": len(data), "raw": True}
    code = data[0]
    out = {"code": code, "name": RESP.get(code, f"?{code}"), "len": len(data)}
    for endian in ("<", ">"):
        try:
            out["seg"] = data[3]
            out["sec"] = struct.unpack_from(endian + "I", data, 4)[0]
            if len(data) >= 12 and code in (2, 4, 8):
                ltp = struct.unpack_from(endian + "f", data, 8)[0]
                if LTP_MIN <= ltp <= LTP_MAX:
                    out["ltp"], out["endian"] = round(ltp, 2), endian
                    break
        except struct.error:
            pass
    return out


def main() -> None:
    listen_s = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    ist = _ist_now()
    _print(f"Dhan Market-Feed WS probe · {ist:%Y-%m-%d %H:%M:%S} IST · "
           f"market {'OPEN' if _market_open(ist) else 'CLOSED (few/no trade ticks expected)'}")

    ts = token_status()
    if not ts["present"]:
        _print("✗ No Dhan token — set DHAN_ACCESS_TOKEN or create data/.dhan_token. Aborting.")
        return
    hl = ts["hours_left"]
    _print(f"  token: source={ts['source']}  valid≈{('%.1fh' % hl) if hl is not None else '?'}"
           f"{'  ⚠️ EXPIRED' if ts['expired'] else ''}")
    if ts["expired"]:
        _print("✗ Token appears expired — repaste a fresh one before probing. Aborting.")
        return

    token = _secret("DHAN_ACCESS_TOKEN", ".dhan_token")
    client = _secret("DHAN_CLIENT_ID", ".dhan_client_id")
    if not client:
        _print("✗ Dhan v2 feed needs a client id — set DHAN_CLIENT_ID or data/.dhan_client_id. Aborting.")
        return

    # resolve the two current-expiry futures (ids/lots from the scrip master — already solved code)
    instruments, id2name = [], {}
    for sym in TARGETS:
        meta = near_month_future_id(sym)
        if not meta:
            _print(f"  ⚠️ {sym}: no active future in the scrip master — skipping")
            continue
        seg = DHAN_SEGMENT.get(sym, "NSE_FNO")
        instruments.append({"ExchangeSegment": seg, "SecurityId": str(meta["security_id"])})
        id2name[int(meta["security_id"])] = f"{sym}({meta['symbol']}, lot {meta['lot']}, exp {meta['expiry']})"
        _print(f"  {sym:<9} id={meta['security_id']} seg={seg} lot={meta['lot']} exp={meta['expiry']}")
    if not instruments:
        _print("✗ No instruments resolved. Aborting.")
        return

    url = f"{WS_URL}?version=2&token={token}&clientId={client}&authType=2"
    _print(f"\n  connecting: {WS_URL}?version=2&token={_mask(token)}&clientId={_mask(client)}&authType=2")

    try:
        import websocket  # websocket-client
    except ImportError:
        _print("✗ websocket-client not installed (pip install websocket-client). Aborting.")
        return

    try:
        ws = websocket.create_connection(url, timeout=12)
    except websocket.WebSocketBadStatusException as e:
        _print(f"\n✗ HANDSHAKE REJECTED — HTTP {getattr(e, 'status_code', '?')}. This is the "
               f"entitlement/auth signal: the token is valid for REST but the live Market-Feed "
               f"websocket is NOT enabled on this plan (or the clientId/token pair is wrong).")
        return
    except Exception as e:  # noqa: BLE001
        _print(f"\n✗ CONNECT FAILED ({type(e).__name__}): {str(e)[:200]}")
        return
    _print("  ✓ socket open — subscribing (Ticker + Quote + Full) …")

    for code, label in SUB_CODES:
        ws.send(json.dumps({"RequestCode": code, "InstrumentCount": len(instruments),
                            "InstrumentList": instruments}))

    seen_codes: dict[int, int] = {}
    seen_ids: set[int] = set()
    sample_ltp: dict[int, float] = {}
    endian_seen: set[str] = set()
    printed_hex = False
    deadline = time.time() + listen_s
    ws.settimeout(2.0)
    try:
        while time.time() < deadline:
            try:
                opcode, frame = ws.recv_data()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:  # noqa: BLE001
                _print(f"  recv error: {type(e).__name__}: {str(e)[:120]}")
                break
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                _print("  server closed the stream.")
                break
            if not isinstance(frame, (bytes, bytearray)) or not frame:
                if isinstance(frame, str):
                    _print(f"  text frame: {frame[:200]}")
                continue
            d = _decode(bytes(frame))
            c = d.get("code")
            seen_codes[c] = seen_codes.get(c, 0) + 1
            if d.get("sec"):
                seen_ids.add(d["sec"])
            if d.get("ltp") is not None:
                sample_ltp[d.get("sec")] = d["ltp"]
                endian_seen.add(d.get("endian"))
            if c == 50:                       # disconnect/feed-error packet — decode its reason code
                reason = struct.unpack_from("<H", frame, 1)[0] if len(frame) >= 3 else "?"
                _print(f"  ⚠️ DISCONNECT packet from server (reason code {reason})")
            if not printed_hex and c in (2, 4, 8):
                _print(f"  first {d.get('name')} packet ({d['len']}B) hex: {bytes(frame)[:24].hex(' ')}…")
                printed_hex = True
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- verdict ------------------------------------------------------------------
    _print("\n" + "=" * 68)
    total = sum(seen_codes.values())
    tiers = {2: "Ticker", 4: "Quote", 8: "Full"}
    if total == 0:
        _print("△ CONNECTED but received NO packets.")
        _print("   • If the market is CLOSED this is expected (no trades) — retry 09:15–15:30 IST.")
        _print("   • If OPEN, the subscribe may be malformed or the feed tier is not entitled.")
        return
    _print(f"✓ ENTITLEMENT CONFIRMED — {total} packets in {listen_s:.0f}s from "
           f"{len(seen_ids)} instrument id(s).")
    _print("  packet tiers received:")
    for cc, nm in sorted(RESP.items()):
        if cc in seen_codes:
            _print(f"    {nm:<10} (code {cc}): {seen_codes[cc]}")
    for cc in (2, 4, 8):
        have = cc in seen_codes
        need = {2: "LTP only", 4: "LTP + LTQ + volume (needed for the ₹ filter)",
                8: "+ OI + 5-level bid/ask (needed for aggressor side)"}[cc]
        _print(f"  {'✓' if have else '·'} {tiers[cc]:<7} → {need}")
    if sample_ltp:
        _print("  sample LTPs (endianness "
               f"{'/'.join(sorted(endian_seen)) or '?'}): "
               + ", ".join(f"{id2name.get(k, k)}={v}" for k, v in sample_ltp.items()))
    else:
        _print("  ⚠️ header decoded but LTP did not land in a sane band — packet offsets/byte order "
               "differ from the assumption; use the printed hex to fix the decoder before building.")
    _print("\n  Next: if Quote+Full are ticked, the futures big-money tape ([W2]) is buildable as "
           "specced (₹ filter + aggressor side). If only Ticker arrives, we get size-free LTP and "
           "must drop aggressor-side (or upgrade the plan). Decision-support; verify-live done.")


if __name__ == "__main__":
    main()
