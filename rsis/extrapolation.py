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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rsis.config import CONFIG

logger = logging.getLogger(__name__)


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
        logger.info("Loaded %d telemetry events from %s", len(events), self.telemetry_dir)
        return events

    # ── Session analysis ─────────────────────────────────────────────

    def get_sessions(self) -> list[dict]:
        """Group telemetry events into sessions."""
        events = self.load_events()
        sessions: dict[str, list[dict]] = defaultdict(list)

        for ev in events:
            session_id = ev.get("metadata", {}).get("session_id") or \
                         ev.get("session_id", "unknown")
            sessions[session_id].append(ev)

        result = []
        for sid, evts in sessions.items():
            # Determine session type from events
            l2_starts = [e for e in evts if e.get("type") == "l2_start"]
            l3_starts = [e for e in evts if e.get("type") == "l3_start"]

            if l3_starts:
                session_type = "L3"
            elif l2_starts:
                session_type = "L2"
            else:
                session_type = "L1"

            result.append({
                "session_id": sid,
                "type": session_type,
                "events": evts,
                "event_count": len(evts),
                "timestamp": evts[0].get("timestamp", ""),
            })

        result.sort(key=lambda s: s["timestamp"])
        return result

    # ── L2 budget prediction ────────────────────────────────────────

    def predict_optimal_iterations(self) -> int:
        """Predict optimal L2 iteration budget based on past eval curves."""
        events = self.load_events()
        eval_events = [e for e in events if e.get("type") == "l2_evaluation"]

        if not eval_events:
            return CONFIG.l2.max_improvement_attempts

        # Analyse: at what attempt number did evaluations typically pass?
        pass_attempts = []
        for ev in eval_events:
            if ev.get("metadata", {}).get("decision") == "PASS":
                attempt = ev.get("metadata", {}).get("attempt", 0)
                if attempt > 0:
                    pass_attempts.append(attempt)

        if not pass_attempts:
            # If nothing passed, recommend more attempts
            return min(CONFIG.l2.max_improvement_attempts + 2, 10)

        # Use median + 1 as recommended budget
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
        # Group by file or goal
        by_context: dict[str, list[float]] = defaultdict(list)
        for ev in eval_events:
            ctx = ev.get("metadata", {}).get("goal", "default")
            score = ev.get("metadata", {}).get("score_avg", 0.5)
            by_context[ctx].append(score)

        for ctx, scores in by_context.items():
            if len(scores) < 3:
                continue
            # Linear regression slope approximation
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

        # Group by target files
        by_file: dict[str, list[dict]] = defaultdict(list)
        for imp in improvements:
            for f in imp.get("target_files", []):
                by_file[f].append(imp)

        # Files with many improvements might have redundant ones
        for fpath, imps in by_file.items():
            if len(imps) > 3:
                # Check for similar descriptions
                descriptions = [i.get("description", "") for i in imps]
                # Simple similarity: shared words
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

        # Deduplicate
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
            successes += sum(1 for e in evals if e.get("metadata", {}).get("decision") == "PASS")
            total_attempts += len(evals)

        return {
            "total_sessions": len(sessions),
            "total_improvements": len(improvements),
            "success_rate": round(successes / max(total_attempts, 1), 3),
            "avg_attempts": round(total_attempts / max(len(improvements), 1), 1),
            "trends": self.detect_regression_trends(),
        }
