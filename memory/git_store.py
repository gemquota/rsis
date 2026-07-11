"""Git-backed versioned store — persistent, auditable memory layer.

Wraps the raw CheckpointManager with higher-level operations for
storing and retrieving knowledge artifacts across sessions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class GitStore:
    """Versioned artifact store backed by git.

    Stores knowledge artifacts (KG snapshots, strategy docs, schemas)
    as versioned files, enabling rollback and diffing across sessions.
    """

    def __init__(self, repo_path: str | Path) -> None:
        self._path = Path(repo_path)
        self._artifacts_dir = self._path / ".rsis" / "artifacts"
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)

    def store(self, key: str, data: Any, message: str = "") -> str:
        """Store a value as a versioned JSON artifact. Returns commit-ish."""
        import git

        path = self._artifacts_dir / f"{key}.json"
        path.write_text(json.dumps(data, indent=2, default=str))

        repo = git.Repo(self._path)
        repo.index.add([str(path.relative_to(self._path))])
        commit = repo.index.commit(message or f"store: {key}")
        logger.debug("Stored artifact %s at %s", key, commit.hexsha[:8])
        return commit.hexsha

    def load(self, key: str, rev: str = "HEAD") -> Any:
        """Load an artifact at a given revision."""
        import git

        repo = git.Repo(self._path)
        path = self._artifacts_dir / f"{key}.json"
        try:
            content = repo.git.show(f"{rev}:{path.relative_to(self._path)}")
            return json.loads(content)
        except git.exc.GitCommandError:
            logger.warning("Artifact %s not found at %s", key, rev)
            return None

    def list_keys(self) -> list[str]:
        """List all stored artifact keys."""
        return [p.stem for p in self._artifacts_dir.glob("*.json")]

    def diff(self, key: str, rev_a: str, rev_b: str = "HEAD") -> str:
        """Show diff of an artifact between two revisions."""
        import git

        repo = git.Repo(self._path)
        path = self._artifacts_dir / f"{key}.json"
        try:
            return repo.git.diff(rev_a, rev_b, "--", str(path.relative_to(self._path)))
        except git.exc.GitCommandError as exc:
            return f"diff error: {exc}"
