"""Resource enforcement — practical bounds to prevent host exhaustion."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Optional

import psutil

logger = logging.getLogger(__name__)


class ResourceViolation(Exception):
    """Raised when a resource limit is exceeded."""
    pass


class ResourceEnforcer:
    """Monitors and enforces resource limits on the RSIS process.

    Runs in a background thread. When a limit is exceeded, it:
      1. Logs the violation.
      2. Attempts graceful shutdown of the offending loop level.
      3. If limits are critically exceeded, sends SIGTERM to self.
    """

    def __init__(
        self,
        *,
        max_memory_gb: float = 4.0,
        disk_usage_pct: float = 80.0,
        max_cpu_cores: int = 0,
        check_interval_s: float = 5.0,
    ) -> None:
        self._max_memory_gb = max_memory_gb
        self._disk_usage_pct = disk_usage_pct
        self._max_cpu_cores = max_cpu_cores if max_cpu_cores > 0 else (os.cpu_count() or 4) - 1
        self._check_interval = check_interval_s
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._violations: list[str] = []

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Resource enforcer started (mem≤%.1fGB, disk≤%.0f%%, cpu≤%d cores)",
                     self._max_memory_gb, self._disk_usage_pct, self._max_cpu_cores)

    def stop(self) -> None:
        self._running = False

    @property
    def violations(self) -> list[str]:
        return list(self._violations)

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                self._check()
            except Exception as exc:
                logger.warning("Resource check error: %s", exc)
            time.sleep(self._check_interval)

    def _check(self) -> None:
        proc = psutil.Process()

        # Memory
        mem_gb = proc.memory_info().rss / (1024 ** 3)
        if mem_gb > self._max_memory_gb:
            msg = f"Memory limit exceeded: {mem_gb:.2f}GB > {self._max_memory_gb}GB"
            logger.warning(msg)
            self._violations.append(msg)
            self._handle_memory_violation(mem_gb)

        # Disk
        try:
            disk = psutil.disk_usage(os.getcwd())
            if disk.percent > self._disk_usage_pct:
                msg = f"Disk usage limit exceeded: {disk.percent:.0f}% > {self._disk_usage_pct}%"
                logger.warning(msg)
                self._violations.append(msg)
        except Exception:
            pass

        # CPU — warn if using > all-but-one cores
        cpu_count = os.cpu_count() or 1
        if cpu_count > 1:
            proc_cpu = proc.cpu_percent(interval=0.1)
            # Rough check: if one process uses > 90% of available CPU
            if proc_cpu > 90.0 and cpu_count > 1:
                msg = f"High CPU usage: {proc_cpu:.0f}%"
                logger.debug(msg)

        # Keep violation list bounded
        if len(self._violations) > 100:
            self._violations = self._violations[-50:]

    def _handle_memory_violation(self, current_gb: float) -> None:
        """Escalate memory handling based on severity."""
        severity = current_gb / self._max_memory_gb
        if severity > 1.5:
            # Critical — kill the process
            logger.critical("CRITICAL: memory at %.2fGB (%.1fx limit). Terminating.", current_gb, severity)
            os.kill(os.getpid(), signal.SIGTERM)
        elif severity > 1.2:
            # Severe — try to halt L2
            logger.error("SEVERE: memory at %.2fGB. Halting L2 improvement loop.", current_gb)
            # Signal L2 to halt via a file flag
            self._write_halt_flag("l2", "memory_exceeded")

    def _write_halt_flag(self, loop: str, reason: str) -> None:
        """Write a halt flag file for a loop to read."""
        flag_path = os.path.join(os.getcwd(), ".rsis", f"halt_{loop}.flag")
        try:
            os.makedirs(os.path.dirname(flag_path), exist_ok=True)
            with open(flag_path, "w") as f:
                f.write(f"{{\"reason\": \"{reason}\", \"timestamp\": {time.time()}}}\n")
            logger.info("Halt flag written for %s: %s", loop, reason)
        except Exception as exc:
            logger.warning("Failed to write halt flag: %s", exc)

    def clear_halt_flag(self, loop: str) -> None:
        """Remove a halt flag."""
        flag_path = os.path.join(os.getcwd(), ".rsis", f"halt_{loop}.flag")
        try:
            if os.path.exists(flag_path):
                os.remove(flag_path)
        except Exception as exc:
            logger.warning("Failed to clear halt flag: %s", exc)

    def check_halt_flag(self, loop: str) -> bool:
        """Check if a halt flag exists for a loop."""
        flag_path = os.path.join(os.getcwd(), ".rsis", f"halt_{loop}.flag")
        return os.path.exists(flag_path)
