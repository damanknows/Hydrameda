"""Hydrameda Specification Auditor: Central Audit Engine.

Coordinates the hybrid audit pipeline:
- Layer 1 Deterministic checks:
  - Check A: Requirement Coverage
  - Check B: Prohibition Enforcement
  - Check C: Scope Expansion
  - Check D: Context Contamination
- Layer 2 Semantic Audit:
  - Paraphrased contradictions & indirect feature expansion
- Audit Policy Engine:
  - Final AuditOutcome synthesis and actionable feedback generation
"""
from __future__ import annotations

import logging
from typing import Any

from intent_engine.models import StructuredIntent

from .audit_policy import AuditPolicy
from .context_contamination_checker import ContextContaminationChecker
from .contradiction_checker import ContradictionChecker
from .coverage_checker import CoverageChecker
from .models import (
    AuditFinding,
    AuditReport,
)
from .scope_checker import ScopeChecker
from .semantic_auditor import SemanticAuditor
from .specification_parser import SpecificationParser

logger = logging.getLogger(__name__)


class AuditEngine:
    """Orchestrates comprehensive autonomous auditing of proposed technical specifications."""

    @classmethod
    def audit_specification(
        cls,
        intent: StructuredIntent,
        spec_md: str,
        vault_context: str = "",
        repo_context: str = "",
        retrieved_skills: list[dict[str, Any]] | None = None,
        retrieved_lessons: list[dict[str, Any]] | None = None,
    ) -> AuditReport:
        """Executes full hybrid audit on a proposed technical specification."""
        try:
            # 1. Parse specification structure
            parsed = SpecificationParser.parse(spec_md)

            # 2. Check A: Requirement Coverage
            cov_passed, cov_score, missing_reqs, cov_findings = CoverageChecker.check_coverage(
                intent=intent,
                parsed_spec=parsed
            )

            # 3. Check B: Prohibition Enforcement
            proh_passed, proh_violations, proh_findings = ContradictionChecker.check_prohibitions(
                intent=intent,
                parsed_spec=parsed
            )

            # 4. Check C: Scope Expansion
            scope_passed, scope_items, scope_findings = ScopeChecker.check_scope(
                intent=intent,
                parsed_spec=parsed
            )

            # 5. Check D: Context Contamination
            contam_passed, contam_items, contam_findings = ContextContaminationChecker.check_contamination(
                intent=intent,
                parsed_spec=parsed,
                vault_context=vault_context,
                repo_context=repo_context,
                retrieved_skills=retrieved_skills,
                retrieved_lessons=retrieved_lessons,
            )

            deterministic_passed = cov_passed and proh_passed and scope_passed and contam_passed

            # 6. Layer 2: Semantic Audit
            sem_passed, sem_findings, assumptions = SemanticAuditor.audit_semantics(
                intent=intent,
                parsed_spec=parsed
            )

            # Aggregate all findings
            all_findings: list[AuditFinding] = (
                cov_findings + proh_findings + scope_findings + contam_findings + sem_findings
            )

            # 7. Audit Policy Decision
            outcome, passed, actionable_feedback = AuditPolicy.evaluate_outcome(
                deterministic_passed=deterministic_passed,
                semantic_passed=sem_passed,
                findings=all_findings,
                scope_items=scope_items,
                contamination_items=contam_items,
                missing_requirements=missing_reqs,
                prohibition_violations=proh_violations,
                internal_error=None,
            )

            return AuditReport(
                outcome=outcome,
                passed=passed,
                coverage_score=cov_score,
                missing_requirements=missing_reqs,
                prohibition_violations=proh_violations,
                scope_items=scope_items,
                contamination_items=contam_items,
                findings=all_findings,
                assumptions=assumptions,
                actionable_feedback=actionable_feedback,
                deterministic_passed=deterministic_passed,
                semantic_passed=sem_passed,
                metadata={
                    "total_classes": len(parsed.proposed_classes),
                    "total_functions": len(parsed.proposed_functions),
                    "total_imports": len(parsed.referenced_imports),
                    "total_headings": len(parsed.headings),
                }
            )

        except Exception as err:
            logger.exception("AuditEngine encountered an unexpected internal error")
            # Fail closed
            outcome, passed, feedback = AuditPolicy.evaluate_outcome(
                deterministic_passed=False,
                semantic_passed=False,
                findings=[],
                scope_items=[],
                contamination_items=[],
                missing_requirements=[],
                prohibition_violations=[],
                internal_error=str(err),
            )
            return AuditReport(
                outcome=outcome,
                passed=False,
                coverage_score=0.0,
                actionable_feedback=feedback,
                deterministic_passed=False,
                semantic_passed=False,
                metadata={"error": str(err)}
            )
