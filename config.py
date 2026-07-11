"""Central configuration for RSIS — all knobs in one place."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class LoopConfig:
    """Per-loop termination budgets and timeouts."""

    l1_max_steps: int = 10
    l1_step_timeout_s: float = 120.0
    l2_max_attempts: int = 5
    l2_session_timeout_s: float = 1800.0  # 30 min
    l3_plateau_sessions: int = 20
    l3_cycle_interval_s: float = 86400.0  # 24h


@dataclass(frozen=True)
class ResourceLimits:
    """Practical resource bounds."""

    disk_usage_pct: float = 80.0
    max_memory_rss_gb: float = 4.0
    max_cpu_cores: int = 0  # 0 = all-but-one
    evaluator_max_rpm: int = 100
    evaluator_backoff_s: float = 5.0


@dataclass(frozen=True)
class EvaluatorConfig:
    """Immutable evaluator settings."""

    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    system_prompt_path: str = "evaluator_prompt.txt"
    read_only_mount: bool = True
    digest_verify: bool = True


@dataclass(frozen=True)
class MemoryConfig:
    """Memory hierarchy configuration."""

    git_base_dir: str = ".rsis/git"
    kg_path: str = ".rsis/knowledge_graph.json"
    vector_persist_dir: str = ".rsis/vectors"
    redundancy_refinement_interval: int = 5  # every N L3 cycles


@dataclass(frozen=True)
class TelemetryConfig:
    """Telemetry collection settings."""

    watch_paths: tuple[str, ...] = (".",)
    ignore_patterns: tuple[str, ...] = (
        ".rsis",
        "__pycache__",
        ".git",
        "*.pyc",
        ".venv",
    )
    report_file: str = ".rsis/telemetry.jsonl"
    flush_interval_s: float = 10.0


@dataclass(frozen=True)
class DashboardConfig:
    """Dashboard server settings."""

    host: str = "127.0.0.1"
    port: int = 8766
    log_level: str = "info"
    token_budget: int = 1_000_000


@dataclass(frozen=True)
class RSISConfig:
    """Top-level RSIS configuration."""

    workspace_root: Path = field(default_factory=lambda: Path.cwd())
    session_id: str = field(default_factory=lambda: os.urandom(8).hex())
    loops: LoopConfig = field(default_factory=LoopConfig)
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)

    @property
    def rsis_dir(self) -> Path:
        return self.workspace_root / ".rsis"

    @property
    def evaluator_prompt_path(self) -> Path:
        return self.workspace_root / self.evaluator.system_prompt_path
