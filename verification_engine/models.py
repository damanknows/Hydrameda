"""Models and data structures for independent, deterministic verification."""
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    """Execution status for an individual verification check."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


class CheckName(str, Enum):
    """Canonical names for the 8 required verification checks."""
    SYNTAX = "syntax_validation"
    AST_STRUCTURE = "ast_structural_validation"
    IMPORT_RESOLUTION = "import_resolution"
    RUFF = "ruff"
    MYPY = "mypy"
    BANDIT = "bandit"
    PYTEST = "pytest"
    DEPENDENCY_SECURITY = "dependency_security"


@dataclass
class CheckResult:
    """Detailed result of a single verification check."""
    name: str
    status: CheckStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    diagnostic: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class VerificationReport:
    """Comprehensive structured verification report."""
    task_id: str
    overall_status: str  # "passed" | "failed"
    checks: dict[str, CheckResult] = field(default_factory=dict)
    total_duration_ms: float = 0.0
    summary: str = ""
    failure_diagnostics: dict[str, Any] = field(default_factory=dict)
    sandbox_config: dict[str, Any] = field(default_factory=dict)

    def is_passed(self) -> bool:
        """Determines if verification passed cleanly.
        
        Strict rule: Do not treat skipped checks as successful.
        A run passes ONLY if all applicable checks passed, with 0 failed and 0 skipped checks.
        """
        if not self.checks:
            return False
        for res in self.checks.values():
            if res.status == CheckStatus.FAILED:
                return False
            if res.status == CheckStatus.SKIPPED:
                # Disallow skipped checks on required verification path
                return False
        return self.overall_status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "overall_status": self.overall_status,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
            "total_duration_ms": self.total_duration_ms,
            "summary": self.summary,
            "failure_diagnostics": self.failure_diagnostics,
            "sandbox_config": self.sandbox_config
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

