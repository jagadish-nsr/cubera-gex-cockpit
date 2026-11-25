"""Read NSE Clearing's end-of-day SPAN file (format 4.00) for index futures.

Each futures contract carries 16 risk-array values per UNIT of the index
(not per lot). NSE's SPAN documentation: each ``ra`` is the P/L of one LONG
unit, losses positive, gains negative. Scenarios 1-16 run unchanged-up,
unchanged-down, then +1/3, -1/3, +2/3, -2/3, +3/3, -3/3, extremes.
So the worst loss for one long unit is max(a) (a rise of the index is a gain
for a long, so the loss comes from the down scenarios), and the worst loss for
one short unit is max(-a). Both are floored at zero.

Worst loss per lot = worst per unit * lot size. Divided by notional
(price * lot size), the lot cancels, so the SPAN fraction is worst_per_unit / price.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from pathlib import Path

_FUT_BLOCK = re.compile(r"<futPf>(.*?)</futPf>", re.S)
_FUT_LEG = re.compile(r"<fut>(.*?)</fut>", re.S)
_CODE = re.compile(r"<pfCode>([^<]+)</pfCode>")
_CREATED = re.compile(r"<created>(\d{8})\d{4}</created>")
_TAG = {
    "pe": re.compile(r"<pe>([^<]+)</pe>"),
    "p": re.compile(r"<p>([^<]+)</p>"),
    "cvf": re.compile(r"<cvf>([^<]+)</cvf>"),
    "setl": re.compile(r"<setlDate>([^<]+)</setlDate>"),
}
_RA = re.compile(r"<ra>(.*?)</ra>", re.S)
_A = re.compile(r"<a>([^<]+)</a>")


def _one(pattern: re.Pattern, text: str, name: str) -> str:
    m = pattern.search(text)
    if not m:
        raise ValueError(f"SPAN file: missing {name}")
    return m.group(1).strip()


def _yyyymmdd(text: str) -> date:
    return datetime.strptime(text, "%Y%m%d").date()


def parse_futures_span(text: str, indices: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")) -> dict:
    """Return the file date and, per index, the nearest unexpired futures contract.

    Output per index: expiry, price, worst_long_per_unit, worst_short_per_unit, span_frac_long,
    span_frac_short. Raises on a malformed file. Returns an empty map if an
    index is absent.
    """
    created = _CREATED.search(text)
    if not created:
        raise ValueError("SPAN file: missing <created> header")
    file_day = _yyyymmdd(created.group(1))

    out: dict[str, dict] = {}
    for block in _FUT_BLOCK.findall(text):
        code = _one(_CODE, block, "pfCode")
        if code not in indices:
            continue
        legs = []
        for leg in _FUT_LEG.findall(block):
            setl = _yyyymmdd(_one(_TAG["setl"], leg, "setlDate"))
            if setl < file_day:
                continue
            cvf = float(_one(_TAG["cvf"], leg, "cvf"))
            if abs(cvf - 1.0) > 1e-9:
                raise ValueError(f"SPAN file: {code} cvf {cvf} != 1.0 is not handled")
            ra = _RA.search(leg)
            if not ra:
                raise ValueError(f"SPAN file: {code} {setl} has no risk array")
            values = [float(v) for v in _A.findall(ra.group(1))]
            if len(values) != 16:
                raise ValueError(f"SPAN file: {code} {setl} has {len(values)} scenarios, expected 16")
            legs.append({
                "expiry": setl,
                "price": float(_one(_TAG["p"], leg, "price")),
                "worst_long_per_unit": max(0.0, max(values)),
                "worst_short_per_unit": max(0.0, -min(values)),
            })
        if not legs:
            continue
        near = min(legs, key=lambda leg: leg["expiry"])
        out[code] = {
            "index": code,
            "expiry": near["expiry"].isoformat(),
            "price": near["price"],
            "worst_long_per_unit": near["worst_long_per_unit"],
            "worst_short_per_unit": near["worst_short_per_unit"],
            "span_frac_long": near["worst_long_per_unit"] / near["price"],
            "span_frac_short": near["worst_short_per_unit"] / near["price"],
        }
    return {"file_date": file_day.isoformat(), "contracts": out}


def load_latest_span(span_dir: Path) -> tuple[str, dict] | None:
    """Parse the newest ``*.spn`` file in ``span_dir``. None if there is none."""
    if not span_dir.is_dir():
        return None
    files = sorted(span_dir.glob("nsccl.*.spn"))
    if not files:
        return None
    path = files[-1]
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r", "")
    return path.name, parse_futures_span(text)


def margin_fracs(contract: dict, exposure_frac: float) -> tuple[float, float]:
    """r_long and r_short, each = SPAN fraction of notional + exposure."""
    if exposure_frac is None or not (0 < float(exposure_frac) < 1):
        raise ValueError("exposure_frac must be a fraction between 0 and 1")
    r_long = float(contract["span_frac_long"]) + float(exposure_frac)
    r_short = float(contract["span_frac_short"]) + float(exposure_frac)
    if not (math.isfinite(r_long) and math.isfinite(r_short)):
        raise ValueError("margin fractions are not finite")
    return r_long, r_short
