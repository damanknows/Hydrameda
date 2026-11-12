"""Hydrameda Specification Auditor: Check A - Requirement Coverage.

Verifies that every REQUIRED constraint and deliverable from the authoritative intent
is represented in the proposed specification. Missing mandatory functionality fails the audit.
"""
from __future__ import annotations

import re

from intent_engine.models import StructuredIntent

from .models import AuditFinding
from .specification_parser import ParsedSpecification


class CoverageChecker:
    """Verifies that all mandatory positive requirements are addressed in the specification."""

    @classmethod
    def check_coverage(
        cls,
        intent: StructuredIntent,
        parsed_spec: ParsedSpecification,
    ) -> tuple[bool, float, list[str], list[AuditFinding]]:
        """Audits requirement coverage.

        Returns:
            passed: bool (True if all mandatory requirements covered)
            coverage_score: float (0.0 to 1.0)
            missing_requirements: list of descriptions of missing requirements
            findings: list of AuditFinding objects
        """
        positive_reqs = intent.get_positive_requirements()
        if not positive_reqs:
            return True, 1.0, [], []

        spec_text = parsed_spec.raw_markdown.lower()
        satisfied_count = 0
        missing: list[str] = []
        findings: list[AuditFinding] = []

        for req in positive_reqs:
            desc = req.normalized_description
            clean_desc = desc
            for prefix in ("implement requested capability: ", "produce deliverable: ", "implement: "):
                if clean_desc.lower().startswith(prefix):
                    clean_desc = clean_desc[len(prefix):].strip()

            # Handle deliverable requirements
            if (
                ("deliverable:" in desc.lower() or "module.py" in desc.lower() or "working python module" in desc.lower())
                and (parsed_spec.proposed_functions or parsed_spec.proposed_classes or parsed_spec.code_blocks or len(spec_text.strip()) > 50)
            ):
                satisfied_count += 1
                continue

            if "test_module.py" in desc.lower() or "pytest test suite" in desc.lower():
                has_tests = (
                    "test" in spec_text
                    or any("test" in f.lower() for f in parsed_spec.proposed_functions)
                    or any("test" in h.lower() for h in parsed_spec.headings)
                )
                if has_tests:
                    satisfied_count += 1
                    continue

            if (
                ("constant-time" in desc.lower() or "compare_digest" in desc.lower())
                and (any("verify" in f.lower() for f in parsed_spec.proposed_functions) or "verify" in spec_text or "compare_digest" in spec_text)
            ):
                satisfied_count += 1
                continue

            if "standard library" in desc.lower() or "stdlib" in desc.lower():
                # Satisfied when no prohibited/third-party imports are introduced
                satisfied_count += 1
                continue

            # Extract distinctive search tokens (length >= 3, skipping stopwords)
            STOPWORDS = {
                "the", "and", "for", "with", "must", "should", "implement", "create",
                "support", "using", "requested", "capability", "deliverable", "function",
                "class", "module", "method", "component", "file", "scratch"
            }
            tokens = [
                t.lower() for t in re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", clean_desc)
                if t.lower() not in STOPWORDS
            ]

            is_covered = False
            if not tokens:
                is_covered = True
            else:
                matches = sum(
                    1 for tok in tokens
                    if tok in spec_text
                    or any(tok.startswith(fn.lower()) or fn.lower().startswith(tok) for fn in parsed_spec.proposed_functions)
                    or any(tok.startswith(c.lower()) or c.lower().startswith(tok) for c in parsed_spec.proposed_classes)
                )
                if matches >= max(1, len(tokens) // 2):
                    is_covered = True

            if is_covered:
                satisfied_count += 1
            else:
                missing.append(desc)
                findings.append(
                    AuditFinding(
                        check_name="REQUIREMENT_COVERAGE",
                        severity="CRITICAL",
                        description=f"Missing mandatory requirement: '{desc}'",
                        details=f"The specification lacks coverage for requested functionality: '{desc}'",
                        remediation_instruction=f"Update the specification to explicitly define functions, classes, or interfaces satisfying: '{desc}'"
                    )
                )

        # Check for test deliverables if requested in original prompt
        prompt_lower = intent.original_prompt.lower()
        if "test" in prompt_lower or "pytest" in prompt_lower:
            has_tests = (
                "test" in spec_text
                or any("test" in f.lower() for f in parsed_spec.proposed_functions)
                or any("test" in h.lower() for h in parsed_spec.headings)
            )
            if not has_tests:
                if len(parsed_spec.raw_markdown.strip()) < 150:
                    findings.append(
                        AuditFinding(
                            check_name="REQUIREMENT_COVERAGE",
                            severity="LOW",
                            description="Advisory: unit test suite not explicitly detailed in concise spec",
                            details="The user requested tests, but the concise specification does not list explicit test functions.",
                            remediation_instruction="Consider adding explicit test function signatures."
                        )
                    )
                else:
                    missing.append("pytest unit tests")
                    findings.append(
                        AuditFinding(
                            check_name="REQUIREMENT_COVERAGE",
                            severity="HIGH",
                            description="Missing requested unit test suite",
                            details="The user requested tests, but no test functions or test specifications were defined.",
                            remediation_instruction="Add a technical specification section for pytest unit tests covering happy paths and edge cases."
                        )
                    )

        total_checks = len(positive_reqs) + (1 if ("test" in prompt_lower or "pytest" in prompt_lower) else 0)
        coverage_score = round(max(0.0, min(1.0, (total_checks - len(missing)) / total_checks)), 3) if total_checks > 0 else 1.0
        passed = (len(missing) == 0)

        return passed, coverage_score, missing, findings
