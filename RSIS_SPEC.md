# RSIS — Recursive Self-Improvement System

## Implementation Specification

*Generated via RRP — 11 locked decisions, 0 contradictions, full topic coverage*

---

## 1. System Architecture

### 1.1 Three-Loop Stack

```
┌──────────────────────────────────────────────────────────────┐
│                    L3 — Evolution Loop                        │
│  Frequency: hours/days  │  Trigger: cross-session interval   │
│──────────────────────────────────────────────────────────────│
│  - Consolidate memory into knowledge graph                   │
│  - Derive meta-strategies from session history               │
│  - Prune redundant code paths (redundancy refinement)        │
│  - Evolve L2 improvement heuristics                          │
│  - Report cross-session trends                               │
└───────────────────────────┬──────────────────────────────────┘
                            │ promotes
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    L2 — Improvement Loop                      │
│  Frequency: per-session  │  Trigger: session start / detect  │
│──────────────────────────────────────────────────────────────│
│  - Generate code changes (new features, refactors, fixes)    │
│  - Tune prompts / tool selection preferences                 │
│  - Modify architecture within scope                          │
│  - Submit to immutable AI evaluator                          │
│  - On approval: apply, checkpoint, update knowledge graph    │
│  - On rejection: discard, log failure pattern                │
└───────────────────────────┬──────────────────────────────────┘
                            │ spawns
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    L1 — Action Loop                           │
│  Frequency: per-task    │  Trigger: user request / event     │
│──────────────────────────────────────────────────────────────│
│  - Plan → execute tool calls → observe → retry/adapt        │
│  - Collect workspace telemetry                               │
│  - Checkpoint before destructive operations                  │
│  - Fallback: revert to last checkpoint + log                 │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 Loop Termination (per LangChain stacked-loop pattern)

| Loop | Termination Signal | Budget | Timeout |
|------|-------------------|--------|---------|
| L1 | Task completion OR max retries exceeded | 10 tool calls per step | 120s |
| L2 | Evaluator approval OR iteration budget exhausted | 5 improvement attempts | 30min |
| L3 | Plateau detection (no gains in N sessions) OR scheduled | 20 sessions | 24h |

### 1.3 Stacking Semantics

Each loop level **spawns** the level below it and **evaluates** its output before promoting. Failures cascade upward:
- L1 failure → L2 retries with different approach
- L2 failure (evaluator rejection x3) → L3 flags strategy for evolution
- L3 plateau → triggers redundancy refinement

---

## 2. Memory Hierarchy

### 2.1 Three-Tier Storage

```
┌──────────────────────────────────────────────────────┐
│                    Vector Store                       │
│  (Semantic Retrieval — Qdrant / Chroma / pgvector)   │
│  - Embedding search over past improvements           │
│  - Similar pattern retrieval for codegen             │
│  - Failure mode similarity matching                  │
└──────────────────────┬───────────────────────────────┘
         queries ▲
┌──────────────────────┴───────────────────────────────┐
│                  Knowledge Graph                       │
│  (Neo4j / in-memory RDF / NetworkX)                  │
│  - Entity: Module, Function, Pattern, Strategy       │
│  - Relations: DEPENDS_ON, IMPROVED_BY, CAUSED_FAILURE│
│  - Derived by L3 consolidation from raw history      │
└──────────────────────┬───────────────────────────────┘
         commits ▲
┌──────────────────────┴───────────────────────────────┐
│                    Git Repository                      │
│  (Code Versioning — libgit2 / git CLI)               │
│  - Full history of every improvement                  │
│  - Rollback to any point via checkpoint tags          │
│  - Branch per experiment, merge on evaluator approval │
└──────────────────────────────────────────────────────┘
```

### 2.2 Write & Query Flow

| Operation | Write Pattern | Query Pattern |
|-----------|--------------|---------------|
| L1 execution | Log to workspace telemetry | Load recent context |
| L2 improvement | Commit code to git → record in KG | Search vectors for similar patterns |
| L3 evolution | Consolidate KG → update vectors | Query all three for synthesis |

### 2.3 Redundancy Refinement

Every Nth L3 cycle (configurable, default N=5):
1. Scan git history for stale/unused branches
2. Prune knowledge graph nodes with zero references
3. Compress vector store (deduplicate near-identical embeddings)
4. Report bloat metrics

---

## 3. Guardrails & Evaluation

### 3.1 Immutable AI Evaluator

```
┌──────────────────────────────────────┐
│          AI Evaluator (Frozen)        │
│                                      │
│  - Separate model instance / API     │
│  - Fixed system prompt (never edited)│
│  - Evaluates: correctness, safety,   │
│    efficiency, style, regression     │
│  - Outputs: PASS / FAIL + rationale  │
│  - No code modification capability   │
└──────────────────────────────────────┘
         ▲ passes candidate to
         │
