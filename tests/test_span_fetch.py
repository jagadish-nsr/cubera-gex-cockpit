"""SPAN fetch: validation, retries, holiday skip and pruning, all offline."""

import io
import zipfile
from datetime import date

import pytest

from analytics.span_fetch import SpanNotPublished, fetch_span, prune, span_filename

_RA = "".join(f"<a>{v}</a>" for v in [
    10.00, 10.00, -781.09, -781.09, 801.09, 801.09, -1572.18, -1572.18,
    1592.18, 1592.18, -2363.27, -2363.27, 2383.27, 2383.27, -1657.79, 1664.79,
])
DAY = date(2026, 11, 23)  # a Monday that trades


def _span_text(created="202611231709", code="NIFTY"):
    return (
        f"<spanFile><fileFormat>4.00</fileFormat><created>{created}</created>"
        f"<futPf><pfCode>{code}</pfCode><fut><pe>20261229</pe><p>25504.70</p>"
        "<cvf>1.00</cvf><setlDate>20261229</setlDate>"
        f"<ra>{_RA}<d>1</d></ra></fut></futPf></spanFile>"
    )


def _zip(text, member=None, day=DAY):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member or span_filename(day), text)
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self.content = content


def _getter(*responses):
    calls = []
    queue = list(responses)

    def get(url, headers=None, timeout=None):
        calls.append(url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    get.calls = calls
    return get


def _no_sleep(_seconds):
    return None


def test_fetch_writes_a_validated_file(tmp_path):
    get = _getter(_Resp(200, _zip(_span_text())))
    assert fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep) == "fetched"
    assert (tmp_path / "nsccl.20261123.s.spn").read_text().startswith("<spanFile>")
    assert get.calls == ["https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.20261123.s.zip"]
    assert not list(tmp_path.glob("*.tmp"))


def test_an_existing_valid_file_is_not_downloaded_again(tmp_path):
    (tmp_path / "nsccl.20261123.s.spn").write_text(_span_text())
    get = _getter()  # any call would pop from an empty queue
    assert fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep) == "present"
    assert get.calls == []


def test_a_corrupt_local_copy_is_replaced(tmp_path):
    (tmp_path / "nsccl.20261123.s.spn").write_text("<spanFile>truncated")
    get = _getter(_Resp(200, _zip(_span_text())))
    assert fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep) == "fetched"


def test_holiday_makes_no_request(tmp_path):
    get = _getter()
    assert fetch_span(date(2026, 11, 24), tmp_path, getter=get, sleep=_no_sleep) == "holiday"
    assert get.calls == []
    assert not list(tmp_path.glob("*.spn"))


def test_404_means_not_published_yet(tmp_path):
    get = _getter(_Resp(404, b"<html>"))
    with pytest.raises(SpanNotPublished):
        fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep)
    assert not list(tmp_path.glob("*.spn"))


def test_network_errors_retry_then_succeed(tmp_path):
    get = _getter(ConnectionError("reset"), _Resp(503), _Resp(200, _zip(_span_text())))
    assert fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep) == "fetched"
    assert len(get.calls) == 3


def test_persistent_failure_raises_and_writes_nothing(tmp_path):
    get = _getter(ConnectionError("x"), ConnectionError("y"), ConnectionError("z"))
    with pytest.raises(RuntimeError, match="download failed"):
        fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep)
    assert not list(tmp_path.glob("*.spn*"))


def test_a_file_dated_for_another_day_is_rejected(tmp_path):
    # The zip is named for DAY but its own <created> date is a day earlier.
    get = _getter(_Resp(200, _zip(_span_text(created="202611201709"))))
    with pytest.raises(ValueError, match="dated 2026-11-20"):
        fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep)
    assert not list(tmp_path.glob("*.spn"))


def test_a_zip_without_the_expected_member_is_rejected(tmp_path):
    get = _getter(_Resp(200, _zip(_span_text(), member="other.spn")))
    with pytest.raises(ValueError, match="zip has no nsccl.20261123.s.spn"):
        fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep)


def test_a_file_without_nifty_futures_is_rejected(tmp_path):
    get = _getter(_Resp(200, _zip(_span_text(code="BANKNIFTY"))))
    with pytest.raises(ValueError, match="no NIFTY futures"):
        fetch_span(DAY, tmp_path, getter=get, sleep=_no_sleep)


def test_prune_keeps_only_the_newest_files(tmp_path):
    for ymd in ("20261116", "20261117", "20261118", "20261119", "20261120"):
        (tmp_path / f"nsccl.{ymd}.s.spn").write_text("x")
    removed = prune(tmp_path, keep=3)
    assert removed == ["nsccl.20261116.s.spn", "nsccl.20261117.s.spn"]
    assert sorted(p.name for p in tmp_path.glob("*.spn")) == [
        "nsccl.20261118.s.spn", "nsccl.20261119.s.spn", "nsccl.20261120.s.spn",
    ]
