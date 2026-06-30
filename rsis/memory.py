"""Three-tier memory hierarchy.

Implements the RSIS memory model:
  Git (code truth) → Knowledge Graph (insights) → Vector Store (retrieval)

Phase 1 provides stubs for KG and vector store, with full git checkpointing
already operational via the checkpoint module.
"""

import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from rsis.config import CONFIG

logger = logging.getLogger(__name__)


# ── Knowledge Graph ──────────────────────────────────────────────────────

class KnowledgeGraph:
    """Lightweight knowledge graph for insights and relationships.

    Stores improvement patterns, success/failure relationships, and
    cross-session strategies. Serialized to JSON for durability.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or CONFIG.memory.knowledge_graph_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self._nodes = data.get("nodes", {})
                self._edges = data.get("edges", [])
                logger.info("Knowledge graph loaded (%d nodes, %d edges)",
                            len(self._nodes), len(self._edges))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load KG, starting fresh: %s", e)

    def save(self) -> None:
        data = {"nodes": self._nodes, "edges": self._edges}
        self.path.write_text(json.dumps(data, indent=2))
        logger.debug("Knowledge graph saved (%d nodes)", len(self._nodes))

    def add_node(self, node_id: str, node_type: str, **attrs) -> None:
        self._nodes[node_id] = {"id": node_id, "type": node_type, **attrs}
        self.save()

    def add_edge(self, source: str, target: str, rel: str, **attrs) -> None:
        self._edges.append({"source": source, "target": target, "rel": rel, **attrs})
        self.save()

    def get_node(self, node_id: str) -> Optional[dict]:
        return self._nodes.get(node_id)

    def query(self, node_type: Optional[str] = None, **attrs) -> list[dict]:
        results = list(self._nodes.values())
        if node_type:
            results = [n for n in results if n.get("type") == node_type]
        for k, v in attrs.items():
            results = [n for n in results if n.get(k) == v]
        return results

    def get_insights(self, limit: int = 10) -> list[dict]:
        """Return most recent insight nodes."""
        nodes = [n for n in self._nodes.values() if n.get("type") == "insight"]
        return nodes[-limit:]


# ── Vector Store (Stub) ──────────────────────────────────────────────────

class VectorStore:
    """Vector store for semantic retrieval over past improvements.

    Phase 1: in-memory stub using simple string matching.
    Phase 2+: replace with Chroma/Qdrant/pgvector.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or CONFIG.memory.vector_store_path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._documents: list[dict] = []
        self._load()

    def _load(self) -> None:
        index_file = self.path / "index.json"
        if index_file.exists():
            try:
                self._documents = json.loads(index_file.read_text())
                logger.info("Vector store loaded (%d documents)", len(self._documents))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load vector store: %s", e)

    def save(self) -> None:
        index_file = self.path / "index.json"
        index_file.write_text(json.dumps(self._documents, indent=2))

    def add(self, text: str, metadata: Optional[dict] = None) -> None:
        # In production: compute embedding and store in vector DB
        doc = {"text": text, "metadata": metadata or {}, "id": len(self._documents)}
        self._documents.append(doc)
        self.save()

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Simple keyword-based search (stub). Replace with embedding similarity."""
        query_lower = query.lower()
        scored = []
        for doc in self._documents:
            score = 0
            if query_lower in doc["text"].lower():
                score = 1.0
            elif any(query_lower in v.lower() if isinstance(v, str) else False
                     for v in doc.get("metadata", {}).values()):
                score = 0.5
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: -x[0])
        return [doc for _, doc in scored[:top_k]]


# ── Memory Manager ───────────────────────────────────────────────────────

class MemoryManager:
    """Coordinates the three-tier memory hierarchy."""

    def __init__(self, repo_root: str = "."):
        self.repo_root = Path(repo_root).resolve()
        self.kg = KnowledgeGraph()
        self.vectors = VectorStore()

    def record_improvement(
        self,
        description: str,
        target_files: list[str],
        eval_scores: dict,
        outcome: str,
    ) -> None:
        """Record an improvement attempt across all memory tiers."""
        # Knowledge graph
        node_id = f"improvement-{len(self.kg._nodes)}"
        self.kg.add_node(
            node_id=node_id,
            node_type="improvement",
            description=description,
            target_files=target_files,
            scores=eval_scores,
            outcome=outcome,
        )

        # Vector store
        self.vectors.add(
            text=f"{outcome}: {description}",
            metadata={"files": target_files, "scores": eval_scores},
        )

        logger.info("Recorded improvement in memory: %s — %s", node_id, outcome)

    def get_relevant_patterns(self, goal: str, limit: int = 5) -> list[str]:
        """Retrieve patterns relevant to a goal."""
        results = self.vectors.search(goal, top_k=limit)
        docs = [r["text"] for r in results]

        # Also pull recent KG insights
        insights = self.kg.get_insights(limit=3)
        docs.extend([i.get("description", "") for i in insights])

        return docs
