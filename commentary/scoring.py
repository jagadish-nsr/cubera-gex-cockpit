"""commentary/scoring.py — C2 Significance Scoring, Confluence Merging & Budget Gatekeeper."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from commentary.config_loader import get_budget_config, load_commentary_config
from commentary.models import Event, ScoredEvent, StoryState


class SignificanceScorer:
    """Computes event significance, merges confluent developments, and enforces daily budgets."""

    def __init__(self):
        cfg = load_commentary_config()
        sig_cfg = cfg.get("significance", {})
        self.publish_threshold = float(sig_cfg.get("publish_threshold", 0.60))
        self.time_multipliers = sig_cfg.get("time_of_day_multipliers", {})
        self.confluence_window_min = float(sig_cfg.get("confluence_window_minutes", 6))

        b_cfg = get_budget_config()
        self.max_entries_per_day = int(b_cfg.get("max_entries_per_day", 15))
        self.min_gap_minutes = float(b_cfg.get("min_gap_minutes", 8))

        self._published_count: int = 0
        self._last_published_ts: datetime | None = None
        self._recent_events: list[Event] = []
        self._suppressed_events: list[tuple[Event, str]] = []

    def score_event(self, event: Event, state: StoryState) -> ScoredEvent:
        """Calculate significance score (0.0 to 1.0) with mathematical components."""
        reasons: list[str] = []

        # 1. Magnitude component (saturating curve)
        # f(x) = 1.0 - exp(-x / 1.2)
        mag_score = 1.0 - math.exp(-max(0.1, event.magnitude_sigma) / 1.2)
        reasons.append(f"Magnitude ({event.magnitude_sigma:.2f}σ): {mag_score:.2f}")

        # 2. Time-of-day weighting
        phase = state.phase
        time_mult = float(self.time_multipliers.get(phase, 1.0))
        reasons.append(f"Phase ({phase}) weight: {time_mult:.2f}x")

        # 3. Quality flag penalty
        quality_mult = 1.0
        if event.quality_flags:
            quality_mult = max(0.5, 1.0 - (0.15 * len(event.quality_flags)))
            reasons.append(f"Quality penalty ({len(event.quality_flags)} flags): {quality_mult:.2f}x")

        # 4. Novelty calculation (decay if identical event fired recently)
        novelty_mult = 1.0
        for past_evt in self._recent_events[-5:]:
            if past_evt.type == event.type:
                dt_m = (event.ts - past_evt.ts).total_seconds() / 60.0
                if dt_m < 30.0:
                    novelty_mult *= 0.75
                    reasons.append(f"Recent repetition decay: {novelty_mult:.2f}x")
                    break

        # Compute raw significance
        raw_score = mag_score * time_mult * quality_mult * novelty_mult
        final_score = min(1.0, max(0.0, raw_score))

        return ScoredEvent(
            event=event,
            significance=round(final_score, 3),
            reasons=reasons,
        )

    def merge_confluent_events(self, scored_events: list[ScoredEvent]) -> list[ScoredEvent]:
        """Merge events occurring within confluence window sharing context into a single compound event."""
        if len(scored_events) <= 1:
            return scored_events

        merged: list[ScoredEvent] = []
        skip_indices: set[int] = set()

        for i, s1 in enumerate(scored_events):
            if i in skip_indices:
                continue

            confluent_ids: list[str] = []
            combined_reasons = list(s1.reasons)
            max_sig = s1.significance

            for j in range(i + 1, len(scored_events)):
                if j in skip_indices:
                    continue
                s2 = scored_events[j]
                dt_m = abs((s1.event.ts - s2.event.ts).total_seconds()) / 60.0

                if dt_m <= self.confluence_window_min:
                    # Confluence found! (e.g. Wall Test + News or Impulse + Buildup)
                    confluent_ids.append(s2.event.id)
                    combined_reasons.append(f"Confluence with {s2.event.type} (+0.12 boost)")
                    max_sig = min(1.0, max_sig + 0.12)
                    skip_indices.add(j)

            merged.append(
                ScoredEvent(
                    event=s1.event,
                    significance=round(max_sig, 3),
                    reasons=combined_reasons,
                    merged_event_ids=confluent_ids,
                )
            )

        return merged

    def should_publish(self, scored: ScoredEvent) -> tuple[bool, str]:
        """Apply daily budget, publication threshold, and inter-entry minimum gap."""
        # Dynamic threshold based on budget remaining
        remaining_budget = max(0, self.max_entries_per_day - self._published_count)
        dynamic_thresh = self.publish_threshold
        if remaining_budget <= 3:
            # Raise threshold when close to daily budget cap
            dynamic_thresh += 0.15

        if scored.significance < dynamic_thresh:
            reason = f"Significance {scored.significance:.2f} below threshold {dynamic_thresh:.2f}"
            self._suppressed_events.append((scored.event, reason))
            return False, reason

        # Check gap between published entries (unless extreme breakthrough > 0.88)
        if self._last_published_ts is not None and scored.significance < 0.88:
            gap_m = (scored.event.ts - self._last_published_ts).total_seconds() / 60.0
            if gap_m < self.min_gap_minutes:
                reason = f"Paced: only {gap_m:.1f}m since last entry (min gap {self.min_gap_minutes}m)"
                self._suppressed_events.append((scored.event, reason))
                return False, reason

        # Passed all gatekeeper rules!
        self._published_count += 1
        self._last_published_ts = scored.event.ts
        self._recent_events.append(scored.event)
        return True, "Passed publication gate"

    @property
    def suppressed_events(self) -> list[tuple[Event, str]]:
        return list(self._suppressed_events)
