"""Hydrameda Specification Auditor: Check C - Scope Expansion.

Identifies and classifies all proposed functionality into:
- REQUIRED
- NECESSARY_IMPLEMENTATION_DETAIL
- OPTIONAL_SUGGESTION
- UNREQUESTED_FEATURE
- PROHIBITED_FEATURE

Enforces strict boundaries against scope creep while permitting essential technical details.
"""
from __future__ import annotations

from typing import ClassVar

from intent_engine.models import StructuredIntent

from .models import AuditFinding, ScopeClassification, ScopeItem
from .specification_parser import ParsedSpecification


class ScopeChecker:
    """Classifies specification functionality and detects unrequested or prohibited scope expansion."""

    STANDARD_IMPLEMENTATION_PATTERNS: ClassVar[set[str]] = {
        "compare_digest", "constant_time", "serialize", "deserialize",
        "encode", "decode", "validate_format", "test_", "fixture",
        "error", "exception", "setup", "teardown", "hash", "secret"
    }

    UNREQUESTED_CAPABILITY_PATTERNS: ClassVar[dict[str, str]] = {
        "expiry": "Expiry / TTL timestamp validation",
        "exp": "Timestamp-based expiration validation",
        "refresh": "Token refreshing or renewal mechanism",
        "revoke": "Token revocation list / blocklist",
        "database": "Persistent database storage or ORM",
        "sqlite": "Persistent database storage",
        "role": "Role-based access control (RBAC)",
        "permission": "Permission matrix or ACL",
        "session": "User session management",
        "user_id": "User identification and account model",
        "claims": "Claims dictionary handling",
    }

    @classmethod
    def check_scope(
        cls,
        intent: StructuredIntent,
        parsed_spec: ParsedSpecification,
    ) -> tuple[bool, list[ScopeItem], list[AuditFinding]]:
        """Audits specification scope.

        Returns:
            passed: bool (True if NO prohibited or fatal unrequested features detected)
            scope_items: list of classified ScopeItem objects
            findings: list of AuditFinding objects
        """
        scope_items: list[ScopeItem] = []
        findings: list[AuditFinding] = []
        prohibitions = [p.normalized_description.lower() for p in intent.get_prohibitions()]
        positive_reqs = [r.normalized_description.lower() for r in intent.get_positive_requirements()]
        prompt_lower = intent.original_prompt.lower()

        # All candidate features to evaluate
        all_features = set(parsed_spec.proposed_functions + parsed_spec.proposed_classes + parsed_spec.headings)

        fatal_violations = False

        for feat in sorted(all_features):
            feat_lower = feat.lower()

            # 1. Check for PROHIBITED_FEATURE
            if any(p in feat_lower for p in prohibitions):
                item = ScopeItem(
                    feature_name=feat,
                    classification=ScopeClassification.PROHIBITED_FEATURE,
                    rationale="Feature matches explicit prohibition in task constraints.",
                    source_text=feat
                )
                scope_items.append(item)
                fatal_violations = True
                findings.append(
                    AuditFinding(
                        check_name="SCOPE_EXPANSION",
                        severity="CRITICAL",
                        description=f"Prohibited feature detected in scope: '{feat}'",
                        details=f"Functionality '{feat}' was explicitly prohibited by user intent.",
                        remediation_instruction=f"Remove '{feat}' from the specification."
                    )
                )
                continue

            # 2. Check for REQUIRED feature
            is_required = any(
                all(tok in feat_lower for tok in req.split() if len(tok) > 3)
                for req in positive_reqs
            ) or any(
                tok in feat_lower for tok in ("sign", "verify") if tok in prompt_lower
            )

            if is_required:
                scope_items.append(
                    ScopeItem(
                        feature_name=feat,
                        classification=ScopeClassification.REQUIRED,
                        rationale="Explicitly requested in task prompt or required deliverables.",
                        source_text=feat
                    )
                )
                continue

            # 3. Check for NECESSARY_IMPLEMENTATION_DETAIL
            if any(std in feat_lower for std in cls.STANDARD_IMPLEMENTATION_PATTERNS):
                scope_items.append(
                    ScopeItem(
                        feature_name=feat,
                        classification=ScopeClassification.NECESSARY_IMPLEMENTATION_DETAIL,
                        rationale="Standard technical detail (safe comparison, serialization, or test structure).",
                        source_text=feat
                    )
                )
                continue

            # 4. Check for UNREQUESTED_FEATURE
            unrequested_match = None
            for pattern, label in cls.UNREQUESTED_CAPABILITY_PATTERNS.items():
                if pattern in feat_lower and pattern not in prompt_lower:
                    unrequested_match = label
                    break

            if unrequested_match:
                item = ScopeItem(
                    feature_name=feat,
                    classification=ScopeClassification.UNREQUESTED_FEATURE,
                    rationale=f"Introduces unrequested functionality: {unrequested_match}.",
                    source_text=feat
                )
                scope_items.append(item)
                fatal_violations = True
                findings.append(
                    AuditFinding(
                        check_name="SCOPE_EXPANSION",
                        severity="HIGH",
                        description=f"Unrequested scope expansion: '{feat}' ({unrequested_match})",
                        details=f"The user did not request {unrequested_match}. Keep scope confined to requested deliverables.",
                        remediation_instruction=f"Remove '{feat}' or re-evaluate specification to satisfy only requested capabilities."
                    )
                )
                continue

            # 5. Default: Optional / auxiliary detail
            scope_items.append(
                ScopeItem(
                    feature_name=feat,
                    classification=ScopeClassification.OPTIONAL_SUGGESTION,
                    rationale="Auxiliary specification detail or architectural structure.",
                    source_text=feat
                )
            )

        passed = not fatal_violations
        return passed, scope_items, findings
