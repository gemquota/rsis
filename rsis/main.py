#!/usr/bin/env python3
"""RSIS — Recursive Self-Improvement System.

Usage:
    python -m rsis init          # Initialise the workspace for RSIS
    python -m rsis run           # Run one improvement session
    python -m rsis evolve        # Run one L3 evolution cycle
    python -m rsis status        # Show system status
"""

import argparse
import logging
import sys
from pathlib import Path

from rsis import __version__
from rsis.checkpoint import CheckpointManager
from rsis.config import CONFIG
from rsis.evaluator import EvaluatorClient
from rsis.loop_l1 import L1ActionLoop
from rsis.loop_l2 import L2ImprovementLoop
from rsis.loop_l3 import L3EvolutionLoop
from rsis.memory import MemoryManager
from rsis.telemetry import TelemetryCollector, WorkspaceMonitor


def setup_logging() -> None:
    log_path = CONFIG.log_file
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    handlers = [logging.StreamHandler(sys.stdout)]
    if log_path:
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_init(args: argparse.Namespace) -> int:
    """Initialise the workspace for RSIS."""
    print(f"RSIS v{__version__} — Initialising workspace...")
    print(f"  Workspace: {CONFIG.workspace_dir}")

    # Ensure directories
    for d in [".rsis", ".rsis/telemetry", ".rsis/vectors"]:
        Path(CONFIG.workspace_dir, d).mkdir(parents=True, exist_ok=True)

    # Initialise git repo
    checkpoint = CheckpointManager(CONFIG.workspace_dir)
    checkpoint.ensure_repo()

    # Create initial checkpoint
    ch = checkpoint.checkpoint("rsis-initialised")
    if ch:
        print(f"  Initial checkpoint: {ch[:12]}")
    else:
        print("  No changes to checkpoint (clean repo)")

    # Verify evaluator exists
    eval_path = Path(CONFIG.evaluator.evaluator_path)
    if eval_path.exists():
        print(f"  Evaluator: {eval_path.resolve()}")
    else:
        print(f"  WARNING: Evaluator not found at {eval_path}")

    print("  RSIS workspace ready.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run one improvement session."""
    print(f"RSIS v{__version__} — Running improvement session")

    telemetry = TelemetryCollector(
        CONFIG.telemetry_dir, CONFIG.telemetry_flush_interval_s
    )
    telemetry.start()

    try:
        evaluator = EvaluatorClient()
        checkpoint = CheckpointManager(CONFIG.workspace_dir)
        memory = MemoryManager(CONFIG.workspace_dir)
        l2 = L2ImprovementLoop(
            telemetry=telemetry, evaluator=evaluator,
            checkpoint_mgr=checkpoint,
        )

        goal = args.goal or "self-improve the codebase"
        result = l2.run_session(goal)

        # Record in memory
        if result.applied:
            memory.record_improvement(
                description=result.applied.description,
                target_files=result.applied.target_files,
                eval_scores=result.eval_results[-1].scores if result.eval_results else {},
                outcome="applied",
            )
            print(f"  ✓ Improvement applied after {result.attempts} attempt(s)")
        else:
            print(f"  ✗ No improvement applied after {result.attempts} attempt(s)")
            if result.eval_results:
                last = result.eval_results[-1]
                print(f"    Last evaluator: {last.decision} — {last.rationale}")

        # Also run L1 for the goal
        l1 = L1ActionLoop(telemetry=telemetry, checkpoint_mgr=checkpoint)
        l1_result = l1.execute(goal)
        print(f"  L1 steps: {l1_result.steps_taken}")

    finally:
        telemetry.stop()

    return 0


def cmd_evolve(args: argparse.Namespace) -> int:
    """Run one L3 evolution cycle."""
    print(f"RSIS v{__version__} — Running L3 evolution cycle")

    telemetry = TelemetryCollector(
        CONFIG.telemetry_dir, CONFIG.telemetry_flush_interval_s
    )
    telemetry.start()

    try:
        memory = MemoryManager(CONFIG.workspace_dir)
        l3 = L3EvolutionLoop(telemetry=telemetry, memory=memory)

        result = l3.run_cycle()
        if result.success:
            print(f"  ✓ Evolution complete")
            print(f"  Sessions analysed: {result.sessions_analysed}")
            print(f"  Strategies evolved: {len(result.strategies_evolved)}")
        else:
            print(f"  ✗ Evolution failed: {result.error}")
    finally:
        telemetry.stop()

    return 0


def _fmt(val: object, unit: str = "") -> str:
    """Format an optional value for display."""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.1f}{unit}"
    return f"{val}{unit}"


def cmd_status(args: argparse.Namespace) -> int:
    """Show system status."""
    print(f"RSIS v{__version__}")
    print(f"  Workspace: {CONFIG.workspace_dir}")

    # Git status
    checkpoint = CheckpointManager(CONFIG.workspace_dir)
    if Path(CONFIG.workspace_dir, ".git").exists():
        print("  Git repo: initialised")
        latest = checkpoint.latest_checkpoint()
        if latest:
            print(f"  Latest checkpoint: {latest[:12]}")
    else:
        print("  Git repo: not initialised (run 'rsis init')")

    # Memory
    memory = MemoryManager(CONFIG.workspace_dir)
    insights = memory.kg.get_insights(limit=3)
    print(f"  Knowledge graph: {len(memory.kg._nodes)} nodes")
    print(f"  Vector store: {len(memory.vectors._documents)} documents")
    print(f"  Recent insights: {len(insights)}")

    # Telemetry
    telemetry_dir = Path(CONFIG.telemetry_dir)
    if telemetry_dir.exists():
        files = list(telemetry_dir.glob("*.jsonl"))
        print(f"  Telemetry files: {len(files)}")

    # Resource monitor
    monitor = WorkspaceMonitor()
    print(f"  CPU usage: {_fmt(monitor.cpu_usage(), '%')}")
    print(f"  Memory: {_fmt(monitor.memory_usage_mb(), ' MB')}")
    print(f"  Disk: {_fmt(monitor.disk_usage_pct(CONFIG.workspace_dir), '%')}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RSIS — Recursive Self-Improvement System",
    )
    parser.add_argument("--version", action="version", version=f"RSIS {__version__}")

    sub = parser.add_subparsers(dest="command", help="Sub-commands")

    p_init = sub.add_parser("init", help="Initialise the workspace")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="Run one improvement session")
    p_run.add_argument("--goal", "-g", default="self-improve the codebase",
                       help="Improvement goal")
    p_run.set_defaults(func=cmd_run)

    p_evolve = sub.add_parser("evolve", help="Run one L3 evolution cycle")
    p_evolve.set_defaults(func=cmd_evolve)

    p_status = sub.add_parser("status", help="Show system status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    setup_logging()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
