"""Git-based checkpoint manager.

Implements checkpoint-before-mutation invariant: every destructive or
code-modifying operation is preceded by a git commit so rollback is always
possible.
"""

import hashlib
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages git checkpoints for recovery and rollback."""

    def __init__(self, repo_root: str = "."):
        self.repo_root = Path(repo_root).resolve()
        self._digest_cache: dict[str, str] = {}

    # ── git helpers ───────────────────────────────────────────────────

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True, text=True, timeout=30,
        )

    def ensure_repo(self) -> None:
        """Initialise git repo if not already one."""
        if not (self.repo_root / ".git").exists():
            logger.info("Initialising git repository at %s", self.repo_root)
            self._git("init", "-b", "main")
            self._git("config", "user.email", "rsis@localhost")
            self._git("config", "user.name", "RSIS")

    def has_changes(self) -> bool:
        """Check whether there are uncommitted changes."""
        r = self._git("status", "--porcelain")
        return bool(r.stdout.strip())

    def checkpoint(self, message: str = "") -> Optional[str]:
        """Create a git checkpoint (commit). Returns commit hash or None."""
        self.ensure_repo()
        if not self.has_changes():
            return None

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        commit_msg = f"rsis-checkpoint: {message or 'pre-mutation'} [{timestamp}]"

        self._git("add", "-A")
        r = self._git("commit", "-m", commit_msg)
        if r.returncode != 0:
            logger.warning("Checkpoint failed: %s", r.stderr.strip())
            return None

        # Extract commit hash
        r2 = self._git("rev-parse", "HEAD")
        commit_hash = r2.stdout.strip()
        logger.info("Checkpoint created: %s — %s", commit_hash[:12], commit_msg)
        return commit_hash

    def rollback(self, commit_hash: str) -> bool:
        """Rollback to a specific commit."""
        logger.warning("Rolling back to %s", commit_hash[:12])
        r = self._git("checkout", commit_hash, "--")
        if r.returncode != 0:
            logger.error("Rollback failed: %s", r.stderr.strip())
            return False
        # Also hard-reset to that commit
        self._git("reset", "--hard", commit_hash)
        return True

    def rollback_last_checkpoint(self) -> bool:
        """Rollback to the most recent RSIS checkpoint."""
        r = self._git("log", "--oneline", "-20", "--grep=rsis-checkpoint:")
        commits = r.stdout.strip().splitlines()
        if not commits:
            logger.warning("No RSIS checkpoints found to rollback to.")
            return False
        # First line is the most recent
        commit_hash = commits[0].split()[0]
        return self.rollback(commit_hash)

    def latest_checkpoint(self) -> Optional[str]:
        """Return the most recent RSIS checkpoint hash."""
        r = self._git("log", "--oneline", "-1", "--grep=rsis-checkpoint:", "--format=%H")
        return r.stdout.strip() or None

    # ── Digest verification ───────────────────────────────────────────

    def sha256_digest(self, path: str) -> str:
        """Compute SHA-256 digest of a file (cached)."""
        abspath = str(self.repo_root / path)
        if abspath in self._digest_cache:
            return self._digest_cache[abspath]

        h = hashlib.sha256()
        with open(abspath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)

        digest = h.hexdigest()
        self._digest_cache[abspath] = digest
        return digest

    def verify_digest(self, path: str, expected: str) -> bool:
        """Verify a file's SHA-256 matches an expected digest."""
        actual = self.sha256_digest(path)
        ok = actual == expected
        if not ok:
            logger.error("Digest mismatch for %s: expected=%s got=%s",
                         path, expected[:16], actual[:16])
        return ok
