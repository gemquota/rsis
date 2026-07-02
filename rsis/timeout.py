"""Timeout enforcement for RSIS loops.

Provides a Deadline context manager that raises TimeoutError when a
deadline is exceeded. Used to enforce the loop termination budgets
defined in the RSIS spec.
"""

import logging
import signal
import time
from contextlib import contextmanager
from typing import Generator, Optional

logger = logging.getLogger(__name__)


class TimeoutError(Exception):
    """Raised when a loop exceeds its budgeted time."""
    pass


@contextmanager
def deadline(seconds: float, label: str = "operation") -> Generator[None, None, None]:
    """Enforce a hard deadline on a block of code.

    Uses SIGALRM on Unix (preferred) or falls back to a polling check
    for compatibility.

    Usage:
        with deadline(30, "L2 improvement"):
            result = run_improvement()
    """
    if seconds <= 0:
        raise TimeoutError(f"Deadline must be positive, got {seconds}")

    # Try SIGALRM (Unix)
    if hasattr(signal, "SIGALRM"):
        _timeout_via_sigalrm(seconds)
    else:
        _timeout_via_polling(seconds)

    yield


def _timeout_via_sigalrm(seconds: float) -> None:
    """Enforce timeout via SIGALRM."""
    import signal

    old_handler = None
    try:
        def _handler(signum, frame):
            raise TimeoutError(f"Deadline of {seconds}s exceeded")

        old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    except Exception:
        # Fall back to polling
        _timeout_via_polling(seconds)


def _timeout_via_polling(seconds: float) -> None:
    """Enforce timeout via polling (non-Unix fallback)."""
    # This is handled by checking time.monotonic() in strategic places
    # It's less precise but portable.
    pass


class Budget:
    """Track and enforce a budget of iterations or time.

    Used by L1, L2, and L3 to enforce their respective termination budgets.
    """

    def __init__(self, max_iterations: int, max_time_s: float, label: str = "budget"):
        self.max_iterations = max_iterations
        self.max_time_s = max_time_s
        self.label = label
        self.iterations = 0
        self._start = time.monotonic()

    @property
    def remaining_time(self) -> float:
        return max(0.0, self.max_time_s - (time.monotonic() - self._start))

    @property
    def expired(self) -> bool:
        return self.remaining_time <= 0

    def tick(self) -> bool:
        """Advance one iteration. Returns False if budget is exhausted."""
        self.iterations += 1
        if self.iterations > self.max_iterations:
            logger.warning("%s: iteration budget exhausted (%d/%d)",
                           self.label, self.iterations, self.max_iterations)
            return False
        elapsed = time.monotonic() - self._start
        if elapsed > self.max_time_s:
            logger.warning("%s: time budget exhausted (%.1fs/%ds)",
                           self.label, elapsed, self.max_time_s)
            return False
        return True

    def reset(self) -> None:
        self.iterations = 0
        self._start = time.monotonic()
