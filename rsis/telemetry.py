"""Workspace telemetry collection.

Collects file modification events, command history, resource usage, and
error rates per the RSIS specification.
"""

import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Optional

logger = logging.getLogger(__name__)


class TelemetryEvent:
    """A single telemetry event."""

    def __init__(
        self,
        event_type: str,
        path: Optional[str] = None,
        delta: Optional[str] = None,
        duration_ms: Optional[int] = None,
        metadata: Optional[dict] = None,
    ):
        self.type = event_type
        self.path = path
        self.delta = delta
        self.duration_ms = duration_ms
        self.metadata = metadata or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "path": self.path,
            "delta": self.delta,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
            **self.metadata,
        }


class TelemetryCollector:
    """Collects and flushes workspace telemetry."""

    def __init__(self, telemetry_dir: str = ".rsis/telemetry", flush_interval_s: int = 5):
        self.telemetry_dir = Path(telemetry_dir)
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)
        self.flush_interval_s = flush_interval_s
        self._buffer: list[dict] = []
        self._lock = Lock()
        self._session_id = str(uuid.uuid4())
        self._running = False
        self._thread: Optional[Thread] = None
        self._last_flush = time.monotonic()

    def start(self) -> None:
        """Start the background flush thread."""
        if self._running:
            return
        self._running = True
        self._thread = Thread(target=self._flush_loop, daemon=True, name="telemetry-flush")
        self._thread.start()
        logger.info("Telemetry collector started (session=%s)", self._session_id[:8])

    def stop(self) -> None:
        """Stop the flush thread and flush remaining events."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()

    def record(self, event: TelemetryEvent) -> None:
        """Record a telemetry event."""
        with self._lock:
            self._buffer.append(event.to_dict())

    def flush(self) -> None:
        """Write buffered events to disk."""
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        filename = f"{self._session_id}_{int(time.time())}.jsonl"
        path = self.telemetry_dir / filename
        try:
            with open(path, "a") as f:
                for ev in events:
                    f.write(json.dumps(ev) + "\n")
            self._last_flush = time.monotonic()
        except OSError as e:
            logger.error("Failed to flush telemetry: %s", e)
            # Put events back
            with self._lock:
                self._buffer.extend(events)

    def _flush_loop(self) -> None:
        while self._running:
            time.sleep(self.flush_interval_s)
            try:
                self.flush()
            except Exception:
                logger.exception("Telemetry flush error")

    def session_report(self) -> dict:
        """Generate a summary report for the current session."""
        return {
            "session_id": self._session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "events_collected": len(self._buffer),
            # In production, this would aggregate from persisted files
        }


class WorkspaceMonitor:
    """Lightweight workspace resource monitor using psutil when available."""

    def __init__(self):
        self._psutil = None
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            logger.warning("psutil not available — resource monitoring disabled")

    def cpu_usage(self) -> Optional[float]:
        if self._psutil:
            try:
                return self._psutil.cpu_percent(interval=0.1)
            except Exception:
                return None
        return None

    def memory_usage_mb(self) -> Optional[float]:
        if self._psutil:
            try:
                proc = self._psutil.Process()
                return proc.memory_info().rss / (1024 * 1024)
            except Exception:
                return None
        return None

    def disk_usage_pct(self, path: str = ".") -> Optional[float]:
        if self._psutil:
            try:
                return self._psutil.disk_usage(path).percent
            except Exception:
                return None
        return None
