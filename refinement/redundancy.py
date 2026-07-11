"""Redundancy refinement — prune stale code, compress memory, report bloat."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from rsis.memory.knowledge_graph import KnowledgeGraph
from rsis.memory.vector_store import VectorStore

logger = logging.getLogger(__name__)


class RefinementReport:
    """Result of a redundancy refinement cycle."""

    def __init__(self) -> None:
        self.branches_pruned: int = 0
        self.kg_nodes_pruned: int = 0
        self.vectors_deduplicated: int = 0
        self.disk_space_reclaimed_mb: float = 0.0
        self.duration_ms: float = 0.0
        self.issues: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "branches_pruned": self.branches_pruned,
            "kg_nodes_pruned": self.kg_nodes_pruned,
            "vectors_deduplicated": self.vectors_deduplicated,
            "disk_space_reclaimed_mb": round(self.disk_space_reclaimed_mb, 1),
            "duration_ms": round(self.duration_ms, 1),
            "issues": self.issues,
        }


class RedundancyRefiner:
    """Periodically prunes and compresses the RSIS memory hierarchy.

    Operates on three levels:
      1. Git branches — delete stale experiment branches.
      2. Knowledge graph — prune orphaned nodes with zero references.
      3. Vector store — deduplicate near-identical embeddings.
    """

    def __init__(
        self,
        *,
        repo_path: str | Path,
        kg: KnowledgeGraph,
        vector_store: VectorStore,
        stale_days: int = 7,
    ) -> None:
        self._repo_path = Path(repo_path)
        self._kg = kg
        self._vector_store = vector_store
        self._stale_days = stale_days

    async def run(self) -> RefinementReport:
        """Execute all refinement passes."""
        report = RefinementReport()
        start = time.monotonic()

        try:
            self._prune_branches(report)
        except Exception as exc:
            report.issues.append(f"Branch pruning failed: {exc}")
            logger.warning("Branch pruning error: %s", exc)

        try:
            self._prune_kg(report)
        except Exception as exc:
            report.issues.append(f"KG pruning failed: {exc}")
            logger.warning("KG pruning error: %s", exc)

        try:
            self._deduplicate_vectors(report)
        except Exception as exc:
            report.issues.append(f"Vector dedup failed: {exc}")
            logger.warning("Vector dedup error: %s", exc)

        report.duration_ms = (time.monotonic() - start) * 1000
        logger.info("Refinement complete: %s", report.to_dict())
        return report

    def _prune_branches(self, report: RefinementReport) -> None:
        """Delete stale experiment branches."""
        import git
        try:
            repo = git.Repo(self._repo_path)
        except git.exc.InvalidGitRepositoryError:
            return

        now = time.time()
        threshold = now - (self._stale_days * 86400)

        for branch in repo.branches:
            name = branch.name
            # Skip main/master
            if name in ("main", "master"):
                continue
            # Check if it looks like an experiment branch
            if "attempt" not in name and "l2" not in name:
                continue

            try:
                commit = branch.commit
                if commit.committed_date < threshold:
                    repo.delete_head(branch.name)
                    report.branches_pruned += 1
                    logger.debug("Pruned stale branch: %s", name)
            except Exception:
                continue

    def _prune_kg(self, report: RefinementReport) -> None:
        """Remove knowledge graph nodes with zero relationships."""
        kg = self._kg._graph  # access the underlying nx graph
        orphaned = [
            node for node in kg.nodes()
            if kg.degree(node) == 0  # no edges at all
        ]
        for node_id in orphaned:
            kg.remove_node(node_id)
            report.kg_nodes_pruned += 1
        if orphaned:
            self._kg.save()
            logger.debug("Pruned %d orphaned KG nodes", len(orphaned))

    def _deduplicate_vectors(self, report: RefinementReport) -> None:
        """Remove near-duplicate vectors (cosine similarity > 0.95)."""
        all_docs = self._vector_store.list_all()
        if len(all_docs) < 2:
            return

        # Simple content-hash dedup: if code is identical or nearly identical
        seen_hashes: set[str] = set()
        for doc in all_docs:
            code = doc.get("code", "")
            if not code:
                continue
            # Use first 100 chars as a content signature
            sig = code[:200]
            if sig in seen_hashes:
                self._vector_store.delete_pattern(doc["id"])
                report.vectors_deduplicated += 1
            else:
                seen_hashes.add(sig)
