"""Telemetry extrapolation engine.

Analyses telemetry across sessions to:
- Predict optimal L2 iteration budget based on past eval curves
- Detect performance regression trends before they hit thresholds
- Suggest which code areas need redundancy refinement
- Generate cross-session improvement velocity reports
"""

import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from rsis.config import CONFIG

logger = logging.getLogger(__name__)


def _get(ev: dict, *keys: str, default: Any = None) -> Any:
    """Get a possibly nested key from an event dict.

    Telemetry metadata is spread at the root level (via **metadata unpacking
    in TelemetryEvent.to_dict()), so we access directly.
    """
    for key in keys:
        if key in ev:
            return ev[key]
    return default


class TelemetryExtrapolator:
    """Analyses historical telemetry to derive insights and predictions."""

    def __init__(self, telemetry_dir: Optional[str] = None):
        self.telemetry_dir = Path(telemetry_dir or CONFIG.telemetry_dir)
        self._cache: Optional[list[dict]] = None

    # ── Data loading ────────────────────────────────────────────────

    def load_events(self, force: bool = False) -> list[dict]:
        """Load all telemetry events from JSONL files."""
        if self._cache is not None and not force:
            return self._cache

        events = []
        if not self.telemetry_dir.exists():
            return events

        for fpath in sorted(self.telemetry_dir.glob("*.jsonl")):
            try:
                for line in fpath.read_text().strip().splitlines():
                    if line.strip():
                        events.append(json.loads(line))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to parse %s: %s", fpath.name, e)

        self._cache = events
        logger.info("Loaded %d telemetry events", len(events))
        return events

    # ── Session analysis ─────────────────────────────────────────────

    def get_sessions(self) -> list[dict]:
        """Group telemetry events into sessions."""
        events = self.load_events()

        # Session IDs are embedded in event metadata (telemetry uses
        # session-level IDs stored in the filename). We reconstruct
        # by proximity — events close in time belong to the same session.
        sessions: list[dict] = []
        current: list[dict] = []

        for ev in events:
            etype = ev.get("type", "")
            if etype in ("l2_start", "l3_start") and current:
                sessions.append(self._build_session(current))
                current = []
            current.append(ev)

        if current:
            sessions.append(self._build_session(current))

        return sessions

    def _build_session(self, events: list[dict]) -> dict:
        """Build a session summary from a list of events."""
        if not events:
            return {"session_id": "unknown", "type": "?", "events": [], "event_count": 0}

        etype = "L1"
        for ev in events:
            t = ev.get("type", "")
            if t.startswith("l3_"):
                etype = "L3"
            elif t.startswith("l2_"):
                etype = "L2"

        return {
            "session_id": f"session-{events[0].get('timestamp', '?')[:19]}",
            "type": etype,
            "events": events,
            "event_count": len(events),
            "timestamp": events[0].get("timestamp", ""),
        }

    # ── L2 budget prediction ────────────────────────────────────────

    def predict_optimal_iterations(self) -> int:
        """Predict optimal L2 iteration budget based on past eval curves."""
        events = self.load_events()
        eval_events = [e for e in events if e.get("type") == "l2_evaluation"]

        if not eval_events:
            return CONFIG.l2.max_improvement_attempts

        pass_attempts = []
        for ev in eval_events:
            if _get(ev, "decision") == "PASS":
                attempt = _get(ev, "attempt", default=0)
                if attempt and attempt > 0:
                    pass_attempts.append(attempt)

        if not pass_attempts:
            return min(CONFIG.l2.max_improvement_attempts + 2, 10)

        median_attempt = int(statistics.median(pass_attempts))
        return max(median_attempt + 1, 3)

    # ── Regression detection ────────────────────────────────────────

    def detect_regression_trends(self) -> list[dict]:
        """Detect performance regression trends across sessions."""
        events = self.load_events()
        eval_events = [e for e in events if e.get("type") == "l2_evaluation"]

        if len(eval_events) < 3:
            return []

        trends = []
        by_context: dict[str, list[float]] = defaultdict(list)
        for ev in eval_events:
            ctx = _get(ev, "goal", "description", default="default")
            score = _get(ev, "score_avg", default=0.5)
            if isinstance(score, (int, float)):
                by_context[ctx].append(float(score))

        for ctx, scores in by_context.items():
            if len(scores) < 3:
                continue
            n = len(scores)
            xs = list(range(n))
            mean_x = statistics.mean(xs)
            mean_y = statistics.mean(scores)
            slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, scores)) / \
                    max(sum((x - mean_x) ** 2 for x in xs), 0.001)

            if slope < -0.05:
                trends.append({
                    "context": ctx,
                    "slope": round(slope, 4),
                    "current_score": round(scores[-1], 3),
                    "trend": "regression",
                    "severity": "high" if slope < -0.15 else "medium",
                })
            elif slope > 0.05:
                trends.append({
                    "context": ctx,
                    "slope": round(slope, 4),
                    "current_score": round(scores[-1], 3),
                    "trend": "improving",
                    "severity": "positive",
                })

        return trends

    # ── Redundancy candidates ───────────────────────────────────────

    def find_redundancy_candidates(self, memory_kg) -> list[dict]:
        """Identify potential redundancy based on KG patterns."""
        candidates = []
        improvements = memory_kg.query(node_type="improvement")

        by_file: dict[str, list[dict]] = defaultdict(list)
        for imp in improvements:
            for f in imp.get("target_files", []):
                by_file[f].append(imp)

        for fpath, imps in by_file.items():
            if len(imps) > 3:
                descriptions = [i.get("description", "") for i in imps]
                for i in range(len(descriptions)):
                    for j in range(i + 1, len(descriptions)):
                        words_i = set(descriptions[i].lower().split())
                        words_j = set(descriptions[j].lower().split())
                        if len(words_i) > 0 and len(words_j) > 0:
                            overlap = len(words_i & words_j) / max(len(words_i | words_j), 1)
                            if overlap > 0.5:
                                candidates.append({
                                    "file": fpath,
                                    "improvement_ids": [imps[i].get("id"), imps[j].get("id")],
                                    "similarity": round(overlap, 2),
                                    "descriptions": [descriptions[i], descriptions[j]],
                                })

        seen = set()
        unique = []
        for c in candidates:
            key = frozenset(c["improvement_ids"])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique

    # ── Reports ─────────────────────────────────────────────────────

    def generate_velocity_report(self) -> dict:
        """Generate a cross-session improvement velocity report."""
        sessions = self.get_sessions()
        improvements = [s for s in sessions if s["type"] == "L2"]

        if not improvements:
            return {
                "total_sessions": 0,
                "total_improvements": 0,
                "success_rate": 0.0,
                "avg_attempts": 0.0,
            }

        successes = 0
        total_attempts = 0
        for s in improvements:
            evals = [e for e in s["events"] if e.get("type") == "l2_evaluation"]
            successes += sum(1 for e in evals if _get(e, "decision") == "PASS")
            total_attempts += len(evals)

        return {
            "total_sessions": len(sessions),
            "total_improvements": len(improvements),
            "success_rate": round(successes / max(total_attempts, 1), 3),
            "avg_attempts": round(total_attempts / max(len(improvements), 1), 1),
            "trends": self.detect_regression_trends(),
        }
