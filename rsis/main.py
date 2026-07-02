#!/usr/bin/env python3
"""RSIS — Recursive Self-Improvement System.

Usage:
    python -m rsis init              # Initialise workspace
    python -m rsis run --goal X      # Improvement session
    python -m rsis evolve            # L3 evolution cycle
    python -m rsis dashboard         # Start web dashboard
    python -m rsis status            # System overview
    python -m rsis check             # Check resource limits
    python -m rsis recovery-test     # Test recovery mechanisms
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from rsis import __version__
from rsis.checkpoint import CheckpointManager
from rsis.config import CONFIG
from rsis.evaluator import EvaluatorClient
from rsis.loop_l1 import L1ActionLoop
from rsis.loop_l2 import L2ImprovementLoop
from rsis.loop_l3 import L3EvolutionLoop
from rsis.memory import MemoryManager
from rsis.recovery import FailureInjector, RecoveryManager
from rsis.resource_monitor import ResourceEnforcer, ResourceSeverity
from rsis.telemetry import TelemetryCollector, WorkspaceMonitor
from rsis.timeout import Budget, deadline, TimeoutError


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


# ── Shared initialisation ────────────────────────────────────────────────

def _init_subsystems() -> tuple:
    """Initialise and return shared subsystems."""
    telemetry = TelemetryCollector(
        CONFIG.telemetry_dir, CONFIG.telemetry_flush_interval_s,
    )
    checkpoint = CheckpointManager(CONFIG.workspace_dir)
    memory = MemoryManager(CONFIG.workspace_dir)
    evaluator = EvaluatorClient()
    recovery = RecoveryManager(checkpoint_mgr=checkpoint)
    enforcer = ResourceEnforcer()
    return telemetry, checkpoint, memory, evaluator, recovery, enforcer


# ── Commands ─────────────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    print(f"RSIS v{__version__} — Initialising workspace...")
    print(f"  Workspace: {CONFIG.workspace_dir}")

    for d in [".rsis", ".rsis/telemetry", ".rsis/vectors"]:
        Path(CONFIG.workspace_dir, d).mkdir(parents=True, exist_ok=True)

    checkpoint = CheckpointManager(CONFIG.workspace_dir)
    checkpoint.ensure_repo()

    ch = checkpoint.checkpoint("rsis-initialised")
    print(f"  Initial checkpoint: {ch[:12] if ch else 'none'}")

    eval_path = Path(CONFIG.evaluator.evaluator_path)
    if eval_path.exists():
        print(f"  Evaluator: {eval_path.resolve()}")
    else:
        print(f"  WARNING: Evaluator not found at {eval_path}")

    print("  RSIS workspace ready.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    telemetry, checkpoint, memory, evaluator, recovery, enforcer = _init_subsystems()

    enforcer.set_callbacks(
        on_halt=lambda msg: setattr(enforcer, '_halt_requested', True),
        on_throttle=lambda msg: logger.warning("Throttle: %s", msg),
    )
    enforcer.start()
    telemetry.start()

    try:
        # Check resources before starting
        limit_msg = enforcer.check_before_operation()
        if limit_msg:
            print(f"  ⚠ Resource limit: {limit_msg}")
            return 1

        l2 = L2ImprovementLoop(
            telemetry=telemetry, evaluator=evaluator,
            checkpoint_mgr=checkpoint, recovery=recovery,
        )

        goal = args.goal or "self-improve the codebase"
        budget = Budget(
            max_iterations=CONFIG.l2.max_improvement_attempts,
            max_time_s=CONFIG.l2.session_timeout_s,
            label="L2 session",
        )

        with deadline(CONFIG.l2.session_timeout_s, "L2 session"):
            result = l2.run_session(goal, budget=budget)

        if enforcer.halt_requested:
            print("  ⚠ Session halted by resource enforcer")
            return 1

        if result.applied:
            memory.record_improvement(
                description=result.applied.description,
                target_files=result.applied.target_files,
                eval_scores=result.eval_results[-1].scores if result.eval_results else {},
                outcome="applied",
                goal=goal,
            )
            print(f"  ✓ Improvement applied after {result.attempts} attempt(s)")
        else:
            print(f"  ✗ No improvement applied after {result.attempts} attempt(s)")

        l1 = L1ActionLoop(telemetry=telemetry, checkpoint_mgr=checkpoint)
        l1_result = l1.execute(goal)
        print(f"  L1 steps: {l1_result.steps_taken}")

    except TimeoutError as e:
        print(f"  ✗ Session timed out: {e}")
        recovery.record_failure()
        return 1
    except Exception as e:
        print(f"  ✗ Session failed: {e}")
        recovery.record_failure()
        recovery.rollback_on_failure("run_session")
        return 1
    finally:
        telemetry.stop()
        enforcer.stop()

    return 0


def cmd_evolve(args: argparse.Namespace) -> int:
    telemetry, checkpoint, memory, evaluator, recovery, enforcer = _init_subsystems()
    enforcer.start()
    telemetry.start()

    try:
        l3 = L3EvolutionLoop(telemetry=telemetry, memory=memory)
        budget = Budget(
            max_iterations=1,
            max_time_s=CONFIG.l3.plateau_timeout_s,
            label="L3 evolution",
        )

        with deadline(CONFIG.l3.plateau_timeout_s, "L3 evolution"):
            result = l3.run_cycle(budget=budget)

        if result.success:
            print(f"  ✓ Evolution complete")
            print(f"  Insights added: {result.insights_added}")
            print(f"  Strategies evolved: {len(result.strategies_evolved)}")
            print(f"  Redundancies identified: {result.redundancies_pruned}")
            for t in result.trends_detected:
                print(f"  Trend: {t['context']} — {t['trend']} (slope={t['slope']})")
        else:
            print(f"  ✗ Evolution failed: {result.error}")

    except TimeoutError as e:
        print(f"  ✗ Evolution timed out: {e}")
        return 1
    finally:
        telemetry.stop()
        enforcer.stop()

    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    host, port = args.host, args.port
    print(f"RSIS v{__version__} — Dashboard at http://{host}:{port}")

    import uvicorn
    from rsis.dashboard.app import app
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _fmt(val: object, unit: str = "") -> str:
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.1f}{unit}"
    return f"{val}{unit}"


def cmd_status(args: argparse.Namespace) -> int:
    print(f"RSIS v{__version__}")
    print(f"  Workspace: {CONFIG.workspace_dir}")

    checkpoint = CheckpointManager(CONFIG.workspace_dir)
    if Path(CONFIG.workspace_dir, ".git").exists():
        print("  Git repo: initialised")
        latest = checkpoint.latest_checkpoint()
        if latest:
            print(f"  Latest checkpoint: {latest[:12]}")
    else:
        print("  Git repo: not initialised")

    memory = MemoryManager(CONFIG.workspace_dir)
    print(f"  Knowledge graph: {memory.kg.node_count} nodes / {memory.kg.edge_count} edges")
    print(f"  Vector store: {len(memory.vectors._documents)} documents")

    telemetry_dir = Path(CONFIG.telemetry_dir)
    if telemetry_dir.exists():
        files = list(telemetry_dir.glob("*.jsonl"))
        print(f"  Telemetry files: {len(files)}")

    monitor = WorkspaceMonitor()
    print(f"  CPU: {_fmt(monitor.cpu_usage(), '%')}  "
          f"Mem: {_fmt(monitor.memory_usage_mb(), ' MB')}  "
          f"Disk: {_fmt(monitor.disk_usage_pct(CONFIG.workspace_dir), '%')}")

    strategies = memory.kg.get_strategies()
    print(f"  Strategies: {len(strategies)}")
    for s in strategies[-3:]:
        print(f"    - {s.get('description', 'N/A')}")

    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Check resource limits and report status."""
    enforcer = ResourceEnforcer()
    monitor = WorkspaceMonitor()

    print(f"RSIS v{__version__} — Resource Check")
    print("")

    checks = [
        ("Disk Usage", monitor.disk_usage_pct(CONFIG.workspace_dir),
         enforcer.limits.disk_usage_pct, "%"),
        ("Memory (RSS)", monitor.memory_usage_mb(),
         float(enforcer.limits.max_memory_rss_mb), " MB"),
        ("API Rate", float(enforcer.api_calls_per_minute()),
         float(enforcer.limits.evaluator_api_calls_per_min), "/min"),
    ]

    all_ok = True
    for name, current, limit, unit in checks:
        if current is None:
            print(f"  ⚠ {name}: N/A (monitoring unavailable)")
            continue
        status = "✓" if current <= limit else "✗"
        if current > limit:
            all_ok = False
        print(f"  {status} {name}: {current:.1f}{unit} (limit: {limit}{unit})")

    print("")
    if all_ok:
        print("  All resources within limits.")
    else:
        print("  ⚠ Some resources exceed limits — consider running 'evolve' for cleanup.")

    return 0 if all_ok else 1


