"""Extrapolation engine — trend analysis, plateau detection, performance regression."""

from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class Extrapolator:
    """Analyzes telemetry to detect trends, plateaus, and anomalies.

    Input: telemetry JSONL file with event snapshots.
    Output: trend reports, plateau flags, budget recommendations.
    """

    def __init__(self, telemetry_path: str | Path, window: int = 10) -> None:
        self._path = Path(telemetry_path)
        self._window = window

    def analyze_trends(self) -> dict[str, Any]:
        """Analyze recent telemetry and return trend indicators."""
        snapshots = self._load_resource_snapshots()
        if len(snapshots) < 3:
            return {"plateau_detected": False, "message": "Insufficient data"}

        result: dict[str, Any] = {}

        # Memory trend
        mem_values = [s.get("memory_gb", 0) for s in snapshots[-self._window:]]
        if len(mem_values) >= 5:
            slope = self._linear_slope(mem_values)
            result["memory_gb"] = {
                "current": round(mem_values[-1], 2),
                "slope": round(slope, 3),
                "trend": "increasing" if slope > 0.01 else "stable" if abs(slope) <= 0.01 else "decreasing",
            }
        else:
            result["memory_gb"] = {"current": round(mem_values[-1], 2) if mem_values else 0, "slope": 0}

        # CPU trend
        cpu_values = [s.get("cpu_percent", 0) for s in snapshots[-self._window:]]
        result["cpu_percent"] = {
            "current": round(cpu_values[-1], 1) if cpu_values else 0,
            "average": round(statistics.mean(cpu_values), 1) if cpu_values else 0,
        }

        # Disk trend
        disk_values = [s.get("disk_used_percent", 0) for s in snapshots[-self._window:]]
        if len(disk_values) >= 5:
            disk_slope = self._linear_slope(disk_values)
            result["disk_used_percent"] = {
                "current": round(disk_values[-1], 1),
                "slope": round(disk_slope, 3),
            }
        else:
            result["disk_used_percent"] = {"current": round(disk_values[-1], 1) if disk_values else 0}

        # Plateau detection
        result["plateau_detected"] = self._detect_plateau()
        if result["plateau_detected"]:
            logger.info("Plateau detected in improvement metrics")

        return result

    def improvement_velocity(self) -> dict[str, float]:
        """Calculate improvement velocity across sessions."""
        events = self._load_events_by_type("l2_session")
        if len(events) < 2:
            return {"rate": 0.0, "avg_duration_s": 0.0, "acceptance_rate": 0.0}

        durations = []
        accepted = 0
        for ev in events:
            data = ev.get("data", {})
            dur = data.get("duration_ms", 0)
            durations.append(dur)
            if data.get("success"):
                accepted += 1

        return {
            "rate": round(len(events) / max(events[-1]["t"] - events[0]["t"], 1) * 3600, 2),  # sessions per hour
            "avg_duration_s": round(statistics.mean(durations) / 1000, 1) if durations else 0,
            "acceptance_rate": round(accepted / len(events), 2),
        }

    def _detect_plateau(self) -> bool:
        """Check if improvement metrics have plateaued."""
        snapshots = self._load_resource_snapshots()
        if len(snapshots) < self._window:
            return False

        recent = snapshots[-self._window:]
        # If memory and disk are stable but CPU is low → idle plateau
        cpu_vals = [s.get("cpu_percent", 0) for s in recent]
        mem_vals = [s.get("memory_gb", 0) for s in recent]

        cpu_stable = max(cpu_vals) - min(cpu_vals) < 5.0
        mem_stable = max(mem_vals) - min(mem_vals) < 0.1
        mem_high = statistics.mean(mem_vals) > 2.0

        return cpu_stable and mem_stable and mem_high

    def budget_recommendation(self) -> dict[str, Any]:
        """Recommend optimal L2 iteration budget based on past eval curves."""
        events = self._load_events_by_type("evaluation")
        if len(events) < 5:
            return {"recommended_attempts": 5, "confidence": "low"}

        scores = [e.get("data", {}).get("score", 0) for e in events if e.get("data")]
        if not scores:
            return {"recommended_attempts": 5, "confidence": "low"}

        # Find the attempt number where score typically plateaus
        # If average score is high, fewer attempts needed
        avg_score = statistics.mean(scores)
        if avg_score > 0.8:
            return {"recommended_attempts": 3, "confidence": "high"}
        elif avg_score > 0.6:
            return {"recommended_attempts": 4, "confidence": "medium"}
        else:
            return {"recommended_attempts": 5, "confidence": "medium"}

    def _load_resource_snapshots(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        events = self._load_events_by_type("resource_snapshot")
        return [e.get("data", {}) for e in events if e.get("data")]

    def _load_events_by_type(self, event_type: str) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        events = []
        try:
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        if ev.get("type") == event_type:
                            events.append(ev)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning("Failed to load telemetry: %s", exc)
        return events

    @staticmethod
    def _linear_slope(values: list[float]) -> float:
        """Compute linear regression slope over a list of values."""
        x = np.arange(len(values))
        y = np.array(values, dtype=float)
        if np.std(x) == 0:
            return 0.0
        coeffs = np.polyfit(x, y, 1)
        return float(coeffs[0])
