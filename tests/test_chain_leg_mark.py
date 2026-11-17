"""The staged expiry is the mark. A later expiry's premium is a different contract."""

from __future__ import annotations

import pandas as pd

from analytics.trade_ideas import vanna_charm_label
from web_server import _chain_leg_ltp, _expiry_ord


def test_front_expiry_wins_when_the_leg_does_not_name_one():
    chain = pd.DataFrame([
        {"strike": 25400, "type": "CE", "expiry": "24NOV2026", "ltp": 162.57},
        {"strike": 25400, "type": "CE", "expiry": "17NOV2026", "ltp": 62.08},
    ])
    assert _expiry_ord("17NOV2026") < _expiry_ord("24NOV2026")
    assert _chain_leg_ltp(chain, 25400, "CE") == 62.08


def test_named_expiry_is_the_staged_leg():
    chain = pd.DataFrame([
        {"strike": 25450, "type": "CE", "expiry": "17NOV2026", "ltp": 24.73},
        {"strike": 25450, "type": "CE", "expiry": "24NOV2026", "ltp": 51.18},
    ])
    assert _chain_leg_ltp(chain, 25450, "CE", expiry="24NOV2026") == 51.18
    assert _chain_leg_ltp(chain, 25450, "CE", expiry="17NOV2026") == 24.73


def test_zero_dte_label_is_the_expiry_session_only():
    name, tags = vanna_charm_label(1.0)
    assert name == "Vanna-Charm Theta Compression"
    assert "0DTE" not in name
    assert "0DTE" not in " ".join(tags)
    expiry_name, expiry_tags = vanna_charm_label(0.4)
    assert expiry_name.startswith("0DTE")
    assert "0DTE Charm Decay" in expiry_tags
