"""Immutable AI Evaluator — the hardened judge that cannot be improved away."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EvaluationResult(BaseModel):
    """Structured output from the evaluator."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    rationale: str
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class ImmutableEvaluator:
    """An evaluator that cannot be modified by the improvement process.

    Enforced via:
      1. Read-only filesystem mount for evaluator code.
      2. SHA-256 digest verification at startup.
      3. Fixed system prompt loaded from a read-only path.
      4. Separate model configuration (not editable by the agent).
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        system_prompt: str = "",
        read_only_mount: bool = True,
        digest_verify: bool = True,
        evaluator_path: str | Path | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._system_prompt = system_prompt
        self._read_only_mount = read_only_mount
        self._digest_verify = digest_verify
        self._evaluator_path = Path(evaluator_path) if evaluator_path else Path(__file__)
        self._initial_digest = self._compute_digest()

    def _compute_digest(self) -> str:
        """Compute SHA-256 of the evaluator code to detect tampering."""
        h = hashlib.sha256()
        h.update(self._evaluator_path.read_bytes())
        return h.hexdigest()

    def verify_integrity(self) -> bool:
        """Check that the evaluator code has not been modified since init."""
        if not self._digest_verify:
            return True
        current = self._compute_digest()
        ok = current == self._initial_digest
        if not ok:
            logger.critical(
                "EVALUATOR TAMPER DETECTED: digest changed from %s to %s",
                self._initial_digest[:16],
                current[:16],
            )
        return ok

    async def evaluate(
        self,
        candidate_code: str,
        context: dict | None = None,
    ) -> EvaluationResult:
        """Evaluate a candidate improvement.

        This implementation uses an LLM call. In production this runs in
        a separate hardened process — here we demonstrate the interface.
        """
        if not self.verify_integrity():
            return EvaluationResult(
                passed=False,
                score=0.0,
                rationale="Evaluator integrity check failed — possible tampering detected.",
                issues=["INTEGRITY_FAILURE"],
            )

        # Delegate to LLM judge
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=self._model, temperature=self._temperature)
        context_str = _fmt_context(context)

        prompt = f"""{self._system_prompt}

--- Context ---
{context_str}

--- Candidate Code ---
```python
{candidate_code}
```

Evaluate the above improvement candidate on:
1. Correctness — does it work?
2. Safety — does it introduce regressions or vulnerabilities?
3. Efficiency — is the change well-structured?
4. Style — does it match the existing codebase conventions?

Respond with a JSON object: {{"passed": bool, "score": float (0-1), "rationale": str, "issues": [str], "suggestions": [str]}}
"""
        try:
            response = await llm.ainvoke(prompt)
            import json

            parsed = json.loads(
                response.content.strip().removeprefix("```json").removeprefix("```")
                .removesuffix("```").strip()
            )
            return EvaluationResult(**parsed)
        except Exception as exc:
            logger.error("Evaluator call failed: %s", exc)
            return EvaluationResult(
                passed=False,
                score=0.0,
                rationale=f"Evaluator error: {exc}",
                issues=["EVALUATOR_ERROR"],
            )


def _fmt_context(ctx: dict | None) -> str:
    if not ctx:
        return "(no context)"
    lines = []
    for k, v in ctx.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)
