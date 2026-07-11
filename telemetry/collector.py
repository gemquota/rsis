"""Workspace telemetry collector — file events, resource usage, command history."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

import psutil

logger = logging.getLogger(__name__)


class TelemetryEvent:
    """A single telemetry data point."""

    def __init__(
        self,
        event_type: str,
        data: dict[str, Any],
        source: str = "collector",
    ) -> None:
        self.timestamp = time.time()
        self.event_type = event_type
        self.data = data
        self.source = source

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": self.timestamp,
            "type": self.event_type,
            "source": self.source,
            "data": self.data,
        }


class TelemetryCollector:
    """Collects workspace and system telemetry.

    Three sources:
      1. Filesystem events (via watchdog)
      2. System resource usage (via psutil)
      3. Manual events (from L1/L2/L3 loops)
    """

    def __init__(
        self,
        *,
        watch_paths: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        report_file: str | Path | None = None,
        flush_interval_s: float = 10.0,
    ) -> None:
        self._watch_paths = watch_paths or ["."]
        self._ignore_patterns = ignore_patterns or [".rsis", "__pycache__", ".git"]
        self._report_file = Path(report_file) if report_file else None
        self._flush_interval = flush_interval_s
        self._buffer: list[TelemetryEvent] = []
        self._running = False
        self._watcher_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start background collection."""
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.info("Telemetry collector started")

    async def stop(self) -> None:
        """Stop background collection and flush remaining events."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush()

    def record(
        self,
        event_type: str,
        data: dict[str, Any],
        source: str = "collector",
    ) -> None:
        """Record a telemetry event."""
        event = TelemetryEvent(event_type, data, source)
        self._buffer.append(event)

    def resource_snapshot(self) -> dict[str, Any]:
        """Take a snapshot of current system resource usage."""
        proc = psutil.Process()
        with proc.oneshot():
            cpu = proc.cpu_percent(interval=0.0)
            mem = proc.memory_info().rss / (1024 ** 3)  # GB
            num_fds = proc.num_fds()
            disk = psutil.disk_usage(os.getcwd())
        return {
            "cpu_percent": cpu,
            "memory_gb": round(mem, 2),
            "num_fds": num_fds,
            "disk_used_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024 ** 3), 1),
        }

    async def flush(self) -> None:
        """Flush buffered events to disk."""
        if not self._buffer or not self._report_file:
            return

        events = [e.to_dict() for e in self._buffer]
        self._buffer.clear()

        try:
            self._report_file.parent.mkdir(parents=True, exist_ok=True)
            async with asyncio.Lock():
                with open(self._report_file, "a") as f:
                    for event in events:
                        f.write(json.dumps(event) + "\n")
            logger.debug("Flushed %d telemetry events", len(events))
        except Exception as exc:
            logger.warning("Telemetry flush failed: %s", exc)
            # Re-buffer on failure
            self._buffer.extend([TelemetryEvent(e["type"], e["data"], e["source"]) for e in events])

    async def _periodic_flush(self) -> None:
        """Periodically flush and collect resource snapshots."""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                # Auto-record resource snapshot
                snap = self.resource_snapshot()
                self.record("resource_snapshot", snap)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Periodic collection error: %s", exc)

    def recent_events(self, n: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        """Return recent events from the buffer + report file."""
        events = [e.to_dict() for e in self._buffer[-n:]]
        if self._report_file and self._report_file.exists():
            try:
                with open(self._report_file) as f:
                    lines = f.readlines()[-n:]
                    for line in lines:
                        events.append(json.loads(line))
            except Exception:
                pass
        if event_type:
            events = [e for e in events if e.get("type") == event_type]
        return events[-n:]
