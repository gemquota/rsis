"""L2 — Per-Session Improvement Loop: codegen, evaluation, checkpointing."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from rsis.core.checkpoint import CheckpointManager
from rsis.core.evaluator import EvaluationResult, ImmutableEvaluator
from rsis.core.l1_action import L1Result

logger = logging.getLogger(__name__)


class ImprovementAttempt(BaseModel):
    """A single L2 improvement cycle."""

    attempt_number: int
    branch_name: str
    candidate_code: str
    diff: str
    evaluation: Optional[EvaluationResult] = None
    applied: bool = False
    duration_ms: float = 0.0


class L2Result(BaseModel):
    """Outcome of a full L2 improvement session."""

    success: bool
    attempts: list[ImprovementAttempt] = Field(default_factory=list)
    total_duration_ms: float = 0.0
    total_tokens: int = 0
    summary: str = ""


class L2ImprovementLoop:
    """Mid-level loop: generates improvements and validates via evaluator.

    Each attempt:
      1. Creates an experiment branch via CheckpointManager.
      2. Generates a candidate improvement (codegen).
      3. Evaluates via ImmutableEvaluator.
      4. On PASS: merges the branch. On FAIL: discards and retries.
    """

    def __init__(
        self,
        *,
        evaluator: ImmutableEvaluator,
        checkpoint_mgr: CheckpointManager,
        max_attempts: int = 5,
        session_timeout_s: float = 1800.0,
        improvement_generator: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._evaluator = evaluator
        self._checkpoint = checkpoint_mgr
        self._max_attempts = max_attempts
        self._session_timeout_s = session_timeout_s
        self._generator = improvement_generator
        self._session_id = f"l2-{int(time.time())}"

    async def run(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
        l1_results: list[L1Result] | None = None,
    ) -> L2Result:
        """Execute a full improvement session."""
        start = time.monotonic()
        attempts: list[ImprovementAttempt] = []
        overall_success = False

        for attempt_num in range(1, self._max_attempts + 1):
            if time.monotonic() - start > self._session_timeout_s:
                logger.warning("L2 session timeout after attempt %d", attempt_num)
                break

            attempt_start = time.monotonic()
            branch = f"{self._session_id}/attempt-{attempt_num:02d}"

            try:
                # Phase 1: Checkpoint & branch
                self._checkpoint.create_experiment_branch(branch)

                # Phase 2: Generate improvement
                candidate, diff = await self._generate_improvement(goal, context, attempt_num)
                if candidate is None:
                    logger.info("No improvement generated on attempt %d", attempt_num)
                    self._checkpoint.rollback()
                    continue

                attempt = ImprovementAttempt(
                    attempt_number=attempt_num,
                    branch_name=branch,
                    candidate_code=candidate,
                    diff=diff or "(new file)",
                )

                # Phase 3: Evaluate
                eval_context = {
                    "goal": goal,
                    "attempt": attempt_num,
                    "l1_results": len(l1_results or []),
                    "session_id": self._session_id,
                }
                evaluation = await self._evaluator.evaluate(candidate, eval_context)
                attempt.evaluation = evaluation
                attempt.duration_ms = (time.monotonic() - attempt_start) * 1000

                # Phase 4: Accept or reject
                if evaluation.passed and evaluation.score >= 0.6:
                    self._checkpoint.merge_experiment(branch, f"RSIS L2 improvement: {goal[:60]}")
                    attempt.applied = True
                    overall_success = True
                    logger.info(
                        "L2 attempt %d ACCEPTED (score=%.2f): %s",
                        attempt_num,
                        evaluation.score,
                        evaluation.rationale[:100],
                    )
                    attempts.append(attempt)
                    break  # success — exit loop
                else:
                    self._checkpoint.rollback()
                    logger.info(
                        "L2 attempt %d REJECTED (score=%.2f): %s",
                        attempt_num,
                        evaluation.score,
                        evaluation.rationale[:100],
                    )
                    attempts.append(attempt)

            except Exception as exc:
                logger.error("L2 attempt %d failed: %s", attempt_num, exc)
                self._checkpoint.rollback()
                continue

        total = (time.monotonic() - start) * 1000
        accepted = [a for a in attempts if a.applied]
        return L2Result(
            success=overall_success,
            attempts=attempts,
            total_duration_ms=total,
            summary=(
                f"Accepted {len(accepted)}/{len(attempts)} attempts"
                f" ({'success' if overall_success else 'failed'})"
            ),
        )

    async def _generate_improvement(
        self,
        goal: str,
        context: dict | None,
        attempt_num: int,
    ) -> tuple[Optional[str], str]:
        """Generate a code improvement candidate using an LLM.

        On attempt_num > 1, include prior failure feedback.
        """
        ctx = context or {}
        feedback = ""
        if attempt_num > 1:
            feedback = "\nPrevious attempts failed. Improve based on prior evaluation feedback."

        prompt = f"""You are an autonomous code improvement system. Generate a Python improvement.

Improvement goal: {goal}

Workspace context: {ctx}
{feedback}

Rules:
1. Generate a single Python improvement (file content or diff).
2. The code must be syntactically valid Python.
3. Use existing patterns in the codebase.
4. Keep changes minimal and focused.

Respond with exactly:
--FILE: path/to/file.py
<code>
--END

Or if no improvement is needed:
--NO-CHANGE--"""
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
        response = await llm.ainvoke(prompt)
        content = str(response.content).strip()

        if "--NO-CHANGE--" in content:
            return None, ""

        if "--FILE:" in content:
            parts = content.split("--FILE:", 1)[1]
            file_line = parts.split("\n", 1)
            filename = file_line[0].strip()
            code = file_line[1] if len(file_line) > 1 else ""
            code = code.split("--END")[0].strip()
            # Write the file
            target = self._checkpoint._repo_path / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code)
            return code, f"{filename}: {len(code)} chars"

        return content, "(inline generation)"
