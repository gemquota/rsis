"""L1 — Per-Task Action Loop.

The innermost loop: plan → execute tool calls → observe → retry/adapt.
Collects workspace telemetry and creates checkpoints before destructive ops.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from rsis.checkpoint import CheckpointManager
from rsis.config import CONFIG
from rsis.telemetry import TelemetryCollector, TelemetryEvent

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A single tool invocation within an L1 step."""
    name: str
    arguments: dict
    result: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class L1Result:
    """Outcome of an L1 loop execution."""
    success: bool
    steps_taken: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: Optional[str] = None
    final_output: Any = None


class L1ActionLoop:
    """Per-task action loop with checkpointing and telemetry."""

    def __init__(
        self,
        telemetry: TelemetryCollector,
        checkpoint_mgr: Optional[CheckpointManager] = None,
        tools: Optional[dict[str, Callable]] = None,
    ):
        self.config = CONFIG.l1
        self.telemetry = telemetry
        self.checkpoint = checkpoint_mgr or CheckpointManager(CONFIG.workspace_dir)
        self.tools = tools or {}
        self._task_description: str = ""

    def execute(self, task: str, context: Optional[dict] = None) -> L1Result:
        """Execute a task through the L1 loop.

        Steps:
        1. Record task start in telemetry
        2. Plan → tool call loop with retry
        3. Checkpoint before destructive operations
        4. Return result
        """
        self._task_description = task
        context = context or {}
        tool_calls: list[ToolCall] = []
        steps = 0

        logger.info("L1 executing task: %s", task[:80])

        self.telemetry.record(TelemetryEvent(
            event_type="l1_start",
            metadata={"task": task},
        ))

        for step_idx in range(self.config.max_tool_calls_per_step):
            steps = step_idx + 1
            logger.debug("L1 step %d/%d", step_idx + 1, self.config.max_tool_calls_per_step)

            # Determine which tool to call based on task
            tool_name, tool_args = self._plan_next_action(task, context, tool_calls)

            if tool_name is None:
                # Task is complete
                logger.info("L1 task complete after %d steps", steps)
                break

            # Execute tool call
            start = time.monotonic()
            call = self._execute_tool(tool_name, tool_args)
            call.duration_ms = int((time.monotonic() - start) * 1000)
            tool_calls.append(call)

            # Telemetry
            self.telemetry.record(TelemetryEvent(
                event_type="tool_call",
                path=tool_name,
                duration_ms=call.duration_ms,
                metadata={"error": call.error} if call.error else None,
            ))

            if call.error:
                logger.warning("Tool call failed: %s — %s", tool_name, call.error)
                # Checkpoint on failure so we can rollback
                if CONFIG.checkpoint_before_mutation:
                    self.checkpoint.checkpoint(f"after-tool-failure-{tool_name}")

            # Update context with result
            context["last_result"] = call.result
            context["last_error"] = call.error

        success = not any(c.error for c in tool_calls)

        self.telemetry.record(TelemetryEvent(
            event_type="l1_complete",
            duration_ms=sum(c.duration_ms for c in tool_calls),
            metadata={"success": success, "steps": steps},
        ))

        return L1Result(
            success=success,
            steps_taken=steps,
            tool_calls=tool_calls,
            final_output=context.get("last_result"),
        )

    def _plan_next_action(
        self, task: str, context: dict, previous_calls: list[ToolCall]
    ) -> tuple[Optional[str], dict]:
        """Decide the next tool to call based on task and prior results.

        In production this would use an LLM to plan. This stub uses a simple
        keyword router for demonstration.
        """
        if not self.tools:
            return None, {}

        # If the last call failed, try a simpler approach
        if previous_calls and previous_calls[-1].error:
            logger.info("Retrying after failure...")
            return "retry", {"previous_error": previous_calls[-1].error}

        # Simple keyword routing for demo purposes
        task_lower = task.lower()
        for tool_name in self.tools:
            if tool_name in task_lower:
                return tool_name, {"task": task, **context}

        # Default: use first tool
        first_tool = next(iter(self.tools))
        return first_tool, {"task": task, **context}

    def _execute_tool(self, name: str, args: dict) -> ToolCall:
        """Execute a single tool call."""
        if name == "retry":
            return ToolCall(name="retry", arguments=args, result=None)
        if name == "noop":
            return ToolCall(name="noop", arguments=args, result=None)

        handler = self.tools.get(name)
        if not handler:
            return ToolCall(name=name, arguments=args, error=f"Unknown tool: {name}")

        try:
            result = handler(**args)
            return ToolCall(name=name, arguments=args, result=result)
        except Exception as e:
            return ToolCall(name=name, arguments=args, error=str(e))
