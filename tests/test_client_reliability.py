"""Pillar F — client ReliabilityController wiring (static fixture, no browser)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COCKPIT_JS = ROOT / "app" / "static" / "js" / "cockpit.js"
INDEX_HTML = ROOT / "app" / "static" / "index.html"

# Audio-only empty catches are allowlisted. Money-adjacent prefs must use notePrefCatch.
_AUDIO_ALLOW_MARKERS = (
    "audioCtx.resume()",
    "ctx.resume()",
    "playSentinel",
    "startGammaBlast",
    "requestFullscreen",
)


def test_reliability_controller_modes_present():
    js = COCKPIT_JS.read_text(encoding="utf-8")
    assert "ReliabilityController" in js
    for mode in ("OK", "DEGRADED", "NOT_READY", "OFF_SESSION"):
        assert f"{mode}:" in js or f'"{mode}"' in js
    assert "stale_while_revalidate" in js
    assert "rememberBook" in js
    assert "scheduleWsReconnect" in js
    assert "TELEMETRY_WARN_S" in js
    assert "TELEMETRY_CRIT_S" in js
    assert "notePrefCatch" in js


def test_telemetry_chip_in_html():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="telemetry-stale-chip"' in html
    assert 'id="reliability-banner"' in html


def test_go_prefs_not_silent_catch():
    js = COCKPIT_JS.read_text(encoding="utf-8")
    # Every /api/go/prefs POST must route failures through notePrefCatch.
    assert js.count('fetch("/api/go/prefs"') >= 1
    assert 'notePrefCatch("GO prefs"' in js
    # No bare empty catch immediately after prefs in the committed tree.
    # Scan windows around prefs fetches.
    idx = 0
    while True:
        i = js.find('fetch("/api/go/prefs"', idx)
        if i < 0:
            break
        window = js[i : i + 350]
        assert "notePrefCatch" in window, "prefs fetch missing notePrefCatch in nearby window"
        assert ".catch(() => {})" not in window
        idx = i + 20


def test_empty_catch_allowlist_audio_only():
    """Remaining `.catch(() => {})` must sit near audio/fullscreen allowlist markers."""
    js = COCKPIT_JS.read_text(encoding="utf-8")
    lines = js.splitlines()
    offenders = []
    for n, line in enumerate(lines, 1):
        if ".catch(() => {})" not in line and ".catch(() => { })" not in line:
            continue
        window = "\n".join(lines[max(0, n - 8) : n])
        if any(m in window for m in _AUDIO_ALLOW_MARKERS):
            continue
        # json().catch(() => ({})) is a parse fallback, not fire-and-forget prefs.
        if "json().catch(() => ({}))" in line or "json().catch(() => ({" in line:
            continue
        offenders.append(f"L{n}: {line.strip()}")
    assert offenders == [], "non-allowlisted empty catches:\n" + "\n".join(offenders)