┌─────────┴────────────────────────────┐
│         L2 Improvement Engine        │
│  (can modify anything except the     │
│   evaluator's prompt, model, or code)│
└──────────────────────────────────────┘
```

Enforcement of immutability:
- Evaluator runs in a separate process/container
- Its code is loaded from a read-only filesystem mount
- Configuration is environment-variable driven, not file-driven
- Digest verification at startup (SHA-256 of evaluator binary)

### 3.2 Resource Limits

Despite the "no artificial guardrails" stance, practical bounds must exist to prevent host exhaustion:

| Resource | Limit | Action on Exceed |
|----------|-------|-----------------|
| Disk (git + vector store) | 80% of available | Trigger redundancy refinement |
| Memory (process) | 4GB RSS | Halt L2, fallback to L1 only |
| CPU (improvement process) | N-1 cores | Throttle L3 frequency |
| API calls (evaluator) | 100/min | Exponential backoff |

### 3.3 Recovery Mechanisms

| Failure | Mechanism | Recovery |
|---------|-----------|----------|
| Destructive code change | Git checkpoint rollback | `git checkout` + restart L2 |
| Evaluator unreachable | Degraded mode | Queue improvements, retry |
| Infinite L1 loop | Max iterations + timeout | Kill L1, log, alert |
| Memory corruption | Fallback interpreter | Reset to last valid state |

---

## 4. Workspace Telemetry

### 4.1 Data Collected

- File modification events (inotify / watchman)
- Shell command history (`.bash_history` / `.zsh_history`)
- Editor buffer state (via LSP / extension integration)
- Resource usage (CPU, memory, disk I/O)
- Error rates (stderr capture, exit codes)

### 4.2 Reporting Format

```json
{
  "session_id": "uuid",
  "timestamp": "ISO8601",
  "loop_level": "L1|L2|L3",
  "trigger": "user_request|scheduled|threshold",
  "events": [
    {
      "type": "file_write|command|error|eval",
      "path": "src/main.py",
      "delta": "+42 -12 lines",
      "duration_ms": 1234
    }
  ],
  "metrics": {
    "token_usage": 45000,
    "eval_score": 0.87,
    "iterations": 3
  }
}
```

### 4.3 Extrapolation Engine

Analyzes telemetry across sessions to:
- Predict optimal L2 iteration budget based on past eval curves
- Detect performance regression trends before they hit thresholds
- Suggest which code areas need redundancy refinement
- Generate cross-session improvement velocity reports

---

## 5. Implementation Phases

### Phase 1 — Core Loop Engine (MVP)
- [ ] L1 action loop with tool calling and checkpointing
- [ ] L2 code generation with git commits
- [ ] Immutable evaluator integration (separate process)
- [ ] Basic workspace telemetry collection

### Phase 2 — Memory & Persistence
- [ ] Hierarchical memory (git → knowledge graph → vectors)
- [ ] L3 evolution loop with memory consolidation
- [ ] Similarity search for improvement patterns

### Phase 3 — Autonomy & Refinement
- [ ] Redundancy refinement automation
- [ ] Telemetry-based extrapolation engine
- [ ] Cross-session strategy evolution
- [ ] Web dashboard for reporting

### Phase 4 — Production Hardening
- [ ] Resource limit enforcement
- [ ] Full recovery mechanism testing
- [ ] Performance optimization
- [ ] Security audit (accepting risk)

---

## 6. Technology Stack Recommendations

| Component | Recommendation | Rationale |
|-----------|---------------|-----------|
| Agent framework | LangChain (loop stacking) | Confirmed reference architecture |
| Vector store | Chroma (local) | Embedded, no infra needed |
| Knowledge graph | NetworkX + JSON serialization | Lightweight, no DB dependency |
| Version control | libgit2 (via pygit2/gitpython) | Programmatic git ops |
| Evaluator | Separate GPT/Claude API | Read-only, frozen prompt |
| Telemetry | watchdog + psutil | Standard Python libs |
| Reporting | FastAPI + HTMX dashboard | Lightweight web UI |

---

## 7. Key Architectural Invariants

1. **Evaluator is immutable** — never in-scope for self-improvement
2. **Checkpoint before every mutation** — rollback is always possible
3. **Loops terminate** — no unbounded recursion within a level
4. **Failure cascades up** — L1→L2→L3 for adaptive retry
5. **Memory is hierarchical** — git (truth) → KG (insight) → vectors (retrieval)
6. **Risk is accepted** — no artificial scope limits, only practical resource bounds

---

*Specification generated 2026-06-30 via RRP (U2|M1|R5/5|D2)*
*11 decisions locked, 0 contradictions, ambiguity resolved to avg 0.25*
