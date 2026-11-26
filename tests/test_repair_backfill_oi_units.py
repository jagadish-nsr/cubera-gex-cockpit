import csv

import pytest

from scripts.repair_backfill_oi_units import RepairRefused, plan_file, write_file

HEADER = ["time_ist", "strike", "call_oi", "put_oi", "call_oi_near", "put_oi_near",
          "net_gex", "call_doi", "call_vol"]


def _write(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def _rows():
    return [
        # live capture: lots, has net_gex / doi / vol
        {"time_ist": "09:19", "strike": "64000.0", "call_oi": "22875.0", "put_oi": "15315.0",
         "call_oi_near": "22875.0", "put_oi_near": "15315.0", "net_gex": "-5.59e9",
         "call_doi": "0.0", "call_vol": "0.0"},
        # backfill: shares, blank net_gex / doi / vol
        {"time_ist": "09:20", "strike": "64000.0", "call_oi": "691260.0", "put_oi": "467640.0",
         "call_oi_near": "691260.0", "put_oi_near": "467640.0", "net_gex": "",
         "call_doi": "", "call_vol": ""},
    ]


def test_only_backfill_rows_are_divided_by_the_lot(tmp_path):
    p = tmp_path / "2026-11-26.csv"
    _write(p, _rows())
    header, rows, n = plan_file(p, 30)
    assert n == 1
    assert rows[0]["call_oi"] == "22875.0"                  # live row untouched
    assert float(rows[1]["call_oi"]) == 23042.0             # 691,260 / 30
    assert float(rows[1]["put_oi_near"]) == 15588.0         # 467,640 / 30
    bak = write_file(p, header, rows, "t")
    assert bak.exists()
    with open(p, newline="") as fh:
        got = list(csv.DictReader(fh))
    assert float(got[1]["call_oi"]) == 23042.0


def test_second_run_is_refused(tmp_path):
    p = tmp_path / "2026-11-26.csv"
    _write(p, _rows())
    header, rows, _ = plan_file(p, 30)
    write_file(p, header, rows, "t")
    with pytest.raises(RepairRefused):
        plan_file(p, 30)


def test_unknown_lot_is_refused(tmp_path):
    p = tmp_path / "2026-11-26.csv"
    _write(p, _rows())
    with pytest.raises(RepairRefused):
        plan_file(p, 1)
