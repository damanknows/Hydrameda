"""Hydrameda Specification Auditor: Audit Policy Engine.

Combines Layer 1 (Deterministic Validation) and Layer 2 (Semantic Audit) outputs
to determine final AuditOutcome and generate actionable architect remediation directives.
"""
from __future__ import annotations

from .models import (
    AuditFinding,
    AuditOutcome,
    ContaminationItem,
    ScopeClassification,
    ScopeItem,
)


class AuditPolicy:
    """Evaluates combined audit findings against governance invariants."""

    @classmethod
    def evaluate_outcome(
        cls,
        deterministic_passed: bool,
        semantic_passed: bool,
        findings: list[AuditFinding],
        scope_items: list[ScopeItem],
        contamination_items: list[ContaminationItem],
        missing_requirements: list[str],
        prohibition_violations: list[str],
        internal_error: str | None = None,
    ) -> tuple[AuditOutcome, bool, str]:
        """Determines the final audit outcome and actionable remediation instructions.

        Returns:
            outcome: AuditOutcome enum value
            passed: bool (True only if outcome == PASSED)
            actionable_feedback: formatted instructions for the architect
        """
        # 1. Handle Internal Errors (Fail-closed)
        if internal_error:
            feedback = f"AUDIT SYSTEM ERROR: {internal_error}. Audit failed closed to prevent unverified execution."
            return AuditOutcome.INTERNAL_ERROR, False, feedback

        # 2. Check for Critical Invariant Violations (Prohibitions & Missing Requirements)
        critical_findings = [f for f in findings if f.severity == "CRITICAL"]
        if prohibition_violations or critical_findings or not deterministic_passed:
            feedback_lines = ["SPECIFICATION AUDIT REJECTED: The proposed specification violates mandatory constraints."]
            if prohibition_violations:
                feedback_lines.append("\nPROHIBITION VIOLATIONS:")
                for v in prohibition_violations:
                    feedback_lines.append(f" - {v}")
            if missing_requirements:
                feedback_lines.append("\nMISSING MANDATORY REQUIREMENTS:")
                for m in missing_requirements:
                    feedback_lines.append(f" - {m}")
            if critical_findings:
                feedback_lines.append("\nREMEDIATION DIRECTIVES:")
                for cf in critical_findings:
                    if cf.remediation_instruction:
                        feedback_lines.append(f" - {cf.remediation_instruction}")

            return AuditOutcome.REJECTED, False, "\n".join(feedback_lines)

        # 3. Check for Material Unrequested Scope Expansion
        unrequested = [s for s in scope_items if s.classification == ScopeClassification.UNREQUESTED_FEATURE]
        if unrequested:
            feedback_lines = ["SPECIFICATION AUDIT REJECTED: Material unrequested scope expansion detected."]
            feedback_lines.append("\nUNREQUESTED FUNCTIONALITY TO ELIMINATE:")
            for u in unrequested:
                feedback_lines.append(f" - {u.feature_name}: {u.rationale}")
            feedback_lines.append("\nKeep implementation confined strictly to requested deliverables.")
            return AuditOutcome.REJECTED, False, "\n".join(feedback_lines)

        # 4. Check for High Severity Semantic Findings
        high_findings = [f for f in findings if f.severity == "HIGH"]
        if high_findings or not semantic_passed:
            feedback_lines = ["SPECIFICATION AUDIT REJECTED: Semantic inconsistencies detected."]
            for hf in high_findings:
                feedback_lines.append(f" - {hf.description}")
                if hf.remediation_instruction:
                    feedback_lines.append(f"   Fix: {hf.remediation_instruction}")
            return AuditOutcome.REJECTED, False, "\n".join(feedback_lines)

        # 5. Clean Pass
        return AuditOutcome.PASSED, True, ""
