"""Three-tier memory hierarchy.

  Git (code truth, via checkpoint.py)
    → Knowledge Graph (insights & relationships, via NetworkX)
    → Vector Store (semantic retrieval, via numpy embeddings)

Phase 2 upgrade: NetworkX for KG, numpy-based character n-gram embeddings
for vector similarity search.
"""

import json
import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import networkx as nx
import numpy as np

from rsis.config import CONFIG

logger = logging.getLogger(__name__)


# ── Character N-Gram Embeddings (lightweight, no external deps) ──────────

class NGramVectorizer:
    """Character n-gram based text vectorizer using numpy.

    Produces fixed-dimension embeddings by hashing character n-grams into
    a sparse vector. Supports cosine similarity search.
    """

    def __init__(self, ngram_range: tuple[int, int] = (2, 4), dim: int = 256):
        self.ngram_range = ngram_range
        self.dim = dim

    def _ngrams(self, text: str) -> list[str]:
        """Extract character n-grams from text."""
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", "", text)
        grams = []
        for n in range(self.ngram_range[0], self.ngram_range[1] + 1):
            for i in range(len(text) - n + 1):
                grams.append(text[i : i + n])
        return grams

    def embed(self, text: str) -> np.ndarray:
        """Convert text to a fixed-dimension embedding vector."""
        vec = np.zeros(self.dim, dtype=np.float32)
        grams = self._ngrams(text)
        if not grams:
            return vec
        for gram in grams:
            idx = hash(gram) % self.dim
            vec[idx] += 1.0
        # Normalise
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec


# ── Vector Store ─────────────────────────────────────────────────────────

