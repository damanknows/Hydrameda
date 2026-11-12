"""Hydrameda Specification Auditor: Core Data Models & Schemas.

Defines outcomes, classifications, findings, and structured audit reports
for the autonomous specification and prompt audit subsystem.
"""
from __future__ import annotations

import enum
import time
from dataclasses import asdict, dataclass, field
from typing import Any


class AuditOutcome(str, enum.Enum):
    """Possible outcomes of an autonomous specification audit."""
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    RETRY_REQUIRED = "RETRY_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ScopeClassification(str, enum.Enum):
    """Categorization of functionality discovered in a proposed technical specification."""
    REQUIRED = "REQUIRED"
    NECESSARY_IMPLEMENTATION_DETAIL = "NECESSARY_IMPLEMENTATION_DETAIL"
    OPTIONAL_SUGGESTION = "OPTIONAL_SUGGESTION"
    UNREQUESTED_FEATURE = "UNREQUESTED_FEATURE"
    PROHIBITED_FEATURE = "PROHIBITED_FEATURE"


@dataclass
class ScopeItem:
    """Classified functionality or feature discovered in the specification."""
    feature_name: str
    classification: ScopeClassification
    rationale: str
    source_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "classification": self.classification.value,
            "rationale": self.rationale,
            "source_text": self.source_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScopeItem:
        return cls(
            feature_name=str(data["feature_name"]),
            classification=ScopeClassification(data["classification"]),
            rationale=str(data.get("rationale", "")),
            source_text=str(data.get("source_text", "")),
        )


class ContaminationSource(str, enum.Enum):
    """Origin source of context contamination."""
    PREVIOUS_TASK = "PREVIOUS_TASK"
    HISTORICAL_RAG = "HISTORICAL_RAG"
    IRRELEVANT_REPO_MODULE = "IRRELEVANT_REPO_MODULE"
    AGENT_ASSUMPTION = "AGENT_ASSUMPTION"
    PROMPT_OPTIMIZER = "PROMPT_OPTIMIZER"
    UNKNOWN = "UNKNOWN"


@dataclass
class ContaminationItem:
    """Suspicious concept or foreign specification element originating from outside task intent."""
    concept: str
    source: ContaminationSource
    evidence: str
    provenance_id: str = ""
    severity: str = "HIGH"

    def to_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "source": self.source.value,
            "evidence": self.evidence,
            "provenance_id": self.provenance_id,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContaminationItem:
        return cls(
            concept=str(data["concept"]),
            source=ContaminationSource(data.get("source", ContaminationSource.UNKNOWN.value)),
            evidence=str(data.get("evidence", "")),
            provenance_id=str(data.get("provenance_id", "")),
            severity=str(data.get("severity", "HIGH")),
        )


@dataclass
class AuditFinding:
    """Structured defect, warning, or observation identified during specification auditing."""
    check_name: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"
    description: str
    details: str = ""
    remediation_instruction: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditFinding:
        return cls(
            check_name=str(data.get("check_name", "UNKNOWN")),
            severity=str(data.get("severity", "HIGH")),
            description=str(data.get("description", "")),
            details=str(data.get("details", "")),
            remediation_instruction=str(data.get("remediation_instruction", "")),
        )


@dataclass
class AuditReport:
    """Comprehensive structured outcome of an autonomous specification audit."""
    outcome: AuditOutcome
    passed: bool
    coverage_score: float = 1.0
    missing_requirements: list[str] = field(default_factory=list)
    prohibition_violations: list[str] = field(default_factory=list)
    scope_items: list[ScopeItem] = field(default_factory=list)
    contamination_items: list[ContaminationItem] = field(default_factory=list)
    findings: list[AuditFinding] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    actionable_feedback: str = ""
    deterministic_passed: bool = True
    semantic_passed: bool = True
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "passed": self.passed,
            "coverage_score": round(self.coverage_score, 3),
            "missing_requirements": list(self.missing_requirements),
            "prohibition_violations": list(self.prohibition_violations),
            "scope_items": [s.to_dict() for s in self.scope_items],
            "contamination_items": [c.to_dict() for c in self.contamination_items],
            "findings": [f.to_dict() for f in self.findings],
            "assumptions": list(self.assumptions),
            "actionable_feedback": self.actionable_feedback,
            "deterministic_passed": self.deterministic_passed,
            "semantic_passed": self.semantic_passed,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditReport:
        return cls(
            outcome=AuditOutcome(data["outcome"]),
            passed=bool(data["passed"]),
            coverage_score=float(data.get("coverage_score", 1.0)),
            missing_requirements=list(data.get("missing_requirements", [])),
            prohibition_violations=list(data.get("prohibition_violations", [])),
            scope_items=[ScopeItem.from_dict(s) for s in data.get("scope_items", [])],
            contamination_items=[ContaminationItem.from_dict(c) for c in data.get("contamination_items", [])],
            findings=[AuditFinding.from_dict(f) for f in data.get("findings", [])],
            assumptions=list(data.get("assumptions", [])),
            actionable_feedback=str(data.get("actionable_feedback", "")),
            deterministic_passed=bool(data.get("deterministic_passed", True)),
            semantic_passed=bool(data.get("semantic_passed", True)),
            timestamp=str(data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))),
            metadata=dict(data.get("metadata", {})),
        )
