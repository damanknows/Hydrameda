"""Hydrameda Specification Auditor: Check B - Prohibition Enforcement.

Verifies that the proposed technical specification does not introduce explicitly
prohibited features, protocols, headers, or claims. Hard constraint violations
immediately reject the specification.
"""
from __future__ import annotations

import re

from intent_engine.models import StructuredIntent

from .models import AuditFinding
from .specification_parser import ParsedSpecification


class ContradictionChecker:
    """Enforces absolute negative constraints and detects contradictory functionality."""

    @classmethod
    def check_prohibitions(
        cls,
        intent: StructuredIntent,
        parsed_spec: ParsedSpecification,
    ) -> tuple[bool, list[str], list[AuditFinding]]:
        """Audits prohibition compliance.

        Returns:
            passed: bool (True if NO prohibited features detected)
            violations: list of violation descriptions
            findings: list of AuditFinding objects
        """
        prohibitions = intent.get_prohibitions()
        if not prohibitions:
            return True, [], []

        spec_text = parsed_spec.raw_markdown
        violations: list[str] = []
        findings: list[AuditFinding] = []

        # Domain-specific signature dictionaries for deep feature detection
        FEATURE_SIGNATURES: dict[str, list[str]] = {
            "jwt": [
                r"\bjwt\b",
                r"\bvalidate_jwt\b",
                r"\bdecode_jwt\b",
                r"\bencode_jwt\b",
                r"\bjwt_token\b",
                r"\bjson\s+web\s+token\b",
                r"\bhs256\s+header\b",
                r"\btoken\s+header\b",
                r"\"alg\"\s*:\s*\"hs256\"",
                r"\"typ\"\s*:\s*\"jwt\"",
            ],
            "claims": [
                r"\bclaims\b",
                r"\bclaim\s+validation\b",
                r"\bpayload_claims\b",
                r"\bstandard_claims\b",
                r"\b(?:exp|nbf|iat|iss|aud|sub)\s+claim\b",
            ],
            "authentication": [
                r"\boauth\b",
                r"\boauth2\b",
                r"\bbearer\s+token\b",
                r"\brefresh_token\b",
                r"\blogin_required\b",
                r"\buser_session\b",
            ],
        }

        for p in prohibitions:
            p_desc = p.normalized_description.lower()
            target_signatures: list[str] = []

            # Check if description matches known signature categories
            for cat, sigs in FEATURE_SIGNATURES.items():
                if cat in p_desc:
                    target_signatures.extend(sigs)

            # If no specialized signature matches, match direct keyword
            if not target_signatures:
                clean_kw = re.escape(p_desc)
                target_signatures.append(rf"\b{clean_kw}\b")

            # Check structural AST elements (functions, classes, imports)
            for fn in parsed_spec.proposed_functions:
                fn_lower = fn.lower()
                for sig in target_signatures:
                    if re.search(sig, fn_lower):
                        msg = f"Function '{fn}' violates explicit prohibition: '{p.normalized_description}'"
                        if msg not in violations:
                            violations.append(msg)
                            findings.append(
                                AuditFinding(
                                    check_name="PROHIBITION_ENFORCEMENT",
                                    severity="CRITICAL",
                                    description=msg,
                                    details=f"Function signature '{fn}' contains prohibited term matching '{sig}'",
                                    remediation_instruction=f"Remove function '{fn}' or redesign it to adhere to non-{p.normalized_description} specifications."
                                )
                            )

            for cls_name in parsed_spec.proposed_classes:
                cls_lower = cls_name.lower()
                for sig in target_signatures:
                    if re.search(sig, cls_lower):
                        msg = f"Class '{cls_name}' violates explicit prohibition: '{p.normalized_description}'"
                        if msg not in violations:
                            violations.append(msg)
                            findings.append(
                                AuditFinding(
                                    check_name="PROHIBITION_ENFORCEMENT",
                                    severity="CRITICAL",
                                    description=msg,
                                    details=f"Class name '{cls_name}' contains prohibited concept matching '{sig}'",
                                    remediation_instruction=f"Remove class '{cls_name}' to avoid introducing prohibited functionality."
                                )
                            )

            for imp in parsed_spec.referenced_imports:
                imp_lower = imp.lower()
                for sig in target_signatures:
                    if re.search(sig, imp_lower):
                        msg = f"Import '{imp}' introduces prohibited library: '{p.normalized_description}'"
                        if msg not in violations:
                            violations.append(msg)
                            findings.append(
                                AuditFinding(
                                    check_name="PROHIBITION_ENFORCEMENT",
                                    severity="CRITICAL",
                                    description=msg,
                                    details=f"Referenced module '{imp}' violates prohibition on '{p.normalized_description}'",
                                    remediation_instruction=f"Eliminate import of '{imp}'. Use Python standard library modules."
                                )
                            )

            # Check code blocks and full markdown lines (with negative disclaimer filtering)
            for line in spec_text.splitlines():
                line_lower = line.lower()
                # Skip lines that are stating negative disclaimers (e.g. "Do not implement JWT", "NO JWT headers", "unlike JWT...")
                if any(disclaim in line_lower for disclaim in (
                    "do not", "never", "avoid", "prohibit", "without", "unlike", "reject", "not include", "no ", "not ", "non-", "zero "
                )):
                    continue

                for sig in target_signatures:
                    if re.search(sig, line_lower):
                        # Affirmative introduction found in spec
                        msg = f"Specification text affirmatively introduces prohibited concept '{p.normalized_description}'"
                        if msg not in violations:
                            violations.append(msg)
                            findings.append(
                                AuditFinding(
                                    check_name="PROHIBITION_ENFORCEMENT",
                                    severity="CRITICAL",
                                    description=msg,
                                    details=f"Affirmative match on '{sig}' in line: \"{line.strip()[:100]}\"",
                                    remediation_instruction=f"Purge all references and design elements relating to '{p.normalized_description}' from the specification."
                                )
                            )

        passed = (len(violations) == 0)
        return passed, violations, findings