class VectorStore:
    """Persistent vector store with numpy-based similarity search.

    Documents are stored as JSON on disk. Embeddings are computed on the fly
    using NGramVectorizer and cached in memory.
    """

    def __init__(self, path: Optional[str] = None, dim: int = 256):
        self.path = Path(path or CONFIG.memory.vector_store_path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.vectorizer = NGramVectorizer(dim=dim)
        self._documents: list[dict] = []
        self._embeddings: list[np.ndarray] = []
        self._load()

    def _load(self) -> None:
        index_file = self.path / "index.json"
        if index_file.exists():
            try:
                data = json.loads(index_file.read_text())
                self._documents = data.get("documents", [])
                # Recompute embeddings
                for doc in self._documents:
                    emb = self.vectorizer.embed(doc.get("text", ""))
                    self._embeddings.append(emb)
                logger.info("Vector store loaded (%d documents)", len(self._documents))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load vector store: %s", e)

    def save(self) -> None:
        index_file = self.path / "index.json"
        # Store documents without embeddings (recomputed on load)
        data = {"documents": self._documents}
        index_file.write_text(json.dumps(data, indent=2, default=str))
        logger.debug("Vector store saved (%d documents)", len(self._documents))

    def add(self, text: str, metadata: Optional[dict] = None) -> int:
        """Add a document. Returns its ID."""
        doc_id = len(self._documents)
        doc = {"id": doc_id, "text": text, "metadata": metadata or {}}
        self._documents.append(doc)
        self._embeddings.append(self.vectorizer.embed(text))
        self.save()
        return doc_id

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Search by cosine similarity. Returns documents with scores."""
        if not self._documents:
            return []

        query_vec = self.vectorizer.embed(query)
        matrix = np.array(self._embeddings)  # (N, dim)
        scores = matrix @ query_vec  # cosine similarity (already normalised)

        # Top-k indices
        if len(scores) <= top_k:
            top_idx = range(len(scores))
        else:
            top_idx = np.argpartition(scores, -top_k)[-top_k:]
            # Sort by descending score
            top_idx = top_idx[np.argsort(-scores[top_idx])]

        results = []
        for idx in top_idx:
            doc = dict(self._documents[idx])
            doc["score"] = float(scores[idx])
            results.append(doc)
        return results


# ── Knowledge Graph ──────────────────────────────────────────────────────

class KnowledgeGraph:
    """Knowledge graph using NetworkX for insights and relationships.

    Nodes represent improvements, patterns, strategies, and outcomes.
    Edges capture relationships like 'led_to', 'contradicts', 'improves'.
    """

    NODE_TYPES = ("improvement", "insight", "strategy", "pattern", "failure")

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or CONFIG.memory.knowledge_graph_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = nx.MultiDiGraph()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                # Rebuild graph from serialised data
                for node in data.get("nodes", []):
                    self.graph.add_node(node["id"], **node.get("attrs", {}))
                for edge in data.get("edges", []):
                    self.graph.add_edge(
                        edge["source"], edge["target"],
                        key=edge.get("key"),
                        rel=edge["rel"],
                        **edge.get("attrs", {}),
                    )
                logger.info("KG loaded (%d nodes, %d edges)",
                            self.graph.number_of_nodes(),
                            self.graph.number_of_edges())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load KG, starting fresh: %s", e)

    def save(self) -> None:
        nodes = [
            {"id": n, "attrs": dict(self.graph.nodes[n])}
            for n in self.graph.nodes
        ]
        edges = []
        for u, v, k, data in self.graph.edges(keys=True, data=True):
            edges.append({
                "source": u,
                "target": v,
                "key": k,
                "rel": data.get("rel", ""),
                "attrs": {k2: v2 for k2, v2 in data.items() if k2 != "rel"},
            })
        data = {"nodes": nodes, "edges": edges}
        self.path.write_text(json.dumps(data, indent=2, default=str))
        logger.debug("KG saved (%d nodes, %d edges)",
                     len(nodes), len(edges))

    # ── Node operations ─────────────────────────────────────────────

    def add_node(self, node_id: str, node_type: str, **attrs) -> str:
        """Add a node. node_type must be one of NODE_TYPES."""
        if node_type not in self.NODE_TYPES:
            logger.warning("Unknown node type: %s", node_type)
        self.graph.add_node(node_id, type=node_type, **attrs)
        self.save()
        return node_id

    def get_node(self, node_id: str) -> Optional[dict]:
        if node_id not in self.graph:
            return None
        return {"id": node_id, **dict(self.graph.nodes[node_id])}

    def query(self, node_type: Optional[str] = None, **attrs) -> list[dict]:
        results = []
        for n, data in self.graph.nodes(data=True):
            if node_type and data.get("type") != node_type:
                continue
            if all(data.get(k) == v for k, v in attrs.items()):
                results.append({"id": n, **dict(data)})
        return results

    def remove_node(self, node_id: str) -> bool:
        if node_id in self.graph:
            self.graph.remove_node(node_id)
            self.save()
            return True
        return False

    # ── Edge operations ─────────────────────────────────────────────

    def add_edge(self, source: str, target: str, rel: str, **attrs) -> None:
        self.graph.add_edge(source, target, rel=rel, **attrs)
        self.save()

    def get_edges(self, node_id: Optional[str] = None) -> list[dict]:
        if node_id:
            edges = list(self.graph.edges(node_id, keys=True, data=True))
            edges += list(self.graph.edges(None, node_id, keys=True, data=True))
        else:
            edges = list(self.graph.edges(keys=True, data=True))

        result = []
        for u, v, k, data in edges:
            result.append({
                "source": u, "target": v,
                "rel": data.get("rel", ""),
                **{k2: v2 for k2, v2 in data.items() if k2 != "rel"},
            })
        return result

    # ── Analytics ───────────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return self.graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    def get_insights(self, limit: int = 10) -> list[dict]:
        """Return most recent insight/improvement nodes."""
        nodes = [
            {"id": n, **dict(data)}
            for n, data in self.graph.nodes(data=True)
            if data.get("type") in ("insight", "improvement")
        ]
        return nodes[-limit:]

    def get_strategies(self) -> list[dict]:
        """Return all strategy nodes."""
        return self.query(node_type="strategy")

    def get_failure_patterns(self) -> list[dict]:
        """Return all failure pattern nodes."""
        return self.query(node_type="failure")

    def find_related(self, node_id: str, rel: Optional[str] = None) -> list[dict]:
        """Find nodes related to a given node via edges."""
        related = []
        for u, v, data in self.graph.edges(node_id, data=True):
            if rel and data.get("rel") != rel:
                continue
            related.append({"id": v, **dict(self.graph.nodes[v])})
        for u, v, data in self.graph.edges(None, node_id, data=True):
            if rel and data.get("rel") != rel:
                continue
            related.append({"id": u, **dict(self.graph.nodes[u])})
        return related


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
        goal: str = "",
    ) -> str:
        """Record an improvement attempt across all memory tiers."""
        node_id = f"improvement-{self.kg.node_count}"

        # Knowledge graph
        self.kg.add_node(
            node_id=node_id,
            node_type="improvement",
            description=description,
            target_files=target_files,
            scores=eval_scores,
            outcome=outcome,
            goal=goal,
        )

        # Vector store (for semantic retrieval)
        self.vectors.add(
            text=f"{outcome}: {description}",
            metadata={
                "node_id": node_id,
                "files": target_files,
                "scores": eval_scores,
                "goal": goal,
            },
        )

        logger.info("Recorded %s — %s", node_id, outcome)
        return node_id

    def get_relevant_patterns(self, goal: str, limit: int = 5) -> list[dict]:
        """Retrieve patterns relevant to a goal using vector similarity."""
        return self.vectors.search(goal, top_k=limit)

    def get_recent_improvements(self, limit: int = 10) -> list[dict]:
        """Get recent improvement nodes from KG."""
        return self.kg.get_insights(limit=limit)
