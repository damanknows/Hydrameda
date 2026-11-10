"""Hydrameda Specification Auditor: Autonomous Prompt and Specification Audit Subsystem.

Enforces:
1. Check A: Mandatory Requirement Coverage
2. Check B: Absolute Prohibition Enforcement
3. Check C: Scope Expansion & Creep Prevention
4. Check D: Historical Context & RAG Contamination Detection
5. Layer 2: Independent Semantic Audit & Paraphrase Contradiction Detection
6. Fail-closed hybrid audit policy
"""
from __future__ import annotations

from .audit_engine import AuditEngine
from .audit_policy import AuditPolicy
from .context_contamination_checker import ContextContaminationChecker
from .contradiction_checker import ContradictionChecker
from .coverage_checker import CoverageChecker
from .models import (
    AuditFinding,
    AuditOutcome,
    AuditReport,
    ContaminationItem,
    ContaminationSource,
    ScopeClassification,
    ScopeItem,
)
from .scope_checker import ScopeChecker
from .semantic_auditor import SemanticAuditor
from .specification_parser import ParsedSpecification, SpecificationParser

__all__ = [
    "AuditEngine",
    "AuditFinding",
    "AuditOutcome",
    "AuditPolicy",
    "AuditReport",
    "ContaminationItem",
    "ContaminationSource",
    "ContextContaminationChecker",
    "ContradictionChecker",
    "CoverageChecker",
    "ParsedSpecification",
    "ScopeChecker",
    "ScopeClassification",
    "ScopeItem",
    "SemanticAuditor",
    "SpecificationParser",
]
