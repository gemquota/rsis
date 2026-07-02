"""Recovery mechanism hardening — testing and enforcement.

Implements the triple recovery pattern from the RSIS spec:
  Checkpoint rollback → Human-in-the-loop (notified) → Fallback interpreter

Also provides automated recovery testing via failure injection.
"""

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Callable

from rsis.checkpoint import CheckpointManager
from rsis.config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class RecoveryTestResult:
    """Result of a recovery mechanism test."""
    mechanism: str
    success: bool
    duration_ms: int
    error: Optional[str] = None


class RecoveryManager:
    """Manages the triple recovery system."""

    def __init__(self, checkpoint_mgr: Optional[CheckpointManager] = None):
        self.checkpoint = checkpoint_mgr or CheckpointManager(CONFIG.workspace_dir)
        self._fallback_interpreter: Optional[Path] = None
        self._halted = False
        self._failure_count = 0
        self._max_failures = 3  # Cascading failure threshold

    # ── Mechanism 1: Checkpoint Rollback ───────────────────────────

    def rollback_on_failure(self, context: str) -> bool:
        """Rollback to the last checkpoint after a failure.

        Returns True if rollback succeeded.
        """
        logger.warning("Attempting rollback after failure in: %s", context)
        latest = self.checkpoint.latest_checkpoint()
        if not latest:
            logger.error("No checkpoint to rollback to — cannot recover")
            return False

        ok = self.checkpoint.rollback(latest)
        if ok:
            logger.info("Rollback successful to %s", latest[:12])
            return True
        else:
            logger.error("Rollback failed — escalating to human-in-loop")
            self._notify_human(f"Rollback failed for {context}")
            return False

    # ── Mechanism 2: Human-in-the-Loop ─────────────────────────────

    def _notify_human(self, message: str) -> None:
        """Notify a human operator about a failure requiring intervention.

        In production, this would send a notification (email, Slack, etc.).
        """
        log_path = Path(CONFIG.workspace_dir) / ".rsis" / "human_alerts.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "action_required": "Manual intervention needed",
        }
        import json
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        logger.critical("HUMAN-IN-LOOP ALERT: %s (logged to %s)", message, log_path)

    def request_human_review(self, context: str) -> None:
        """Request human review for a decision the system can't make alone."""
        self._notify_human(f"Review requested for: {context}")

    # ── Mechanism 3: Fallback Interpreter ──────────────────────────

    def set_fallback_interpreter(self, path: str) -> None:
        """Set a fallback interpreter path for emergency execution."""
        self._fallback_interpreter = Path(path).resolve()
        if not self._fallback_interpreter.exists():
            logger.warning("Fallback interpreter not found at %s", path)
        else:
            logger.info("Fallback interpreter set: %s", self._fallback_interpreter)

    def execute_via_fallback(self, code: str) -> Optional[str]:
        """Execute a recovery routine via the fallback interpreter.

        This is used when the main interpreter is corrupted.
        """
        if not self._fallback_interpreter or not self._fallback_interpreter.exists():
            logger.error("No valid fallback interpreter available")
            return None

        try:
            r = subprocess.run(
                [str(self._fallback_interpreter), "-c", code],
                capture_output=True, text=True, timeout=30,
            )
            return r.stdout if r.returncode == 0 else r.stderr
        except Exception as e:
            logger.error("Fallback execution failed: %s", e)
            return None

    # ── Failure tracking ───────────────────────────────────────────

    def record_failure(self) -> None:
        """Record a failure. Triggers escalation if threshold exceeded."""
        self._failure_count += 1
        logger.warning("Failure count: %d/%d", self._failure_count, self._max_failures)

        if self._failure_count >= self._max_failures:
            self._halted = True
            self._notify_human(
                f"System halted after {self._failure_count} failures"
            )

    def reset_failure_count(self) -> None:
        """Reset the failure counter after successful recovery."""
        self._failure_count = 0


# ── Failure Injection Testing ────────────────────────────────────────────

class FailureInjector:
    """Injects controlled failures to test recovery mechanisms.

    Used in automated recovery testing to verify that each mechanism
    works correctly.
    """

    def __init__(self, workspace: str = "."):
        self.workspace = Path(workspace).resolve()
        self._injected_failures: list[dict] = []

    def corrupt_file(self, path: str) -> bool:
        """Corrupt a file by overwriting with garbage bytes."""
        target = self.workspace / path
        if not target.exists():
            logger.warning("Cannot corrupt %s: not found", path)
            return False
        try:
            target.write_bytes(b"\x00\xFF" * 100)
            self._injected_failures.append({
                "type": "file_corruption",
                "path": path,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("Injected file corruption: %s", path)
            return True
        except OSError as e:
            logger.error("Failed to corrupt %s: %s", path, e)
            return False

    def delete_file(self, path: str) -> bool:
        """Delete a critical file to test recovery."""
        target = self.workspace / path
        if not target.exists():
            return False
        target.unlink()
        self._injected_failures.append({
            "type": "file_deletion",
            "path": path,
        })
        logger.warning("Injected file deletion: %s", path)
        return True

    def simulate_crash(self, path: str) -> bool:
        """Create an invalid syntax file to simulate a crash."""
        target = self.workspace / path
        target.write_text("this is not valid Python $$$")
        self._injected_failures.append({
            "type": "syntax_error",
            "path": path,
        })
        logger.info("Injected syntax error: %s", path)
        return True

    def reset_all(self) -> None:
        """Clear all injected failures."""
        self._injected_failures.clear()
        logger.info("All injected failures cleared")
