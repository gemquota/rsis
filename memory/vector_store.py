"""Vector Store — semantic similarity search over improvements & patterns.

Lightweight in-memory implementation using numpy for cosine similarity.
Persists to JSON. No external vector database dependency required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _simple_embed(text: str, dim: int = 128) -> np.ndarray:
    """A statistically-biased hash-based embedding for lightweight similarity.

    Not as good as a real LLM embedding model, but sufficient for
    deduplication and rough similarity search within RSIS patterns.
    Replace with a proper embedding model for production use.
    """
    vec = np.zeros(dim, dtype=np.float32)
    words = text.split()
    for i, word in enumerate(words):
        h = hashlib.sha256(word.encode()).digest()
        for j in range(min(len(h), dim)):
            vec[j] += (h[j] / 255.0) * (1.0 / (1.0 + i * 0.01))
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


class VectorStore:
    """Lightweight in-memory vector store with JSON persistence."""

    def __init__(self, persist_dir: str | Path) -> None:
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._data_path = self._persist_dir / "vectors.json"
        self._documents: dict[str, dict[str, Any]] = {}
        self._embeddings: dict[str, np.ndarray] = {}
        self._load()

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text())
                for doc_id, entry in data.items():
                    self._documents[doc_id] = {
                        "code": entry["code"],
                        "metadata": entry.get("metadata", {}),
                    }
                    self._embeddings[doc_id] = np.array(entry["embedding"], dtype=np.float32)
                logger.info("Loaded %d vectors from %s", len(self._documents), self._data_path)
            except Exception as exc:
                logger.warning("Failed to load vectors, starting fresh: %s", exc)
                self._documents = {}
                self._embeddings = {}

    def _save(self) -> None:
        data = {}
        for doc_id in self._documents:
            data[doc_id] = {
                "code": self._documents[doc_id]["code"],
                "metadata": self._documents[doc_id].get("metadata", {}),
                "embedding": self._embeddings[doc_id].tolist(),
            }
        self._data_path.write_text(json.dumps(data, indent=2))
        logger.debug("Saved %d vectors", len(data))

    def index_pattern(
        self,
        code: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Index a code pattern for similarity retrieval. Returns doc ID."""
        doc_id = hashlib.sha256(code.encode()).hexdigest()[:16]
        if doc_id in self._documents:
            return doc_id

        embedding = _simple_embed(code)
        self._documents[doc_id] = {
            "code": code,
            "metadata": metadata or {},
        }
        self._embeddings[doc_id] = embedding
        self._save()
        logger.debug("Indexed pattern %s (%d chars)", doc_id, len(code))
        return doc_id

    def search(
        self,
        query: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar patterns by semantic query (cosine similarity)."""
        if not self._documents:
            return []

        query_emb = _simple_embed(query)
        scores = []
        for doc_id, emb in self._embeddings.items():
            # Cosine similarity
            dot = float(np.dot(query_emb, emb))
            scores.append((dot, doc_id))

        scores.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, doc_id in scores[:n_results]:
            doc = self._documents[doc_id]
            results.append({
                "id": doc_id,
                "score": round(float(score), 4),
                "code": doc["code"],
                "metadata": doc.get("metadata", {}),
            })
        return results

    def search_by_code(self, code_snippet: str, n_results: int = 3) -> list[dict[str, Any]]:
        return self.search(code_snippet, n_results=n_results)

    def count(self) -> int:
        return len(self._documents)

    def delete_pattern(self, doc_id: str) -> None:
        if doc_id in self._documents:
            del self._documents[doc_id]
            if doc_id in self._embeddings:
                del self._embeddings[doc_id]
            self._save()
            logger.debug("Deleted pattern %s", doc_id)

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {"id": doc_id, "code": doc["code"], "metadata": doc.get("metadata", {})}
            for doc_id, doc in self._documents.items()
        ]
