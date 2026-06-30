"""Configuration and resource limits for RSIS."""

import os
from dataclasses import dataclass, field
from typing import List, Optional


# ── Loop Termination Budgets ──────────────────────────────────────────────

@dataclass
class L1Config:
    """Per-Task Action Loop."""
    max_tool_calls_per_step: int = 10
    step_timeout_s: int = 120
    max_retries: int = 3


@dataclass
class L2Config:
    """Per-Session Improvement Loop."""
    max_improvement_attempts: int = 5
    session_timeout_s: int = 1800  # 30 min


@dataclass
class L3Config:
    """Cross-Session Evolution Loop."""
    plateau_sessions: int = 20
    plateau_timeout_s: int = 86400  # 24 h


# ── Resource Limits ───────────────────────────────────────────────────────

@dataclass
class ResourceLimits:
    """Practical resource bounds to prevent host exhaustion."""
    disk_usage_pct: float = 80.0
    max_memory_rss_mb: int = 4096
    max_cpu_cores: int = max(1, os.cpu_count() or 4) - 1
    evaluator_api_calls_per_min: int = 100


# ── Memory Configuration ─────────────────────────────────────────────────

@dataclass
class MemoryConfig:
    """Three-tier memory hierarchy paths."""
    repo_root: str = "."
    git_branch: str = "rsis-evolution"
    knowledge_graph_path: str = ".rsis/knowledge_graph.json"
    vector_store_path: str = ".rsis/vectors"
    vector_store_dimension: int = 384  # e.g. all-MiniLM-L6-v2


# ── Evaluator Configuration ──────────────────────────────────────────────

@dataclass
class EvaluatorConfig:
    """Immutable evaluator settings."""
    evaluator_path: str = "evaluator/evaluator.py"
    evaluator_prompt_path: str = "evaluator/prompt.txt"
    model: str = "gpt-4o-mini"
    startup_digest_verify: bool = True
    read_only_mount: bool = True


# ── Main Configuration ───────────────────────────────────────────────────

@dataclass
class RSISConfig:
    l1: L1Config = field(default_factory=L1Config)
    l2: L2Config = field(default_factory=L2Config)
    l3: L3Config = field(default_factory=L3Config)
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)

    # Workspace
    workspace_dir: str = "."

    # Telemetry
    telemetry_dir: str = ".rsis/telemetry"
    telemetry_flush_interval_s: int = 5

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = ".rsis/rsis.log"

    # Checkpoint
    checkpoint_before_mutation: bool = True


def load_config() -> RSISConfig:
    """Load configuration, potentially from environment overrides."""
    cfg = RSISConfig()
    # Environment overrides
    if "RSIS_WORKSPACE" in os.environ:
        cfg.workspace_dir = os.environ["RSIS_WORKSPACE"]
    if "RSIS_LOG_LEVEL" in os.environ:
        cfg.log_level = os.environ["RSIS_LOG_LEVEL"]
    if "RSIS_EVALUATOR_MODEL" in os.environ:
        cfg.evaluator.model = os.environ["RSIS_EVALUATOR_MODEL"]
    return cfg


# Convenience singleton
CONFIG = load_config()
