"""commentary/story_state.py — C3 Story State & Narrative Continuity Manager."""

from __future__ import annotations

from datetime import datetime, time, timezone, timedelta
from typing import Any

from commentary.config_loader import load_commentary_config
from commentary.models import CommentaryEntry, Event, StoryState
try:
    from commentary.models import to_ist
except Exception:
    _IST = timezone(timedelta(hours=5, minutes=30))
    def to_ist(ts: Any) -> datetime:
        if ts is None: return datetime.now(_IST)
        if isinstance(ts, str):
            try: dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception: return datetime.now(_IST)
        else: dt = ts
        if dt.tzinfo is not None: return dt.astimezone(_IST)
        if dt.hour < 7: return dt.replace(tzinfo=timezone.utc).astimezone(_IST)
        return dt.replace(tzinfo=_IST)


class StoryStateManager:
    """Manages session phase transitions, running thesis updates, and open narrative threads."""

    def __init__(self):
        cfg = load_commentary_config()
        self.phases_cfg = cfg.get("session", {}).get("phases", [])
        self.state = StoryState(phase="pre_open")

    def determine_phase(self, ts: datetime) -> str:
        """Map datetime to configured intraday trading session phase."""
        from commentary.models import to_ist
        ts_ist = to_ist(ts)
        t_str = ts_ist.strftime("%H:%M")
        for p in self.phases_cfg:
            start = p.get("start", "09:00")
            end = p.get("end", "15:30")
            if start <= t_str < end:
                return p.get("name", "morning")
        if t_str >= "15:00":
            return "closing_30m"
        return "pre_open"

    def update_phase(self, ts: datetime) -> str:
        """Update current story phase."""
        from commentary.models import to_ist
        ts_ist = to_ist(ts)
        phase = self.determine_phase(ts_ist)
        self.state.phase = phase
        return phase

    def update_thesis(self, event: Event) -> None:
        """Update running market thesis based on regime flips, wall breaks, or directional events."""
        curr_thesis = self.state.running_thesis
        old_bias = curr_thesis.get("bias", "unclear")
        new_bias = old_bias
        new_conf = curr_thesis.get("confidence", 0.5)

        if event.type in ("WALL_BREAK", "ORB_BREAK_UP") and event.facts.get("wall_type") == "CALL_WALL":
            new_bias = "bullish"
            new_conf = 0.75
        elif event.type in ("WALL_BREAK", "ORB_BREAK_DOWN") and event.facts.get("wall_type") == "PUT_WALL":
            new_bias = "bearish"
            new_conf = 0.75
        elif event.type == "REGIME_CHANGE":
            if "POSITIVE" in str(event.facts.get("new_regime")):
                new_bias = "range"
                new_conf = 0.65
            else:
                new_bias = "breakout"
                new_conf = 0.70
        elif event.type == "MAX_PAIN_PINNING":
            new_bias = "range"
            new_conf = 0.80

        if new_bias != old_bias:
            self.state.running_thesis = {
                "bias": new_bias,
                "confidence": new_conf,
                "since_ts": event.ts,
                "driver_event": event.type,
            }

    def open_thread(self, thread_id: str, topic: str, opened_ts: datetime, details: dict[str, Any]) -> None:
        """Register an unresolved narrative thread (e.g. wall under test, unfilled gap)."""
        # Avoid duplicate thread IDs
        self.state.open_threads = [t for t in self.state.open_threads if t.get("thread_id") != thread_id]
        self.state.open_threads.append({
            "thread_id": thread_id,
            "topic": topic,
            "opened_ts": opened_ts,
            "details": details,
        })

    def close_thread(self, thread_id: str) -> None:
        """Resolve and close a narrative thread."""
        self.state.close_thread(thread_id)

    def record_entry(self, entry: CommentaryEntry) -> None:
        """Append published entry to story state."""
        self.state.entries.append(entry)

    def get_narrative_context(self, max_past_entries: int = 3) -> dict[str, Any]:
        """Compile running narrative context for the Narrator."""
        recent = self.state.entries[-max_past_entries:]
        return {
            "phase": self.state.phase,
            "thesis": self.state.running_thesis,
            "open_threads": list(self.state.open_threads),
            "recent_headlines": [e.headline for e in recent],
        }
