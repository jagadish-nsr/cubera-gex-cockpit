"""Hand checks for the shared latest_snap writer."""

from __future__ import annotations

import pickle
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.latest_snap import snap_path_for, write_latest_snap


def test_snap_path_names_the_symbol(tmp_path: Path) -> None:
    assert snap_path_for("nifty", tmp_path).name == "latest_snap_NIFTY.pkl"
    assert snap_path_for("BANKNIFTY", tmp_path).name == "latest_snap_BANKNIFTY.pkl"


def test_two_writers_leave_one_readable_pickle(tmp_path: Path) -> None:
    """A shared ``.pkl.tmp`` name used to make the second os.replace fail."""
    path = snap_path_for("NIFTY", tmp_path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def publish(tag: str) -> None:
        try:
            barrier.wait(timeout=2)
            write_latest_snap(
                path,
                {"data": tag, "symbol": "NIFTY", "window": None, "fetched_at": tag},
            )
        except BaseException as exc:  # noqa: BLE001 — collect for the assert below
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(publish, ("a", "b")))

    assert errors == [], errors
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    assert payload["symbol"] == "NIFTY"
    assert payload["data"] in ("a", "b")
    leftovers = list(tmp_path.glob(".latest_snap_NIFTY.pkl.*.tmp"))
    assert leftovers == []
