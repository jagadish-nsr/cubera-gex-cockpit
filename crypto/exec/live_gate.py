"""Fail-closed safety gate for Live Confirm on Delta India.

Live capital requires ALL of:
- feature flags ``exec.live`` + ``exec.confirm``
- explicit live API keys (never paper/demo keys)
- explicit ack env ``CRYPTO_LIVE_CONFIRM_ACK=I_UNDERSTAND_LIVE_RISK``
- production India host only (or an explicitly allowed override that still
  refuses testnet/demo hosts)

No Auto path. Missing any check → refuse.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from crypto.config import get_flag

# Exact string operators must set — intentional friction.
LIVE_ACK_VALUE = "I_UNDERSTAND_LIVE_RISK"
LIVE_ACK_ENV = "CRYPTO_LIVE_CONFIRM_ACK"

DELTA_INDIA_LIVE_REST = "https://api.india.delta.exchange"

_FORBIDDEN_LIVE_HOSTS = (
    "cdn-ind.testnet.deltaex.org",
    "testnet.delta.exchange",
    "testnet",
)

_ALLOWED_LIVE_HOSTS = (
    "api.india.delta.exchange",
)


@dataclass
class LiveGateResult:
    ok: bool
    code: str
    reasons: list[str] = field(default_factory=list)
    base_url: Optional[str] = None
    checks: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "reasons": list(self.reasons),
            "base_url": self.base_url,
            "checks": dict(self.checks),
            "ack_env": LIVE_ACK_ENV,
            "ack_required_value": LIVE_ACK_VALUE,
            "note": (
                "Live Confirm ready."
                if self.ok
                else "Live Confirm refused — fail closed until all safety checks pass."
            ),
        }


def live_credentials() -> tuple[Optional[str], Optional[str]]:
    key = (os.environ.get("DELTA_LIVE_API_KEY") or "").strip() or None
    secret = (os.environ.get("DELTA_LIVE_API_SECRET") or "").strip() or None
    return key, secret


def live_ack_ok() -> bool:
    return (os.environ.get(LIVE_ACK_ENV) or "").strip() == LIVE_ACK_VALUE


def live_base_url() -> str:
    """Resolve live REST base. Defaults to Delta India production."""
    raw = (os.environ.get("DELTA_LIVE_REST_URL") or DELTA_INDIA_LIVE_REST).strip().rstrip("/")
    return raw


def _host_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower()


def validate_live_base_url(url: str | None = None) -> tuple[bool, str, list[str]]:
    """Return (ok, normalized_url, reasons). Refuses testnet/demo hosts."""
    raw = (url or live_base_url()).strip().rstrip("/")
    host = _host_of(raw)
    reasons: list[str] = []
    if not host:
        return False, raw, ["DELTA_LIVE_REST_URL has no host"]
    for bad in _FORBIDDEN_LIVE_HOSTS:
        if bad in host or bad in raw.lower():
            reasons.append(f"refusing testnet/demo host {host!r} for live path")
            return False, raw, reasons
    if host not in _ALLOWED_LIVE_HOSTS:
        reasons.append(
            f"host {host!r} is not an allowed live host "
            f"(allowed: {', '.join(_ALLOWED_LIVE_HOSTS)}); "
            f"set DELTA_LIVE_REST_URL to {DELTA_INDIA_LIVE_REST}"
        )
        return False, raw, reasons
    return True, raw, []


def paper_keys_look_like_live_confusion() -> bool:
    """True when paper keys are set but live keys are missing — common mix-up."""
    paper_key = (os.environ.get("DELTA_PAPER_API_KEY") or "").strip()
    live_key, live_secret = live_credentials()
    return bool(paper_key) and not (live_key and live_secret)


def evaluate_live_gate(
    *,
    require_flags: bool = True,
) -> LiveGateResult:
    """Fail-closed evaluation of Live Confirm readiness."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    live_flag = get_flag("exec.live", default=False)
    confirm_flag = get_flag("exec.confirm", default=False)
    auto_flag = get_flag("exec.auto", default=False)

    checks["exec.live"] = live_flag
    checks["exec.confirm"] = confirm_flag
    checks["exec.auto_off"] = not auto_flag

    if require_flags:
        if not live_flag:
            reasons.append("exec.live is off")
        if not confirm_flag:
            reasons.append("exec.confirm is off — Advisory default")
    if auto_flag:
        reasons.append("exec.auto is on — Auto is not supported; refuse live")

    key, secret = live_credentials()
    checks["delta_live_api_key"] = bool(key)
    checks["delta_live_api_secret"] = bool(secret)
    if not key or not secret:
        reasons.append(
            "DELTA_LIVE_API_KEY and DELTA_LIVE_API_SECRET required "
            "(separate from DELTA_PAPER_* demo keys)"
        )
        if paper_keys_look_like_live_confusion():
            reasons.append(
                "DELTA_PAPER_* is set but DELTA_LIVE_* is missing — "
                "paper keys cannot unlock live Confirm"
            )

    ack = live_ack_ok()
    checks["live_ack"] = ack
    if not ack:
        reasons.append(
            f"set {LIVE_ACK_ENV}={LIVE_ACK_VALUE} to explicitly acknowledge live risk"
        )

    # IP whitelist is operator-side on Delta; require an explicit note ack.
    ip_ack = (os.environ.get("DELTA_LIVE_IP_WHITELIST_ACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    checks["ip_whitelist_ack"] = ip_ack
    if not ip_ack:
        reasons.append(
            "set DELTA_LIVE_IP_WHITELIST_ACK=1 after whitelisting this host's "
            "static IP on Delta India trading keys"
        )

    url_ok, base_url, url_reasons = validate_live_base_url()
    checks["live_base_url"] = url_ok
    reasons.extend(url_reasons)

    ok = (
        (not require_flags or (live_flag and confirm_flag))
        and not auto_flag
        and bool(key and secret)
        and ack
        and ip_ack
        and url_ok
    )
    code = "ok" if ok else "live_gate_failed"
    return LiveGateResult(
        ok=ok,
        code=code,
        reasons=reasons,
        base_url=base_url if url_ok else base_url,
        checks=checks,
    )
