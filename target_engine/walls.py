"""Wall detection algorithm for Target Engine (T1 & T3 spec).

Identifies significant call resistance walls and put support walls within
the ATM strike window, computes relative OI share and buildup strength,
and merges adjacent wall clusters into zones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import pandas as pd


@dataclass
class OptionWall:
    """Detected structural option wall or wall zone."""
    strike: float               # Lower/primary strike
    side: str                   # "CALL" or "PUT"
    oi: float                   # Total Open Interest
    oi_share: float             # Share of total side OI within window [0, 1]
    oi_change: float            # Aggregate OI change in period
    strength: float             # Combined strength metric (OI share + OI change momentum)
    is_zone: bool = False       # True if merged cluster of multiple adjacent strikes
    strike_high: Optional[float] = None  # Upper strike if is_zone is True

    @property
    def label(self) -> str:
        if self.is_zone and self.strike_high and self.strike_high != self.strike:
            return f"{int(self.strike)}-{int(self.strike_high)} {self.side} Zone"
        return f"{int(self.strike)} {self.side} Wall"


def detect_walls(
    contracts_df: pd.DataFrame,
    spot: float,
    strike_window: int = 10,
    min_oi_share: float = 0.15,
) -> Tuple[List[OptionWall], List[OptionWall]]:
    """Detect significant call and put walls around spot price.

    Parameters
    ----------
    contracts_df : pd.DataFrame
        DataFrame with columns: ['strike', 'type', 'oi', 'change_oi' (optional)]
    spot : float
        Current underlying spot or futures price.
    strike_window : int
        Number of strikes above and below ATM to include in calculation.
    min_oi_share : float
        Minimum percentage share of side window OI (e.g., 0.15 for 15%).

    Returns
    -------
    Tuple[List[OptionWall], List[OptionWall]]
        (call_walls, put_walls) sorted by proximity to spot.
    """
    if contracts_df is None or contracts_df.empty or spot <= 0.0:
        return [], []

    df = contracts_df.copy()
    if "strike" not in df.columns or "oi" not in df.columns:
        return [], []

    if "type" not in df.columns:
        return [], []

    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["oi"] = pd.to_numeric(df["oi"], errors="coerce").fillna(0.0)
    chg_col = "change_oi" if "change_oi" in df.columns else ("oi_change" if "oi_change" in df.columns else None)
    if chg_col:
        df["oi_change"] = pd.to_numeric(df[chg_col], errors="coerce").fillna(0.0)
    else:
        df["oi_change"] = 0.0

    df = df.dropna(subset=["strike"])
    df["type_norm"] = df["type"].astype(str).str.upper()

    all_strikes = sorted(df["strike"].unique())
    if not all_strikes:
        return [], []

    # Find ATM strike
    atm_strike = min(all_strikes, key=lambda k: abs(k - spot))
    atm_idx = all_strikes.index(atm_strike)

    min_idx = max(0, atm_idx - strike_window)
    max_idx = min(len(all_strikes) - 1, atm_idx + strike_window)
    window_strikes = set(all_strikes[min_idx : max_idx + 1])

    # Subset window
    win_df = df[df["strike"].isin(window_strikes)]

    # 1. Calls: candidate walls are strikes >= spot
    call_candidates = win_df[
        win_df["type_norm"].str.startswith(("C", "CE", "CALL")) & (win_df["strike"] >= spot)
    ]
    # 2. Puts: candidate walls are strikes <= spot
    put_candidates = win_df[
        win_df["type_norm"].str.startswith(("P", "PE", "PUT")) & (win_df["strike"] <= spot)
    ]

    call_walls = _process_side_walls(call_candidates, "CALL", min_oi_share, reverse_sort=False)
    put_walls = _process_side_walls(put_candidates, "PUT", min_oi_share, reverse_sort=True)

    return call_walls, put_walls


def _process_side_walls(
    df: pd.DataFrame,
    side: str,
    min_share: float,
    reverse_sort: bool,
) -> List[OptionWall]:
    """Process strikes for a given side, calculate shares, and merge adjacent zones."""
    if df.empty:
        return []

    agg = df.groupby("strike").agg({"oi": "sum", "oi_change": "sum"}).reset_index()
    total_oi = agg["oi"].sum()
    if total_oi <= 0:
        return []

    agg["oi_share"] = agg["oi"] / total_oi
    total_chg = abs(agg["oi_change"]).sum()
    if total_chg > 0:
        agg["chg_share"] = agg["oi_change"] / total_chg
    else:
        agg["chg_share"] = 0.0

    # Wall strength = OI share + 0.5 * chg_share
    agg["strength"] = agg["oi_share"] + 0.5 * agg["chg_share"].clip(lower=-0.5, upper=0.5)

    # Filter strikes with sufficient share
    candidates = agg[agg["oi_share"] >= min_share].sort_values("strike", ascending=not reverse_sort)
    if candidates.empty:
        # Fallback to absolute maximum strike on this side
        top_row = agg.sort_values("oi", ascending=False).iloc[0]
        return [
            OptionWall(
                strike=float(top_row["strike"]),
                side=side,
                oi=float(top_row["oi"]),
                oi_share=float(top_row["oi_share"]),
                oi_change=float(top_row["oi_change"]),
                strength=float(top_row["strength"]),
            )
        ]

    # Merge adjacent strikes into zones if consecutive
    raw_list = candidates.to_dict("records")
    merged: List[OptionWall] = []
    i = 0
    while i < len(raw_list):
        curr = raw_list[i]
        # Check if adjacent strike exists in candidates
        if i + 1 < len(raw_list):
            nxt = raw_list[i + 1]
            # Strikes are considered adjacent if within standard strike step (e.g. 50 or 100 pts)
            diff = abs(nxt["strike"] - curr["strike"])
            if diff <= 100.0:
                # Merge into zone
                total_zone_oi = curr["oi"] + nxt["oi"]
                total_zone_chg = curr["oi_change"] + nxt["oi_change"]
                merged_share = curr["oi_share"] + nxt["oi_share"]
                merged_strength = curr["strength"] + nxt["strength"]
                merged.append(
                    OptionWall(
                        strike=min(curr["strike"], nxt["strike"]),
                        side=side,
                        oi=total_zone_oi,
                        oi_share=merged_share,
                        oi_change=total_zone_chg,
                        strength=merged_strength,
                        is_zone=True,
                        strike_high=max(curr["strike"], nxt["strike"]),
                    )
                )
                i += 2
                continue

        merged.append(
            OptionWall(
                strike=float(curr["strike"]),
                side=side,
                oi=float(curr["oi"]),
                oi_share=float(curr["oi_share"]),
                oi_change=float(curr["oi_change"]),
                strength=float(curr["strength"]),
                is_zone=False,
            )
        )
        i += 1

    return merged
