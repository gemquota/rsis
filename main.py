"""RSIS — Recursive Self-Improvement System. Entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from rsis.config import RSISConfig
from rsis.core.checkpoint import CheckpointManager
from rsis.core.evaluator import ImmutableEvaluator
from rsis.core.l1_action import L1ActionLoop
from rsis.core.l2_improvement import L2ImprovementLoop
from rsis.core.l3_evolution import L3EvolutionLoop
from rsis.core.resource_enforcer import ResourceEnforcer
from rsis.memory.knowledge_graph import KnowledgeGraph
from rsis.memory.vector_store import VectorStore
from rsis.refinement.redundancy import RedundancyRefiner
from rsis.telemetry.collector import TelemetryCollector
from rsis.telemetry.extrapolator import Extrapolator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rsis")


class RSIS:
    """Top-level orchestrator for the Recursive Self-Improvement System."""

    def __init__(self, config: Optional[RSISConfig] = None) -> None:
        self.config = config or RSISConfig()
        self._shutdown = False

        # Phase 1: Core
        self.checkpoint = CheckpointManager(self.config.workspace_root)
        system_prompt = self._load_evaluator_prompt()
        self.evaluator = ImmutableEvaluator(
            model=self.config.evaluator.model,
            temperature=self.config.evaluator.temperature,
            system_prompt=system_prompt,
            read_only_mount=self.config.evaluator.read_only_mount,
            digest_verify=self.config.evaluator.digest_verify,
        )
        self.l1 = L1ActionLoop(
            max_steps=self.config.loops.l1_max_steps,
            step_timeout_s=self.config.loops.l1_step_timeout_s,
            checkpoint_callback=self.checkpoint.create_checkpoint,
        )
        self.l2 = L2ImprovementLoop(
            evaluator=self.evaluator,
            checkpoint_mgr=self.checkpoint,
            max_attempts=self.config.loops.l2_max_attempts,
            session_timeout_s=self.config.loops.l2_session_timeout_s,
        )

        # Phase 2: Memory
        self.kg = KnowledgeGraph(self.config.workspace_root / self.config.memory.kg_path)
        self.vector_store = VectorStore(self.config.workspace_root / self.config.memory.vector_persist_dir)
        self.l3 = L3EvolutionLoop(
            kg=self.kg,
            vector_store=self.vector_store,
            evaluator=self.evaluator,
            plateau_sessions=self.config.loops.l3_plateau_sessions,
        )

        # Phase 3: Telemetry & Refinement
        self.telemetry = TelemetryCollector(
            watch_paths=list(self.config.telemetry.watch_paths),
            ignore_patterns=list(self.config.telemetry.ignore_patterns),
            report_file=self.config.workspace_root / self.config.telemetry.report_file,
            flush_interval_s=self.config.telemetry.flush_interval_s,
        )
        self.extrapolator = Extrapolator(self.config.workspace_root / self.config.telemetry.report_file)

        # Phase 4: Hardening
        self.enforcer = ResourceEnforcer(
            max_memory_gb=self.config.resources.max_memory_rss_gb,
            disk_usage_pct=self.config.resources.disk_usage_pct,
            max_cpu_cores=self.config.resources.max_cpu_cores,
        )
        self.refiner = RedundancyRefiner(
            repo_path=self.config.workspace_root,
            kg=self.kg,
            vector_store=self.vector_store,
        )

        # Init checkpoint repo
        self.checkpoint.ensure_repo()

        logger.info(
            "RSIS initialized — session=%s workspace=%s",
            self.config.session_id[:8],
            self.config.workspace_root,
        )

    def _load_evaluator_prompt(self) -> str:
        path = self.config.evaluator_prompt_path
        if path.exists():
            return path.read_text().strip()
        logger.warning("Evaluator prompt not found at %s, using default", path)
        return "Evaluate the following code improvement for correctness, safety, and quality."

    async def start(self) -> None:
        """Start all background services."""
        await self.telemetry.start()
        self.enforcer.start()
        self.telemetry.record("system", {"event": "startup", "session": self.config.session_id})
        logger.info("RSIS running — dashboard at http://%s:%d",
                     self.config.dashboard.host, self.config.dashboard.port)

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._shutdown = True
        self.enforcer.stop()
        await self.telemetry.stop()
        self.telemetry.record("system", {"event": "shutdown"})
        await self.telemetry.flush()
        logger.info("RSIS shutdown complete")

    async def run_l1(self, task: str, context: dict | None = None) -> dict:
        """Execute a single L1 action loop."""
        self.telemetry.record("l1_task", {"task": task[:100]})
        result = await self.l1.run(task, context)
        self.telemetry.record("l1_result", {
            "success": result.success,
            "iterations": result.iterations,
            "duration_ms": result.total_duration_ms,
        })
        return result.model_dump()

    async def run_l2(self, goal: str, context: dict | None = None) -> dict:
        """Execute an L2 improvement session."""
        self.telemetry.record("l2_session", {"goal": goal[:100], "started": True})

        # Do a resource check first
        if self.enforcer.check_halt_flag("l2"):
            logger.warning("L2 blocked by resource enforcer — clearing flag")
            self.enforcer.clear_halt_flag("l2")

        result = await self.l2.run(goal, context)
        self.telemetry.record("l2_session", {
            "success": result.success,
            "attempts": len(result.attempts),
            "duration_ms": result.total_duration_ms,
        })

        # Feed result into L3
        self.l3.record_session(result)
        if self.l3.should_run:
            asyncio.create_task(self._run_l3())

        return result.model_dump()

    async def _run_l3(self) -> dict:
        """Execute an L3 evolution cycle."""
        self.telemetry.record("l3_cycle", {"started": True})
        result = await self.l3.run()
        self.telemetry.record("l3_cycle", {
            "sessions": result.sessions_consolidated,
            "kg_entities": result.kg_entities_added,
            "vectors": result.vectors_indexed,
            "strategies": len(result.strategies_derived),
        })

        # Check if redundancy refinement is due
        if result.redundancy_pruned:
            asyncio.create_task(self._run_refinement())

        return result.model_dump()

    async def _run_refinement(self) -> dict:
        """Execute a redundancy refinement cycle."""
        self.telemetry.record("refinement", {"started": True})
        report = await self.refiner.run()
        self.telemetry.record("refinement", report.to_dict())
        return report.to_dict()

    async def improve_self(self, goal: str) -> dict:
        """Full self-improvement pipeline: analyze → generate → validate → evolve.

        This is the primary entry point for recursive self-improvement.
        """
        # Phase 1: Analyze current state via L1
        context = {"goal": goal, "source": "rsis"}

        # Phase 2: Generate & validate improvement via L2
        l2_result = await self.run_l2(goal, context)

        # Phase 3: Evolve (if enough sessions accumulated)
        if self.l3.should_run:
            await self._run_l3()

        return {
            "goal": goal,
            "success": l2_result.get("success", False),
            "attempts": len(l2_result.get("attempts", [])),
            "summary": l2_result.get("summary", ""),
        }


async def main() -> None:
    """CLI entry point."""
    cfg = RSISConfig()
    rsis = RSIS(cfg)

    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(rsis.stop()))

    await rsis.start()

    # Launch dashboard in background
    from rsis.dashboard.server import create_app, serve as serve_dashboard
    import threading
    dash_thread = threading.Thread(
        target=serve_dashboard,
        args=(cfg,),
        kwargs={"port": cfg.dashboard.port},
        daemon=True,
    )
    dash_thread.start()

    logger.info("RSIS ready. Commands: improve_self('<goal>')")

    # Interactive loop for CLI
    try:
        while not rsis._shutdown:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await rsis.stop()


def run_cli():
    """Entry point for `rsis` console command."""
    asyncio.run(main())


if __name__ == "__main__":
    run_cli()
