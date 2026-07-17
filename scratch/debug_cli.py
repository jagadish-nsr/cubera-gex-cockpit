from cli import _build_snapshot
from types import SimpleNamespace
args = SimpleNamespace(demo=True, window=None, symbol="NIFTY")
snap, idx = _build_snapshot(args)
print(len(snap.contracts))
