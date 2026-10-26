"""Max pain: the strike that minimises total ITM payout to option holders at
expiry, summed over open interest. Separate, simpler calc over OI only.

At hypothetical settlement price P:
    call holders' payout at strike k = max(P - k, 0) * callOI[k] * contract_size
    put  holders' payout at strike k = max(k - P, 0) * putOI[k]  * contract_size
Max pain = the listed strike P minimising the grand total.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def max_pain(contracts: pd.DataFrame, contract_size: int = 1) -> float:
    """contracts columns required: strike, type ("C"/"P"), oi.

    Evaluated over the set of listed strikes (standard convention).
    """
    required = {"strike", "type", "oi"}
    missing = required - set(contracts.columns)
    if missing:
        raise ValueError(f"contracts missing columns for max_pain: {sorted(missing)}")

    df = contracts.copy()
    df["type"] = df["type"].str.upper()
    calls = df[df["type"] == "C"].groupby("strike")["oi"].sum()
    puts = df[df["type"] == "P"].groupby("strike")["oi"].sum()

    strikes = np.sort(df["strike"].unique().astype(float))
    call_k = strikes
    call_oi = calls.reindex(strikes, fill_value=0).to_numpy(dtype=float)
    put_oi = puts.reindex(strikes, fill_value=0).to_numpy(dtype=float)

    totals = np.empty(len(strikes))
    for i, P in enumerate(strikes):
        call_pay = np.maximum(P - call_k, 0.0) * call_oi
        put_pay = np.maximum(call_k - P, 0.0) * put_oi
        totals[i] = (call_pay.sum() + put_pay.sum()) * contract_size

    return float(strikes[int(np.argmin(totals))])
