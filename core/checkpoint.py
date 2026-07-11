"""Git-based checkpoint & rollback — the foundation of safe self-modification."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import git

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Creates git checkpoints before mutations and enables rollback."""

    def __init__(self, repo_path: str | Path, author: str = "RSIS <rsis@local>") -> None:
        self._repo_path = Path(repo_path)
        self._author = author
        self._repo: Optional[git.Repo] = None
        self._checkpoint_tag_counter = 0

    @property
    def repo(self) -> git.Repo:
        if self._repo is None:
            self._repo = git.Repo(self._repo_path)
        return self._repo

    @staticmethod
    def _parse_author() -> tuple[str, str]:
        name, email = 'RSIS <rsis@local>'.split(' <')
        return name, email.rstrip('>')

    def _ensure_initial_commit(self) -> None:
        """Create the initial commit if the repo is bare/new."""
        try:
            self.repo.head.object  # raises if no commit
        except (ValueError, git.exc.BadName):
            # No commits yet — create empty initial commit
            self.repo.index.commit("RSIS: initial snapshot", author=git.Actor(*self._author.split(" <")))

    def ensure_repo(self) -> None:
        """Initialise or verify the git repository exists."""
        git_dir = self._repo_path / ".git"
        if not git_dir.exists():
            logger.info("Initialising git repository at %s", self._repo_path)
            self._repo = git.Repo.init(self._repo_path)
            # Set local git config for author identity
            with self._repo.config_writer() as cw:
                cw.set_value('user', 'email', 'rsis@local')
                cw.set_value('user', 'name', 'RSIS')
            self._ensure_initial_commit()
        else:
            self._repo = git.Repo(self._repo_path)
            # Ensure local git config exists
            try:
                with self._repo.config_writer() as cw:
                    if not cw.has_option('user', 'email'):
                        cw.set_value('user', 'email', 'rsis@local')
                    if not cw.has_option('user', 'name'):
                        cw.set_value('user', 'name', 'RSIS')
            except Exception:
                pass
            self._ensure_initial_commit()

    def create_checkpoint(self, label: str = "auto") -> str:
        """Snapshot the working tree before a mutation."""
        self.ensure_repo()
        self._checkpoint_tag_counter += 1
        tag = f"ckpt-{self._checkpoint_tag_counter:04d}-{label}"
        self.repo.git.add(A=True)
        if self.repo.index.diff("HEAD") or self.repo.untracked_files:
            self.repo.index.commit(f"checkpoint: {label}", author=git.Actor(*self._author.split(" <")))
        self.repo.create_tag(tag, message=f"RSIS checkpoint: {label}")
        logger.debug("Checkpoint created: %s", tag)
        return tag

    def rollback(self, tag: Optional[str] = None) -> None:
        """Restore the working tree to a previous checkpoint (or HEAD)."""
        self.ensure_repo()
        target = tag or "HEAD"
        logger.warning("Rolling back to %s", target)
        self.repo.git.reset("--hard", target)
        self.repo.git.clean("-fd")

    def list_checkpoints(self) -> list[str]:
        """Return all checkpoint tags sorted most-recent-first."""
        self.ensure_repo()
        tags = sorted(
            (t.name for t in self.repo.tags if t.name.startswith("ckpt-")),
            reverse=True,
        )
        return tags

    def create_experiment_branch(self, name: str) -> str:
        """Create and switch to a new branch for an improvement attempt."""
        self.ensure_repo()
        if name in self.repo.branches:
            logger.warning("Branch %s already exists, deleting and recreating", name)
            self.repo.delete_head(name)
        branch = self.repo.create_head(name)
        branch.checkout()
        logger.info("Switched to experiment branch: %s", name)
        return name

    def merge_experiment(self, branch_name: str, message: str = "RSIS: merge improvement") -> None:
        """Merge an approved experiment branch back to main/master."""
        self.ensure_repo()
        main_branch = "main" if "main" in self.repo.heads else "master"
        main = self.repo.heads[main_branch]
        main.checkout()
        self.repo.git.merge(branch_name, "--no-ff", "-m", message)
        self.repo.delete_head(branch_name)
        logger.info("Merged and deleted branch: %s", branch_name)
