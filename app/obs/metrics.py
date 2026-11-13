"""In-process HTTP counters plus a sliding latency ring for RPM / percentiles."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_count = 0
_by_class = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
_slow = 0
_last_duration_ms = 0.0
_in_flight = 0

# (ts, duration_ms, status, route) — capped by count and age
_RING_MAX = 2000
_RING_KEEP_S = 15 * 60.0
_ring: deque[tuple[float, float, int, str]] = deque(maxlen=_RING_MAX)

# Event-loop lag samples from the watchdog beat
_LAG_MAX = 600
_lag_ring: deque[tuple[float, float]] = deque(maxlen=_LAG_MAX)

# Thin egress miss gauges (process lifetime; windowed via ops_events separately)
_egress = {"news_miss": 0, "sectors_miss": 0, "nse_fallback": 0}
_gate_waits: dict[str, deque[float]] = {
    "chain": deque(maxlen=240),
    "quote": deque(maxlen=240),
}


def reset() -> None:
    global _count, _slow, _last_duration_ms, _in_flight
    with _lock:
        _count = 0
        _slow = 0
        _last_duration_ms = 0.0
        _in_flight = 0
        _ring.clear()
        _lag_ring.clear()
        for key in _by_class:
            _by_class[key] = 0
        for key in _egress:
            _egress[key] = 0
        for waits in _gate_waits.values():
            waits.clear()


def begin_request() -> None:
    global _in_flight
    with _lock:
        _in_flight += 1


def end_request() -> None:
    global _in_flight
    with _lock:
        _in_flight = max(0, _in_flight - 1)


def note(status: int, duration_ms: float, route: str | None = None) -> None:
    global _count, _slow, _last_duration_ms
    klass = f"{int(status) // 100}xx"
    if klass not in _by_class:
        klass = "other"
    path = (route or "").split("?", 1)[0] or "/"
    now = time.time()
    with _lock:
        _count += 1
        _by_class[klass] += 1
        _last_duration_ms = float(duration_ms)
        if duration_ms > 1000:
            _slow += 1
        _ring.append((now, float(duration_ms), int(status), path))
        _prune_ring_locked(now)


def note_loop_lag(lag_ms: float) -> None:
    now = time.time()
    with _lock:
        _lag_ring.append((now, max(0.0, float(lag_ms))))


def note_egress(kind: str) -> None:
    with _lock:
        if kind in _egress:
            _egress[kind] += 1


def note_gate_wait(bucket: str, wait_ms: float) -> None:
    with _lock:
        q = _gate_waits.get(bucket)
        if q is not None:
            q.append(max(0.0, float(wait_ms)))


def egress_snapshot() -> dict:
    with _lock:
        return dict(_egress)


def gate_wait_snapshot(window_s: float = 60.0) -> dict:
    # waits are not timestamped individually; report recent p95 of last N
    del window_s  # reserved for a future timestamped ring
    out = {}
    with _lock:
        for bucket, waits in _gate_waits.items():
            vals = list(waits)
            out[bucket] = {
                "samples": len(vals),
                "p95_ms": round(_percentile(vals, 0.95), 1) if vals else 0.0,
                "max_ms": round(max(vals), 1) if vals else 0.0,
            }
    return out


def _prune_ring_locked(now: float) -> None:
    cutoff = now - _RING_KEEP_S
    while _ring and _ring[0][0] < cutoff:
        _ring.popleft()
    lag_cut = now - 300.0
    while _lag_ring and _lag_ring[0][0] < lag_cut:
        _lag_ring.popleft()


def _percentile(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    i = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return s[i]


def _window_samples(now: float, window_s: float) -> list[tuple[float, float, int, str]]:
    cutoff = now - window_s
    return [row for row in _ring if row[0] >= cutoff]


def snapshot(window_s: float = 300.0) -> dict:
    now = time.time()
    with _lock:
        _prune_ring_locked(now)
        lifetime = {
            "count": _count,
            "by_class": dict(_by_class),
            "slow": _slow,
            "last_duration_ms": round(_last_duration_ms, 1),
            "in_flight": _in_flight,
        }
        samples = _window_samples(now, window_s)
        rpm_rows = _window_samples(now, 60.0)
        lag_rows = [(t, v) for t, v in _lag_ring if t >= now - window_s]
        in_flight = _in_flight

    durs = [d for _, d, _, _ in samples]
    statuses = [st for _, _, st, _ in samples]
    by_route: dict[str, list[float]] = defaultdict(list)
    route_status: dict[str, list[int]] = defaultdict(list)
    for _, d, st, route in samples:
        by_route[route].append(d)
        route_status[route].append(st)

    window_by_class = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
    for st in statuses:
        klass = f"{st // 100}xx"
        if klass not in window_by_class:
            klass = "other"
        window_by_class[klass] += 1

    err_rpm = sum(1 for _, _, st, _ in rpm_rows if st >= 400)
    top_by_count = sorted(by_route.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
    top_by_p95 = sorted(
        ((r, _percentile(vs, 0.95)) for r, vs in by_route.items() if vs),
        key=lambda kv: kv[1],
        reverse=True,
    )[:5]

    lag_vals = [v for _, v in lag_rows]
    return {
        **lifetime,
        "window_s": float(window_s),
        "window_count": len(samples),
        "window_by_class": window_by_class,
        "window_slow": sum(1 for d in durs if d > 1000),
        "rpm": len(rpm_rows),
        "error_rpm": err_rpm,
        "p50_ms": round(_percentile(durs, 0.50), 1) if durs else 0.0,
        "p95_ms": round(_percentile(durs, 0.95), 1) if durs else 0.0,
        "p99_ms": round(_percentile(durs, 0.99), 1) if durs else 0.0,
        "max_ms": round(max(durs), 1) if durs else 0.0,
        "top_routes": [
            {
                "route": route,
                "count": len(vs),
                "p50_ms": round(_percentile(vs, 0.50), 1),
                "p95_ms": round(_percentile(vs, 0.95), 1),
                "error_count": sum(1 for st in route_status[route] if st >= 400),
            }
            for route, vs in top_by_count
        ],
        "top_slow_routes": [
            {"route": route, "p95_ms": round(p95, 1), "count": len(by_route[route])}
            for route, p95 in top_by_p95
        ],
        "loop_lag_p50_ms": round(_percentile(lag_vals, 0.50), 1) if lag_vals else 0.0,
        "loop_lag_p95_ms": round(_percentile(lag_vals, 0.95), 1) if lag_vals else 0.0,
        "loop_lag_max_ms": round(max(lag_vals), 1) if lag_vals else 0.0,
        "in_flight": in_flight,
    }
