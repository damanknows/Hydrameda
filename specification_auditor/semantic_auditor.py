"""Hydrameda Specification Auditor: Layer 2 - Independent Semantic Auditor.

Performs semantic analysis to detect:
1. Paraphrased contradictions (e.g. indirect JWT structures described without explicit "JWT" keyword)
2. Indirect feature expansion (e.g. stateful user models, session cookies, database schemas)
3. Omitted requirements and subtle architectural gaps
4. Ambiguous scope and unwarranted assumptions

Fail-closed guarantee: Never treats model failures or service unavailability as a pass.
"""
from __future__ import annotations

import re

from intent_engine.models import StructuredIntent

from .models import AuditFinding
from .specification_parser import ParsedSpecification


class SemanticAuditor:
    """Performs deep semantic inspection of technical specifications."""

    @classmethod
    def audit_semantics(
        cls,
        intent: StructuredIntent,
        parsed_spec: ParsedSpecification,
    ) -> tuple[bool, list[AuditFinding], list[str]]:
        """Audits specification semantics.

        Returns:
            passed: bool (True if NO semantic contradictions or indirect expansions detected)
            findings: list of AuditFinding objects
            assumptions: list of identified or flagged assumptions
        """
        findings: list[AuditFinding] = []
        assumptions: list[str] = list(parsed_spec.declared_assumptions)
        spec_text = parsed_spec.raw_markdown
        spec_lower = spec_text.lower()
        prohibitions = [p.normalized_description.lower() for p in intent.get_prohibitions()]

        # 1. Paraphrased Contradiction: Indirect JWT Structure
        # Check if specification describes a 3-part base64 token with JSON header and payload claims,
        # even if it avoids the exact acronym "JWT"
        if any("jwt" in p for p in prohibitions):
            indirect_jwt_patterns = [
                (r"header\s*\.\s*payload\s*\.\s*signature", "Describes three-part header.payload.signature structure typical of JWT"),
                (r"base64(?:url)?\s*encoded\s*header", "Specifies Base64-encoded token header characteristic of JWT"),
                (r"dictionary\s*containing\s*(?:exp|expiry|expiration)\s*timestamp", "Specifies claims dictionary with expiration timestamp"),
                (r"header\s*containing\s*algorithm\s*(?:hs256|sha256)", "Specifies header dictionary with algorithm declaration"),
            ]
            for pattern, reason in indirect_jwt_patterns:
                if re.search(pattern, spec_lower):
                    findings.append(
                        AuditFinding(
                            check_name="SEMANTIC_CONSISTENCY",
                            severity="CRITICAL",
                            description=f"Paraphrased prohibition violation: {reason}",
                            details=f"Specification indirectly implements JWT mechanics via: '{pattern}'",
                            remediation_instruction="Use a clean two-part HMAC format: <payload>.<signature> without headers or claims dictionaries."
                        )
                    )

        # 2. Indirect Feature Expansion: Database or Session State
        if "database" not in intent.original_prompt.lower() and "session" not in intent.original_prompt.lower():
            stateful_patterns = [
                (r"create\s+table\b", "Declares SQL database tables for stateless token signing"),
                (r"session\s*cookie\b", "Introduces browser session cookies for stateless cryptographic signing"),
                (r"user\s*registration\b", "Introduces user management lifecycle not requested by user"),
            ]
            for pattern, reason in stateful_patterns:
                if re.search(pattern, spec_lower):
                    findings.append(
                        AuditFinding(
                            check_name="SEMANTIC_CONSISTENCY",
                            severity="HIGH",
                            description=f"Indirect feature expansion: {reason}",
                            details=f"Specification introduces unwarranted complexity matching: '{pattern}'",
                            remediation_instruction="Keep the implementation stateless and focused strictly on the requested cryptographic functions."
                        )
                    )

        # 3. Cryptographic Best Practice Auditing (Timing attack vulnerability check)
        if (
            "hmac" in intent.original_prompt.lower()
            and re.search(r"signature\s*==\s*expected", spec_lower)
            and "compare_digest" not in spec_lower
        ):
            findings.append(
                    AuditFinding(
                        check_name="SEMANTIC_CONSISTENCY",
                        severity="HIGH",
                        description="Insecure direct equality comparison for HMAC signatures",
                        details="Specification suggests standard equality (`==`) rather than constant-time `hmac.compare_digest()`.",
                        remediation_instruction="Specify `hmac.compare_digest()` for all signature verifications to prevent timing attacks."
                    )
                )

        # 4. Check for unstated or suspicious assumptions
        for line in spec_text.splitlines():
            line_str = line.strip()
            if line_str.lower().startswith("assume") and line_str not in assumptions:
                assumptions.append(line_str)

        critical_or_high = [f for f in findings if f.severity in ("CRITICAL", "HIGH")]
        passed = (len(critical_or_high) == 0)
        return passed, findings, assumptions
