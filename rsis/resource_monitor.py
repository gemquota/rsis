"""Resource limit enforcement for RSIS.

Monitors disk, memory, CPU, and API call rates, and takes configured
actions when limits are exceeded — halting loops, triggering refinement,
or throttling frequency.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from rsis.config import CONFIG, ResourceLimits
from rsis.telemetry import WorkspaceMonitor

logger = logging.getLogger(__name__)


class ResourceSeverity(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class ResourceAlert:
    """An alert triggered when a resource limit is exceeded."""

    def __init__(self, resource: str, value: float, limit: float,
                 severity: ResourceSeverity, action_taken: str = ""):
        self.resource = resource
        self.value = value
        self.limit = limit
        self.severity = severity
        self.action_taken = action_taken
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def __repr__(self) -> str:
        return (f"[{self.severity.value.upper()}] {self.resource}: "
                f"{self.value:.1f} exceeds {self.limit:.1f} — {self.action_taken}")


class ResourceEnforcer:
    """Active resource monitor and enforcement.

    Runs a background thread that periodically checks resource usage
    and triggers callbacks when limits are exceeded.
    """

    def __init__(self, limits: Optional[ResourceLimits] = None):
        self.limits = limits or CONFIG.resources
        self.monitor = WorkspaceMonitor()
        self._alerts: list[ResourceAlert] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._check_interval_s = 10
        self._halt_requested = False

        # Callbacks for escalation
        self._on_halt: Optional[Callable[[str], None]] = None
        self._on_throttle: Optional[Callable[[str], None]] = None
        self._on_warn: Optional[Callable[[str], None]] = None

        # API call rate tracking
        self._api_call_times: deque[float] = deque(maxlen=200)

    # ── Configuration ──────────────────────────────────────────────

    def set_callbacks(
        self,
        on_halt: Optional[Callable[[str], None]] = None,
        on_throttle: Optional[Callable[[str], None]] = None,
        on_warn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_halt = on_halt
        self._on_throttle = on_throttle
        self._on_warn = on_warn

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._halt_requested = False
        self._thread = threading.Thread(
            target=self._check_loop, daemon=True,
            name="resource-enforcer",
        )
        self._thread.start()
        logger.info("Resource enforcer started (interval=%ds)", self._check_interval_s)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def halt_requested(self) -> bool:
        return self._halt_requested

    @property
    def alerts(self) -> list[ResourceAlert]:
        with self._lock:
            return list(self._alerts)

    # ── API call tracking ──────────────────────────────────────────

    def record_api_call(self) -> None:
        """Record an evaluator API call for rate limiting."""
        self._api_call_times.append(time.monotonic())

    def api_calls_per_minute(self) -> int:
        """Calculate current API call rate."""
        now = time.monotonic()
        cutoff = now - 60
        return sum(1 for t in self._api_call_times if t > cutoff)

    # ── Checks ─────────────────────────────────────────────────────

    def _check_loop(self) -> None:
        while self._running:
            try:
                self._check_all()
            except Exception:
                logger.exception("Resource check error")
            time.sleep(self._check_interval_s)

    def _check_all(self) -> None:
        alerts: list[ResourceAlert] = []

        # Disk usage
        disk_pct = self.monitor.disk_usage_pct(CONFIG.workspace_dir)
        if disk_pct is not None and disk_pct > self.limits.disk_usage_pct:
            alerts.append(ResourceAlert(
                resource="disk",
                value=disk_pct,
                limit=self.limits.disk_usage_pct,
                severity=ResourceSeverity.WARNING,
                action_taken="Triggering redundancy refinement",
            ))
            self._escalate(self._on_throttle,
                           f"Disk at {disk_pct:.1f}% (limit: {self.limits.disk_usage_pct}%)")

        # Memory usage
        mem_mb = self.monitor.memory_usage_mb()
        if mem_mb is not None and mem_mb > self.limits.max_memory_rss_mb:
            alerts.append(ResourceAlert(
                resource="memory",
                value=mem_mb,
                limit=float(self.limits.max_memory_rss_mb),
                severity=ResourceSeverity.CRITICAL,
                action_taken="Halting L2, fallback to L1 only",
            ))
            self._halt_requested = True
            self._escalate(self._on_halt,
                           f"Memory at {mem_mb:.0f}MB (limit: {self.limits.max_memory_rss_mb}MB)")

        # API call rate
        api_rate = self.api_calls_per_minute()
        if api_rate > self.limits.evaluator_api_calls_per_min:
            alerts.append(ResourceAlert(
                resource="evaluator_api",
                value=float(api_rate),
                limit=float(self.limits.evaluator_api_calls_per_min),
                severity=ResourceSeverity.WARNING,
                action_taken="Exponential backoff",
            ))
            self._escalate(self._on_throttle,
                           f"API rate at {api_rate}/min (limit: {self.limits.evaluator_api_calls_per_min})")

        # Record alerts
        with self._lock:
            self._alerts.extend(alerts)

        if alerts:
            for a in alerts:
                logger.warning("%s", a)

    def _escalate(self, callback: Optional[Callable], msg: str) -> None:
        if callback:
            try:
                callback(msg)
            except Exception:
                logger.exception("Escalation callback failed")

    def check_before_operation(self) -> Optional[str]:
        """Synchronous check before a potentially expensive operation.

        Returns an error message if limits are exceeded, None if OK.
        """
        mem_mb = self.monitor.memory_usage_mb()
        if mem_mb is not None and mem_mb > self.limits.max_memory_rss_mb * 0.9:
            return f"Memory high: {mem_mb:.0f}MB"

        disk_pct = self.monitor.disk_usage_pct(CONFIG.workspace_dir)
        if disk_pct is not None and disk_pct > self.limits.disk_usage_pct:
            return f"Disk high: {disk_pct:.1f}%"

        return None
