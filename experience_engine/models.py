"""Hydrameda Experience Engine: Structured Knowledge and Provenance Models.

Defines the three distinct knowledge categories:
1. SKILL: General engineering skills and reusable procedures (how-to guides, recipes).
2. LESSON: Lessons extracted from previous task executions (failure causes, anti-patterns).
3. REPO_CONTEXT: Information inspected from the current repository (module structure, contracts).

Enforces the core principle: SEPARATE LEARNING FROM CODE REUSE.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class KnowledgeCategory(str, Enum):
    """Three distinct knowledge categories in Hydrameda."""
    SKILL = "SKILL"                  # Reusable engineering procedures and recipes
    LESSON = "LESSON"                # Post-execution lessons, warnings, and anti-patterns
    REPO_CONTEXT = "REPO_CONTEXT"    # Live repository structure, interfaces, conventions


class ReusePolicy(str, Enum):
    """Governs how a piece of knowledge may be utilized by downstream agents."""
    PROCEDURAL_GUIDE = "PROCEDURAL_GUIDE"    # Reusable engineering steps and guidelines
    VERIFICATION_RECIPE = "VERIFICATION_RECIPE"  # Test, linter, or sandbox recipes
    FAILURE_WARNING = "FAILURE_WARNING"      # Anti-pattern or hazard warning
    NEVER_CODE_COPY = "NEVER_CODE_COPY"      # Strictly forbids copying historical source code
    ADVISORY_ONLY = "ADVISORY_ONLY"          # Guidance only; non-authoritative


class ExecutionOutcome(str, Enum):
    """Task outcome context from which knowledge was extracted."""
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    GOVERNANCE_REJECTION = "GOVERNANCE_REJECTION"
    SEMANTIC_VIOLATION = "SEMANTIC_VIOLATION"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass
class ProvenanceRecord:
    """Immutable audit metadata tracking the origin and validation of knowledge."""
    source_task_id: str
    outcome: ExecutionOutcome
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    validation_hash: str = ""
    context_source: str = "execution_trace"
    authoritative: bool = False  # By default, learned knowledge is ADVISORY, never authoritative

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_task_id": self.source_task_id,
            "outcome": self.outcome.value,
            "timestamp": self.timestamp,
            "validation_hash": self.validation_hash,
            "context_source": self.context_source,
            "authoritative": self.authoritative,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvenanceRecord:
        return cls(
            source_task_id=str(data.get("source_task_id", "unknown")),
            outcome=ExecutionOutcome(data.get("outcome", ExecutionOutcome.UNKNOWN.value)),
            timestamp=str(data.get("timestamp", datetime.now(UTC).isoformat())),
            validation_hash=str(data.get("validation_hash", "")),
            context_source=str(data.get("context_source", "execution_trace")),
            authoritative=bool(data.get("authoritative", False)),
        )


@dataclass
class KnowledgeRecord:
    """Canonical unit of learned engineering knowledge (Skill, Lesson, or Context)."""
    knowledge_id: str
    category: KnowledgeCategory
    title: str
    description: str
    procedure: list[str] = field(default_factory=list)
    provenance: ProvenanceRecord = field(default_factory=lambda: ProvenanceRecord("init", ExecutionOutcome.UNKNOWN))
    evidence: str = ""
    confidence: float = 1.0
    applicability: list[str] = field(default_factory=list)  # domain tags: ["crypto", "hmac", "worktree"]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_validated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reuse_policy: ReusePolicy = ReusePolicy.PROCEDURAL_GUIDE
    code_snippet: str = ""  # Strictly empty by default unless explicit continuation approved

    def __post_init__(self) -> None:
        # Enforce the strict learning policy: skills and lessons do not store raw implementation code
        if self.reuse_policy == ReusePolicy.NEVER_CODE_COPY and self.code_snippet:
            self.code_snippet = ""

    def calculate_decayed_confidence(self, half_life_days: float = 30.0) -> float:
        """Applies exponential half-life decay to confidence based on time since last validation."""
        try:
            val_dt = datetime.fromisoformat(self.last_validated_at)
            now_dt = datetime.now(UTC)
            days_elapsed = max(0.0, (now_dt - val_dt).total_seconds() / 86400.0)
            decay_factor = math.exp(-math.log(2) * (days_elapsed / half_life_days))
            return max(0.1, min(1.0, round(self.confidence * decay_factor, 3)))
        except (ValueError, TypeError):
            return self.confidence

    def revalidate(self, success: bool) -> None:
        """Updates validation timestamp and adjusts confidence score based on revalidation outcome."""
        self.last_validated_at = datetime.now(UTC).isoformat()
        if success:
            self.confidence = min(1.0, round(self.confidence + 0.05, 3))
        else:
            self.confidence = max(0.1, round(self.confidence - 0.20, 3))

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "category": self.category.value,
            "title": self.title,
            "description": self.description,
            "procedure": list(self.procedure),
            "provenance": self.provenance.to_dict(),
            "evidence": self.evidence,
            "confidence": self.confidence,
            "applicability": list(self.applicability),
            "created_at": self.created_at,
            "last_validated_at": self.last_validated_at,
            "reuse_policy": self.reuse_policy.value,
            "code_snippet": self.code_snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeRecord:
        prov = ProvenanceRecord.from_dict(data.get("provenance", {}))
        return cls(
            knowledge_id=str(data["knowledge_id"]),
            category=KnowledgeCategory(data["category"]),
            title=str(data["title"]),
            description=str(data["description"]),
            procedure=list(data.get("procedure", [])),
            provenance=prov,
            evidence=str(data.get("evidence", "")),
            confidence=float(data.get("confidence", 1.0)),
            applicability=list(data.get("applicability", [])),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
            last_validated_at=str(data.get("last_validated_at", datetime.now(UTC).isoformat())),
            reuse_policy=ReusePolicy(data.get("reuse_policy", ReusePolicy.PROCEDURAL_GUIDE.value)),
            code_snippet=str(data.get("code_snippet", "")),
        )

    def format_for_prompt(self) -> str:
        """Formats the knowledge record for inclusion in agent prompts with explicit provenance and advisory notices."""
        prefix = f"[{self.category.value}: {self.title}]"
        advisory_notice = "[ADVISORY ONLY — NOT PERMISSION TO OVERRIDE USER PROMPT OR CONSTRAINTS]"
        lines = [
            f"### {prefix} {advisory_notice}",
            f"- **Description:** {self.description}",
            f"- **Provenance:** Source Task `{self.provenance.source_task_id}` (Outcome: {self.provenance.outcome.value}, Confidence: {self.confidence:.2f})",
        ]
        if self.procedure:
            lines.append("- **Recommended Procedure:**")
            for i, step in enumerate(self.procedure, 1):
                lines.append(f"  {i}. {step}")
        if self.evidence:
            lines.append(f"- **Execution Evidence:** {self.evidence}")
        return "\n".join(lines)
