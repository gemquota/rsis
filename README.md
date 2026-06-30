# RSIS — Recursive Self-Improvement System

A three-loop recursive self-improvement system implementing the architecture
defined by an RRP session (11 locked decisions, 0 contradictions).

## Architecture

```
L3 ─ Cross-Session Evolution (hours/days)
  ├─ Memory consolidation (git → KG → vectors)
  ├─ Strategy & meta-parameter evolution
  └─ Redundancy refinement pruning

L2 ─ Per-Session Improvement (minutes)
  ├─ Code generation & architecture modification
  ├─ Prompt/tool tuning
  └─ Validated by IMMUTABLE AI evaluator

L1 ─ Per-Task Action Loop (seconds)
  ├─ Tool calls, observations, retries
  ├─ Workspace telemetry collection
  └─ Checkpoint rollback on failure
```

## Key Invariants

- **Evaluator is immutable** — never in-scope for self-improvement
- **Checkpoint before every mutation** — rollback is always possible
- **Loops terminate** — no unbounded recursion within a level
- **Failure cascades up** — L1→L2→L3 for adaptive retry
- **Memory is hierarchical** — git (truth) → KG (insight) → vectors (retrieval)
- **Risk is accepted** — no artificial scope limits, only practical resource bounds

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Initialise a workspace
python -m rsis init

# Run a self-improvement session
python -m rsis run --goal "add error handling to utils.py"

# Run an L3 evolution cycle
python -m rsis evolve

# Check system status
python -m rsis status
```

## Project Structure

```
rsis/
├── rsis/                  # Core Python package
│   ├── __init__.py        # Package metadata
│   ├── config.py          # Configuration & resource limits
│   ├── checkpoint.py      # Git-based checkpoint/rollback
│   ├── telemetry.py       # Workspace telemetry collection
│   ├── evaluator.py       # Evaluator subprocess client
│   ├── loop_l1.py         # L1 Action Loop
│   ├── loop_l2.py         # L2 Improvement Loop
│   ├── loop_l3.py         # L3 Evolution Loop
│   ├── memory.py          # Three-tier memory hierarchy
│   └── main.py            # CLI entry point
├── evaluator/             # Immutable evaluator (separate process)
│   ├── evaluator.py       # Evaluator binary
│   └── prompt.txt         # Immutable evaluator system prompt
├── tests/                 # Test suite
├── requirements.txt
└── README.md
```

## Implementation Phases

| Phase | Status | Description |
|-------|--------|-------------|
| 1     | ✅     | Core Loop Engine — L1, L2, immutable evaluator, checkpoints, telemetry |
| 2     | ⏳     | Memory & Persistence — KG, vector store, L3 evolution |
| 3     | 📅     | Autonomy & Refinement — redundancy pruning, extrapolation, dashboard |
| 4     | 📅     | Production Hardening — resource limits, recovery testing, security |

## Spec

The full implementation specification is at `RSIS_SPEC.md`.