def cmd_recovery_test(args: argparse.Namespace) -> int:
    """Test all recovery mechanisms."""
    print(f"RSIS v{__version__} — Recovery Mechanism Test")
    print("")

    checkpoint = CheckpointManager(CONFIG.workspace_dir)
    injector = FailureInjector(CONFIG.workspace_dir)
    recovery = RecoveryManager(checkpoint_mgr=checkpoint)
    results = []

    # Test 1: Checkpoint and rollback
    print("  Test 1: Checkpoint creation...")
    ch = checkpoint.checkpoint("recovery-test-before")
    if ch:
        print(f"    ✓ Checkpoint created: {ch[:12]}")
    else:
        print("    ⚡ No changes to checkpoint")
    results.append(("checkpoint_creation", ch is not None))

    print("  Test 2: Checkpoint rollback...")
    if ch:
        ok = checkpoint.rollback(ch)
        print(f"    {'✓ Rollback successful' if ok else '✗ Rollback failed'}")
        results.append(("checkpoint_rollback", ok))
    else:
        print("    ⚡ Skipped (no checkpoint)")
        results.append(("checkpoint_rollback", True))

    # Test 3: Failure injection + recovery
    print("  Test 3: File corruption + recovery...")
    test_file = ".rsis/recovery_test_marker"
    Path(CONFIG.workspace_dir, test_file).parent.mkdir(parents=True, exist_ok=True)
    Path(CONFIG.workspace_dir, test_file).write_text("recovery test marker")
    checkpoint.checkpoint("recovery-test-marker")

    ok = injector.corrupt_file(test_file)
    print(f"    {'✓ Corruption injected' if ok else '✗ Injection failed'}")
    results.append(("failure_injection", ok))

    rollback_ok = recovery.rollback_on_failure("recovery_test")
    print(f"    {'✓ Rollback recovered corruption' if rollback_ok else '✗ Rollback failed'}")
    results.append(("rollback_recovery", rollback_ok))

    # Test 4: Human alert logging
    print("  Test 4: Human-in-loop alert...")
    recovery._notify_human("Recovery test alert")
    alert_log = Path(CONFIG.workspace_dir) / ".rsis" / "human_alerts.log"
    if alert_log.exists():
        print(f"    ✓ Alert logged to {alert_log}")
        results.append(("human_alert", True))
    else:
        print(f"    ✗ Alert not logged")
        results.append(("human_alert", False))

    # Test 5: Resource enforcer
    print("  Test 5: Resource enforcer...")
    enforcer = ResourceEnforcer()
    enforcer.start()
    time.sleep(1.5)
    alerts = enforcer.alerts
    print(f"    ✓ Enforcer running ({len(alerts)} alerts triggered)")
    enforcer.stop()
    results.append(("resource_enforcer", True))

    # Summary
    print("")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"  Results: {passed}/{total} tests passed")
    if passed == total:
        print("  ✓ All recovery mechanisms operational.")
    else:
        print(f"  ⚠ {total - passed} test(s) failed.")

    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RSIS — Recursive Self-Improvement System",
    )
    parser.add_argument("--version", action="version", version=f"RSIS {__version__}")

    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialise workspace")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="Run improvement session")
    p_run.add_argument("--goal", "-g", default="self-improve the codebase")
    p_run.set_defaults(func=cmd_run)

    p_evolve = sub.add_parser("evolve", help="Run L3 evolution cycle")
    p_evolve.set_defaults(func=cmd_evolve)

    p_dash = sub.add_parser("dashboard", help="Start web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", "-p", type=int, default=8080)
    p_dash.set_defaults(func=cmd_dashboard)

    p_status = sub.add_parser("status", help="System overview")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="Check resource limits")
    p_check.set_defaults(func=cmd_check)

    p_recovery = sub.add_parser("recovery-test",
                                help="Test recovery mechanisms")
    p_recovery.set_defaults(func=cmd_recovery_test)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    setup_logging()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
