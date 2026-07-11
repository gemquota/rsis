"""Knowledge Graph — entity-relationship store for structural memory.

Entities: Module, Function, Pattern, Strategy, Failure
Relations: DEPENDS_ON, IMPROVED_BY, CAUSED_FAILURE, RELATED_TO
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import networkx as nx

logger = logging.getLogger(__name__)

from rsis.core.l2_improvement import L2Result


class KnowledgeGraph:
    """Persistent knowledge graph backed by NetworkX + JSON serialization."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._graph: nx.DiGraph = nx.DiGraph()
        self._load()

    # --- Persistence ---

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._graph = nx.node_link_graph(data, edges="edges")
                logger.info("Loaded KG with %d nodes, %d edges", self._graph.number_of_nodes(), self._graph.number_of_edges())
            except Exception as exc:
                logger.warning("Failed to load KG, starting fresh: %s", exc)
                self._graph = nx.DiGraph()
        else:
            logger.info("No existing KG found, starting fresh")

    def save(self) -> None:
        data = nx.node_link_data(self._graph, edges="edges")
        self._path.write_text(json.dumps(data, indent=2, default=str))
        logger.debug("KG saved (%d nodes, %d edges)", self._graph.number_of_nodes(), self._graph.number_of_edges())

    # --- Entity management ---

    def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        self._graph.add_node(entity_id, type=entity_type, properties=properties or {})

    def add_relation(
        self,
        source: str,
        target: str,
        relation: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        self._graph.add_edge(source, target, relation=relation, properties=properties or {})

    def get_entity(self, entity_id: str) -> Optional[dict[str, Any]]:
        if entity_id not in self._graph:
            return None
        attrs = dict(self._graph.nodes[entity_id])
        return {"id": entity_id, **attrs}

    def query(
        self,
        entity_type: Optional[str] = None,
        relation: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        results = []
        for node, attrs in self._graph.nodes(data=True):
            if entity_type and attrs.get("type") != entity_type:
                continue
            results.append({"id": node, **attrs})
        if relation:
            filtered = []
            for r in results:
                edges = self._graph.edges(r["id"], data=True)
                if any(e[2].get("relation") == relation for e in edges):
                    filtered.append(r)
            results = filtered
        return results[:limit]

    def get_related(self, entity_id: str, relation: Optional[str] = None) -> list[dict[str, Any]]:
        related = []
        for _, neighbor, data in self._graph.edges(entity_id, data=True):
            if relation and data.get("relation") != relation:
                continue
            related.append({"id": neighbor, **dict(self._graph.nodes[neighbor]), "relation": data.get("relation")})
        return related

    # --- Consolidation from L2 results ---

    def consolidate_session(self, session_result: L2Result) -> int:
        """Extract entities and relations from an L2 session result."""
        count = 0
        for attempt in session_result.attempts:
            # Entity: each attempt
            attempt_id = f"attempt:{attempt.attempt_number}"
            self.add_entity(attempt_id, "ImprovementAttempt", {
                "branch": attempt.branch_name,
                "applied": attempt.applied,
                "duration_ms": attempt.duration_ms,
            })
            count += 1

            if attempt.evaluation:
                eval_id = f"eval:{attempt.attempt_number}"
                self.add_entity(eval_id, "Evaluation", {
                    "score": attempt.evaluation.score,
                    "passed": attempt.evaluation.passed,
                })
                self.add_relation(attempt_id, eval_id, "EVALUATED_BY")
                count += 1

                if attempt.evaluation.issues:
                    for issue in attempt.evaluation.issues:
                        issue_id = f"issue:{issue.lower().replace(' ', '_')}"
                        self.add_entity(issue_id, "Failure", {"description": issue})
                        self.add_relation(attempt_id, issue_id, "CAUSED_FAILURE")
                        count += 1

        self.save()
        return count
