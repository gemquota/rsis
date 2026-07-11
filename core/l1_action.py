"""L1 — Per-task Action Loop: tool calls, observations, retries."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    error: str | None = None
    duration_ms: float = 0.0


class L1Result(BaseModel):
    """Outcome of a single L1 action loop execution."""

    success: bool
    output: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    total_duration_ms: float = 0.0
    iterations: int = 0
    error: str | None = None


ToolRegistry = dict[str, Callable[..., Any]]


class L1ActionLoop:
    """Lowest-level loop: plan → execute → observe → retry/adapt.

    Operates within strict budget limits and timeouts.
    Checkpoints are created before any destructive action.
    """

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        max_steps: int = 10,
        step_timeout_s: float = 120.0,
        checkpoint_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self._tools = tools or {}
        self._max_steps = max_steps
        self._step_timeout_s = step_timeout_s
        self._checkpoint_cb = checkpoint_callback

    async def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> L1Result:
        """Execute a single task through the action loop."""
        start = time.monotonic()
        calls: list[ToolCall] = []
        step = 0
        output = ""
        error: str | None = None

        plan = await self._plan(task, context)

        while step < self._max_steps:
            step += 1
            logger.debug("L1 step %d/%d: %s", step, self._max_steps, task[:80])

            # Determine next action
            action = await self._decide(plan, calls, task)
            if action.get("type") == "complete":
                output = action.get("output", "")
                break
            if action.get("type") == "error":
                error = action.get("error", "Unknown error")
                break

            tool_name = action.get("tool", "")
            tool_args = action.get("args", {})
            tool_result = ToolCall(tool=tool_name, args=tool_args)

            t0 = time.monotonic()
            try:
                async with asyncio.timeout(self._step_timeout_s):
                    handler = self._tools.get(tool_name)
                    if handler is None:
                        raise ValueError(f"Unknown tool: {tool_name}")

                    # checkpoint before destructive tools
                    if tool_name in ("write_file", "delete_file", "install") and self._checkpoint_cb:
                        self._checkpoint_cb(f"pre-{tool_name}")

                    result = await handler(**tool_args) if asyncio.iscoroutinefunction(handler) else handler(**tool_args)
                    tool_result.result = str(result) if result is not None else ""
            except asyncio.TimeoutError:
                tool_result.error = f"Timeout after {self._step_timeout_s}s"
                logger.warning("L1 step %d timed out", step)
            except Exception as exc:
                tool_result.error = str(exc)
                logger.warning("L1 step %d error: %s", step, exc)
            finally:
                tool_result.duration_ms = (time.monotonic() - t0) * 1000

            calls.append(tool_result)

            if tool_result.error:
                # retry logic — exponential backoff
                await asyncio.sleep(0.5 * (2 ** (step - 1)))

        total = (time.monotonic() - start) * 1000
        return L1Result(
            success=error is None,
            output=output or task,
            tool_calls=calls,
            total_duration_ms=total,
            iterations=step,
            error=error,
        )

    async def _plan(self, task: str, context: dict | None) -> str:
        """Generate an initial plan for the task."""
        ctx = context or {}
        tools_desc = "\n".join(f"  - {name}: {fn.__doc__ or 'no doc'}" for name, fn in self._tools.items())
        prompt = f"""You are an autonomous agent. Given a task and available tools, create a simple step-by-step plan.

Available tools:
{tools_desc}

Task: {task}

Context: {ctx}

Return a numbered list of steps using available tools."""
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
        response = await llm.ainvoke(prompt)
        return str(response.content)

    async def _decide(
        self,
        plan: str,
        calls: list[ToolCall],
        task: str,
    ) -> dict:
        """Decide the next action based on the plan and prior results."""
        history = "\n".join(
            f"  Step {i+1}: {c.tool}({c.args}) → {'OK' if not c.error else c.error}"
            for i, c in enumerate(calls)
        )
        prompt = f"""You are executing a plan step by step.

Plan:
{plan}

Completed steps:
{history}

Task: {task}

Decide the next action. Respond as JSON:
- To call a tool: {{"type": "tool", "tool": "...", "args": {{...}}}}
- If task is complete: {{"type": "complete", "output": "..."}}
- If task cannot proceed: {{"type": "error", "error": "..."}}"""
        from langchain_openai import ChatOpenAI
        import json
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
        response = await llm.ainvoke(prompt)
        content = response.content.strip()
        # Strip markdown fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"type": "error", "error": f"Failed to parse decision: {content[:100]}"}
