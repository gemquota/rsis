"""L3 — Cross-Session Evolution Loop: memory consolidation & strategy evolution."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from rsis.core.evaluator import ImmutableEvaluator
from rsis.core.l2_improvement import L2Result
from rsis.memory.knowledge_graph import KnowledgeGraph
from rsis.memory.vector_store import VectorStore
from rsis.telemetry.extrapolator import Extrapolator

logger = logging.getLogger(__name__)


class StrategyInsight(BaseModel):
    """A meta-strategy derived from session history."""

    insight: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_sessions: list[str] = Field(default_factory=list)
    applicable_loops: list[str] = Field(default_factory=list)


class L3Result(BaseModel):
    """Outcome of an L3 evolution cycle."""

    sessions_consolidated: int = 0
    kg_entities_added: int = 0
    vectors_indexed: int = 0
    strategies_derived: list[StrategyInsight] = Field(default_factory=list)
    redundancy_pruned: bool = False
    duration_ms: float = 0.0


class L3EvolutionLoop:
    """Highest-level loop: consolidates cross-session memory and evolves strategies.

    Runs on a schedule or plateau detection:
      - Consolidates L2 results into the knowledge graph.
      - Indexes patterns into vector store.
      - Derives meta-strategies for improvement.
      - Triggers redundancy refinement.
    """

    def __init__(
        self,
        *,
        kg: KnowledgeGraph,
        vector_store: VectorStore,
        evaluator: ImmutableEvaluator,
        extrapolator: Optional[Extrapolator] = None,
        plateau_sessions: int = 20,
    ) -> None:
        self._kg = kg
        self._vector_store = vector_store
        self._evaluator = evaluator
        self._extrapolator = extrapolator
        self._plateau_sessions = plateau_sessions
        self._session_history: list[L2Result] = []

    def record_session(self, result: L2Result) -> None:
        """Record an L2 result for future consolidation."""
        self._session_history.append(result)
        logger.debug("L3 recorded session %d/%d", len(self._session_history), self._plateau_sessions)

    @property
    def should_run(self) -> bool:
        """Check if an evolution cycle is due."""
        return len(self._session_history) >= self._plateau_sessions

    async def run(self) -> L3Result:
        """Execute one evolution cycle."""
        start = time.monotonic()
        result = L3Result()

        if not self._session_history:
            logger.info("L3: no sessions to consolidate")
            return result

        # 1. Consolidate into knowledge graph
        for session_result in self._session_history:
            entities = self._kg.consolidate_session(session_result)
            result.kg_entities_added += entities

        # 2. Index successful patterns into vector store
        for session_result in self._session_history:
            if session_result.success:
                for attempt in session_result.attempts:
                    if attempt.applied and attempt.candidate_code:
                        self._vector_store.index_pattern(
                            code=attempt.candidate_code,
                            metadata={
                                "goal": session_result.summary,
                                "score": attempt.evaluation.score if attempt.evaluation else 0.0,
                            },
                        )
                        result.vectors_indexed += 1

        # 3. Derive meta-strategies
        strategies = await self._derive_strategies()
        result.strategies_derived = strategies

        # 4. Check for plateau and trigger refinement
        if self._extrapolator:
            trends = self._extrapolator.analyze_trends()
            if trends.get("plateau_detected", False):
                result.redundancy_pruned = True
                logger.info("L3: plateau detected, flagging for redundancy refinement")

        result.sessions_consolidated = len(self._session_history)
        result.duration_ms = (time.monotonic() - start) * 1000
        self._session_history.clear()

        logger.info(
            "L3 cycle complete: %d sessions, %d KG entities, %d vectors, %d strategies",
            result.sessions_consolidated,
            result.kg_entities_added,
            result.vectors_indexed,
            len(result.strategies_derived),
        )
        return result

    async def _derive_strategies(self) -> list[StrategyInsight]:
        """Analyze session history to derive improvement strategies."""
        if not self._session_history:
            return []

        session_summaries = "\n".join(
            f"- Session {i}: {'success' if s.success else 'fail'} — {s.summary}"
            for i, s in enumerate(self._session_history[-10:])  # last 10
        )

        prompt = f"""Analyze these L2 improvement sessions and derive meta-strategies.

Recent sessions:
{session_summaries}

Identify 1-3 actionable strategies that could improve future L2 attempts.
For each, state:
- The insight
- Your confidence (0-1)
- Which loops it applies to (L1, L2, L3)

Respond as JSON list: [{{"insight": "...", "confidence": 0.0, "source_sessions": [...], "applicable_loops": [...]}}]
"""
        from langchain_openai import ChatOpenAI
        import json

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
        response = await llm.ainvoke(prompt)
        content = response.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(content)
            return [StrategyInsight(**item) for item in data]
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Failed to parse strategies: %s", exc)
            return []
