"""Hand-checkable max-pain fixture.

call OI: 90->100, 100->200, 110->0 ; put OI: 90->0, 100->200, 110->100
Total holder payout (contract scaling cancels in the argmin):
  P=90 : puts 200*(100-90)+100*(110-90) = 2000+2000 = 4000
  P=100: calls 100*(100-90)=1000 ; puts 100*(110-100)=1000 -> 2000  <- min
  P=110: calls 100*(110-90)+200*(110-100)=2000+2000=4000
Max pain = 100.
"""

import pandas as pd

from gex.maxpain import max_pain


def test_max_pain_hand_calc():
    rows = [
        (90, "C", 100), (90, "P", 0),
        (100, "C", 200), (100, "P", 200),
        (110, "C", 0), (110, "P", 100),
    ]
    df = pd.DataFrame(rows, columns=["strike", "type", "oi"])
    assert max_pain(df) == 100
