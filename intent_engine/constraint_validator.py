"""
Hydrameda Intent Engine: Constraint Validator.

Deterministically verifies that generated technical specifications, code, and diffs
satisfy the canonical StructuredIntent ledger without violating negative constraints
or expanding scope unrequested.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import StructuredIntent


@dataclass
class ValidationFinding:
    """Individual finding produced during constraint validation."""
    category: str  # "CRITICAL_CONTRADICTION", "MISSING_REQUIREMENT", "SCOPE_EXPANSION"
    description: str
    requirement_id: str = ""
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConstraintValidationReport:
    """Comprehensive validation report summarizing adherence to the StructuredIntent ledger."""
    passed: bool
    findings: list[ValidationFinding] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    critical_contradictions: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    scope_expansions: list[str] = field(default_factory=list)
    actionable_feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
            "violations": self.violations,
            "critical_contradictions": self.critical_contradictions,
            "missing_requirements": self.missing_requirements,
            "scope_expansions": self.scope_expansions,
            "actionable_feedback": self.actionable_feedback,
        }

    def __iter__(self):
        msg = "; ".join(self.violations) if self.violations else "Constraint validation passed."
        return iter((self.passed, msg))

    def __bool__(self) -> bool:
        return self.passed

    @property
    def is_valid(self) -> bool:
        return self.passed


class ConstraintValidator:
    """Evaluates artifacts against the compiled StructuredIntent ledger."""

    def __init__(self, intent: StructuredIntent):
        self.intent = intent

    def validate_specification(self, spec_md: str, vault_context: str = "") -> ConstraintValidationReport:
        """Validates an architectural specification against the structured intent ledger."""
        findings: list[ValidationFinding] = []
        violations: list[str] = []
        critical_contradictions: list[str] = []
        missing_requirements: list[str] = []
        scope_expansions: list[str] = []

        spec_text = spec_md or ""
        spec_lower = spec_text.lower()
        user_prompt_lower = self.intent.original_prompt.lower()

        # ------------------------------------------------------------------ #
        # 1. Negative Constraints & Prohibitions Verification (Priority 1)
        # ------------------------------------------------------------------ #
        prohibitions = self.intent.get_prohibitions()

        is_jwt_prohibited = any("jwt" in p.normalized_description.lower() for p in prohibitions) or (
            "not implement jwt" in user_prompt_lower or "no jwt" in user_prompt_lower or "do not implement jwt" in user_prompt_lower
        )

        if is_jwt_prohibited:
            # Filter out negative disclaimer statements before checking for JWT implementation features
            filtered_lines = []
            for line in spec_text.splitlines():
                stripped = line.strip()
                if re.search(r"^[-*#\s]*(?:no|non[- ]?jwt|without|avoid|never|do not|prohibit|not)\b", stripped, re.IGNORECASE):
                    continue
                filtered_lines.append(line)
            spec_for_jwt = "\n".join(filtered_lines)
            spec_for_jwt = re.sub(r"(?i)\bnon[- ]?jwt\b", "", spec_for_jwt)
            spec_for_jwt = re.sub(r"(?i)\b(?:no|not|without|avoid|never|do not implement|do not use)\s+jwt\b", "", spec_for_jwt)
            spec_for_jwt = re.sub(r"(?i)\b(?:no|not|without|avoid|never)\s+claims\b", "", spec_for_jwt)

            jwt_indicators = [
                (r"\b(?:jwt|jsonwebtoken|jwk|jws)\b", "JWT terminology"),
                (r"\bvalidate_jwt\b|\bdecode_jwt\b|\bencode_jwt\b", "JWT validation/encoding functions"),
                (r"\bJWTError\b|\bJWTInvalidSignature\b|\bJWTExpired\b", "JWT exception classes"),
                (r"['\"]?alg['\"]?\s*:\s*['\"]?HS256['\"]?", "JWT header algorithm declaration"),
                (r"['\"]?typ['\"]?\s*:\s*['\"]?JWT['\"]?", "JWT header type declaration"),
                (r"\bvalidate_exp\b|\bexp\s+claim\b|\b['\"]exp['\"]", "JWT claims-based expiration"),
                (r"\bclaims\b", "JWT claims dictionary/handling"),
            ]

            detected_jwt_features = []
            for pattern, desc in jwt_indicators:
                matches = re.findall(pattern, spec_for_jwt, flags=re.IGNORECASE)
                if matches:
                    detected_jwt_features.append(f"{desc} (matches: {matches[:3]})")

            if detected_jwt_features:
                msg = f"User explicitly prohibited JWT, but specification implements: {'; '.join(detected_jwt_features)}."
                findings.append(ValidationFinding(
                    category="CRITICAL_CONTRADICTION",
                    description=msg,
                    requirement_id="PROHIB-JWT",
                    evidence=spec_text[:400]
                ))
                critical_contradictions.append(msg)
                violations.append(msg)

        # Check for stub package prohibitions
        is_stubs_prohibited = any("stub" in p.normalized_description.lower() or "mock" in p.normalized_description.lower() for p in prohibitions) or (
            "not create mock" in user_prompt_lower or "no stub" in user_prompt_lower
        )
        if is_stubs_prohibited:
            stub_patterns = [r"\blitellm\.py\b", r"\blanggraph\b", r"\btree_sitter_language_pack\b"]
            for pat in stub_patterns:
                if re.search(pat, spec_text, re.IGNORECASE):
                    msg = f"Specification attempts to create prohibited stub/mock package: '{pat}'"
                    findings.append(ValidationFinding(
                        category="CRITICAL_CONTRADICTION",
                        description=msg,
                        requirement_id="PROHIB-STUB",
                        evidence=pat
                    ))
                    critical_contradictions.append(msg)
                    violations.append(msg)

        # ------------------------------------------------------------------ #
        # 2. Unrequested Protocol Expansion Detection
        # ------------------------------------------------------------------ #
        unrequested_protocols = [
            ("jwt", "JSON Web Token (JWT)"),
            ("oauth", "OAuth"),
            ("saml", "SAML"),
        ]
        for kw, label in unrequested_protocols:
            if kw not in user_prompt_lower and kw in spec_lower and (
                spec_lower.count(kw) >= 3 or f"#{kw}" in spec_lower.replace(" ", "") or f"##{kw}" in spec_lower.replace(" ", "")
            ):
                msg = f"Specification introduced unrequested protocol '{label}' not requested by user prompt."
                findings.append(ValidationFinding(category="SCOPE_EXPANSION", description=msg))
                scope_expansions.append(msg)
                violations.append(msg)

        # ------------------------------------------------------------------ #
        # 3. Required Capabilities Verification (for complete specifications)
        # ------------------------------------------------------------------ #
        is_comprehensive_spec = len(spec_text) > 250
        if is_comprehensive_spec:
            # Check HMAC-SHA256 signing & verification
            if "hmac" in user_prompt_lower:
                has_hmac = "hmac" in spec_lower
                has_sha256 = any(w in spec_lower for w in ["sha256", "sha-256", "sha", "hashlib"])
                has_signing = any(w in spec_lower for w in ["sign", "signing", "sign_token", "create_token"])
                has_verify = any(w in spec_lower for w in ["verify", "verification", "verify_token", "compare_digest"])

                if not (has_hmac or has_sha256):
                    msg = "Specification missing required HMAC-SHA256 cryptographic algorithm."
                    findings.append(ValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

                if not has_signing:
                    msg = "Specification missing required token signing functionality."
                    findings.append(ValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

                if not has_verify:
                    msg = "Specification missing required token verification functionality."
                    findings.append(ValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

                if any(w in user_prompt_lower for w in ["constant-time", "compare_digest", "timing"]) and "compare_digest" not in spec_lower:
                    msg = "Specification must mandate constant-time signature comparison using hmac.compare_digest()."
                    findings.append(ValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

            # Check unit tests deliverable
            if ("pytest" in user_prompt_lower or "tests" in user_prompt_lower) and "test" not in spec_lower:
                msg = "Specification missing required unit test suite definition."
                findings.append(ValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                missing_requirements.append(msg)
                violations.append(msg)

        # ------------------------------------------------------------------ #
        # 4. RAG-Induced Scope Expansion Detection
        # ------------------------------------------------------------------ #
        if vault_context:
            vault_lower = vault_context.lower()
            if "jwt" in vault_lower and "jwt" not in user_prompt_lower and "jwt" in spec_lower:
                msg = "Specification incorporated unrequested JWT protocol from Obsidian RAG context."
                findings.append(ValidationFinding(category="SCOPE_EXPANSION", description=msg))
                scope_expansions.append(msg)
                violations.append(msg)

        # ------------------------------------------------------------------ #
        # 5. Actionable Feedback Synthesis
        # ------------------------------------------------------------------ #
        passed = (len(critical_contradictions) == 0 and len(missing_requirements) == 0 and len(scope_expansions) == 0)

        actionable_feedback = ""
        if not passed:
            feedback_parts = [
                "CRITICAL SPECIFICATION REVISION REQUIRED:",
                "Your generated specification violated user requirements and constraints."
            ]
            if critical_contradictions:
                feedback_parts.append("CONTRADICTIONS TO ELIMINATE:")
                for c in critical_contradictions:
                    feedback_parts.append(f"  - {c}")
                if is_jwt_prohibited:
                    feedback_parts.append(
                        "  * MANDATORY ACTION: Remove all JWT classes (JWTError, etc.), JWT functions (validate_jwt, etc.), "
                        "JWT headers (alg/typ), claims dictionary, and expiration claims."
                    )
                    feedback_parts.append(
                        "  * MANDATORY ACTION: Implement a pure non-JWT HMAC format: `<base64url_payload>.<base64url_signature>`."
                    )

            if missing_requirements:
                feedback_parts.append("MISSING REQUIREMENTS TO IMPLEMENT:")
                for m in missing_requirements:
                    feedback_parts.append(f"  - {m}")

            if scope_expansions:
                feedback_parts.append("SCOPE EXPANSIONS TO REMOVE:")
                for s in scope_expansions:
                    feedback_parts.append(f"  - {s}")
                feedback_parts.append("  * DO NOT adopt architecture from background notes that exceeds the user prompt.")

            actionable_feedback = "\n".join(feedback_parts)

        return ConstraintValidationReport(
            passed=passed,
            findings=findings,
            violations=violations,
            critical_contradictions=critical_contradictions,
            missing_requirements=missing_requirements,
            scope_expansions=scope_expansions,
            actionable_feedback=actionable_feedback
        )
