"""
Hydrameda Specification Fidelity and Constraint Enforcement Engine.

Deterministic neuro-symbolic requirement extraction, negative constraint tracking,
and specification fidelity validation.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskRequirements:
    """Structured representation of task requirements and constraints."""
    required_capabilities: list[str] = field(default_factory=list)
    prohibited_capabilities: list[str] = field(default_factory=list)
    scope_boundaries: list[str] = field(default_factory=list)
    technical_constraints: list[str] = field(default_factory=list)
    requested_deliverables: list[str] = field(default_factory=list)
    repository_conventions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskRequirements:
        if not data:
            return cls()
        return cls(
            required_capabilities=list(data.get("required_capabilities", [])),
            prohibited_capabilities=list(data.get("prohibited_capabilities", [])),
            scope_boundaries=list(data.get("scope_boundaries", [])),
            technical_constraints=list(data.get("technical_constraints", [])),
            requested_deliverables=list(data.get("requested_deliverables", [])),
            repository_conventions=list(data.get("repository_conventions", []))
        )


@dataclass
class FidelityValidationFinding:
    """Structured finding produced during specification fidelity validation."""
    category: str  # "CRITICAL_CONTRADICTION", "MISSING_REQUIREMENT", "SCOPE_EXPANSION"
    description: str
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecificationFidelityReport:
    """Comprehensive report summarizing specification fidelity evaluation."""
    passed: bool
    findings: list[FidelityValidationFinding] = field(default_factory=list)
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
        msg = "; ".join(self.violations) if self.violations else "Specification fidelity verified."
        return iter((self.passed, msg))

    def __bool__(self) -> bool:
        return self.passed


# Canonical Reference Blueprint for Non-JWT HMAC Tokens
NON_JWT_HMAC_SPECIFICATION = """
# Reference Blueprint: Non-JWT HMAC-SHA256 Token Signer & Verifier

## Token Structure
The token consists strictly of two dot-separated URL-safe base64 strings:
    <urlsafe_b64_payload>.<urlsafe_b64_signature>

- urlsafe_b64_payload: URL-safe base64-encoded raw payload (string, bytes, or JSON payload).
- urlsafe_b64_signature: URL-safe base64-encoded HMAC-SHA256 signature computed over the raw payload bytes using the secret key.
- NO JWT headers (no alg/typ JSON header).
- NO claims dictionary (no exp, nbf, iat, iss, sub).
- Exactly ONE dot separator.

## Public API
1. sign_token(payload: Union[str, bytes, dict], secret_key: Union[str, bytes]) -> str:
   - Encodes payload into URL-safe base64.
   - Computes HMAC-SHA256 digest of the raw payload bytes.
   - Encodes signature into URL-safe base64.
   - Returns '<payload_b64>.<signature_b64>'.

2. verify_token(token: str, secret_key: Union[str, bytes]) -> Union[str, bytes, dict]:
   - Validates that token contains exactly two dot-separated parts. Raises ValueError if malformed.
   - Decodes payload and provided signature.
   - Computes expected HMAC-SHA256 signature over decoded payload bytes.
   - Compares expected signature with provided signature using hmac.compare_digest().
   - Raises ValueError if signature mismatch (tampered token or incorrect secret).
   - Returns decoded payload.

## Security Constraints
- Relies exclusively on Python standard library modules (hmac, hashlib, base64, json, secrets).
- Uses hmac.compare_digest() to mitigate timing attacks.
- Strict input validation rejecting malformed tokens, corrupted base64, or non-string tokens with ValueError.

## Unit Tests
- test_sign_and_verify_valid_token: validates round-trip token generation and verification.
- test_tampered_token_rejected: ensures modified payload or signature raises ValueError.
- test_invalid_secret_key_rejected: ensures verification fails with wrong secret.
- test_malformed_token_structure: ensures non-two-part tokens raise ValueError.
"""


def extract_task_requirements(
    user_prompt: str,
    task_analysis: dict[str, Any] | None = None
) -> TaskRequirements:
    """Extracts structured capabilities, negative constraints, scope boundaries, and deliverables from prompt."""
    reqs = TaskRequirements()
    prompt = user_prompt.strip()
    prompt_lower = prompt.lower()

    # Split prompt into clauses / sentences by punctuation (. ; \n)
    clauses = re.split(r"[.;\n]+", prompt)

    # 1. Prohibited capabilities & negative constraints extraction
    prohibition_patterns = [
        re.compile(r"(?:do\s+not|don't|never|avoid|prohibit|prohibited|without)\s+(?:implement|use|create|add|introduce)?\s*([^.;]+)", re.IGNORECASE),
        re.compile(r"\bno\s+([a-zA-Z0-9_\-\s]+)", re.IGNORECASE)
    ]

    for clause in clauses:
        clause_str = clause.strip()
        for pat in prohibition_patterns:
            for match in pat.finditer(clause_str):
                prohibited_text = match.group(1).strip()
                # Split comma-separated or "or"-separated items
                items = re.split(r",|\bor\b|\band\b", prohibited_text)
                for item in items:
                    clean_item = item.strip().strip("'\"`")
                    if clean_item and len(clean_item) > 1 and clean_item.lower() not in ("a", "an", "the", "any"):
                        reqs.prohibited_capabilities.append(clean_item)

    # Specific domain negative constraint identification
    if any(p in prompt_lower for p in ["not implement jwt", "no jwt", "without jwt", "avoid jwt", "do not implement jwt", "prohibit jwt"]):
        for jwt_term in ["JWT", "JWT headers", "JWT claims", "claims-based expiration", "unrelated authentication features"]:
            if jwt_term not in reqs.prohibited_capabilities:
                reqs.prohibited_capabilities.append(jwt_term)

    if any(p in prompt_lower for p in ["not create mock", "no mock", "no stub", "without stub", "do not create mock or stub packages"]):
        stub_term = "fake or stub dependency packages"
        if stub_term not in reqs.prohibited_capabilities:
            reqs.prohibited_capabilities.append(stub_term)

    # 2. Required capabilities extraction
    if "hmac" in prompt_lower:
        reqs.required_capabilities.append("HMAC-SHA256 token signer")
        reqs.required_capabilities.append("HMAC-SHA256 token verifier")
        reqs.required_capabilities.append("constant-time signature verification (hmac.compare_digest)")

    if "pytest" in prompt_lower or "tests" in prompt_lower or "unit test" in prompt_lower:
        reqs.requested_deliverables.append("pytest unit tests")
        reqs.required_capabilities.append("comprehensive unit test suite")

    if "jwt" in prompt_lower and not any("jwt" in p.lower() for p in reqs.prohibited_capabilities):
        reqs.required_capabilities.append("JWT token verification")

    if "signer" in prompt_lower and not any("signer" in r.lower() for r in reqs.required_capabilities):
        reqs.required_capabilities.append("token signer")

    if "verifier" in prompt_lower and not any("verifier" in r.lower() for r in reqs.required_capabilities):
        reqs.required_capabilities.append("token verifier")

    # 3. Technical constraints
    if "standard library" in prompt_lower or "stdlib" in prompt_lower or "use hmac-sha256 directly" in prompt_lower:
        reqs.technical_constraints.append("Python standard library only (hashlib, hmac, base64, json)")
        reqs.technical_constraints.append("No third-party cryptographic dependencies")

    if "hmac" in prompt_lower:
        reqs.technical_constraints.append("hmac.compare_digest() for signature verification")
        reqs.technical_constraints.append("Explicitly documented non-JWT token serialization format")

    # 4. Scope boundaries
    if "modify only files required" in prompt_lower or "only files required" in prompt_lower:
        reqs.scope_boundaries.append("Modify only files required for this task")

    if any("unrelated" in p.lower() for p in reqs.prohibited_capabilities) or "do not implement jwt" in prompt_lower:
        reqs.scope_boundaries.append("Prohibit unrequested authentication features, claims, and headers")

    # 5. Deliverables & conventions
    reqs.requested_deliverables.append("module.py: complete working implementation")
    reqs.requested_deliverables.append("test_module.py: pytest test suite")

    if "existing repository architecture" in prompt_lower or "repository architecture" in prompt_lower:
        reqs.repository_conventions.append("Follow existing repository conventions and error types")

    return reqs


class SpecificationFidelityValidator:
    """Validates that an architect-generated specification adheres strictly to user requirements."""

    def __init__(self, task_requirements: TaskRequirements | None = None):
        self.requirements = task_requirements or TaskRequirements()

    def validate(
        self,
        user_prompt: str,
        spec_md: str,
        vault_context: str = ""
    ) -> SpecificationFidelityReport:
        findings: list[FidelityValidationFinding] = []
        violations: list[str] = []
        critical_contradictions: list[str] = []
        missing_requirements: list[str] = []
        scope_expansions: list[str] = []

        spec_text = spec_md or ""
        spec_lower = spec_text.lower()
        user_lower = user_prompt.lower()

        # ------------------------------------------------------------------ #
        # 1. Prohibited Capabilities & Negative Constraints Enforcement
        # ------------------------------------------------------------------ #
        is_jwt_prohibited = any("jwt" in p.lower() for p in self.requirements.prohibited_capabilities) or (
            "not implement jwt" in user_lower or "no jwt" in user_lower or "do not implement jwt" in user_lower
        )

        if is_jwt_prohibited:
            # Filter out negative disclaimer statements before checking for JWT implementation features
            filtered_lines = []
            for line in spec_text.splitlines():
                stripped = line.strip()
                if re.search(r"^[-*#\s]*(?:no|non[- ]?jwt|without|avoid|never|do not|prohibit|not)\b", stripped, re.IGNORECASE):
                    continue
                filtered_lines.append(line)
            spec_for_jwt_check = "\n".join(filtered_lines)
            spec_for_jwt_check = re.sub(r"(?i)\bnon[- ]?jwt\b", "", spec_for_jwt_check)
            spec_for_jwt_check = re.sub(r"(?i)\b(?:no|not|without|avoid|never|do not implement|do not use)\s+jwt\b", "", spec_for_jwt_check)
            spec_for_jwt_check = re.sub(r"(?i)\b(?:no|not|without|avoid|never)\s+claims\b", "", spec_for_jwt_check)

            # Look for JWT-specific terms, classes, functions, headers, and claims
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
                matches = re.findall(pattern, spec_for_jwt_check, flags=re.IGNORECASE)
                if matches:
                    detected_jwt_features.append(f"{desc} (matches: {matches[:3]})")

            if detected_jwt_features:
                msg = f"User explicitly prohibited JWT, but specification implements: {'; '.join(detected_jwt_features)}."
                findings.append(FidelityValidationFinding(
                    category="CRITICAL_CONTRADICTION",
                    description=msg,
                    evidence=spec_text[:400]
                ))
                critical_contradictions.append(msg)
                violations.append(msg)

        # Check for stub package prohibitions
        is_stubs_prohibited = any("stub" in p.lower() or "mock" in p.lower() for p in self.requirements.prohibited_capabilities) or (
            "not create mock" in user_lower or "no stub" in user_lower
        )
        if is_stubs_prohibited:
            stub_patterns = [r"\blitellm\.py\b", r"\blanggraph\b", r"\btree_sitter_language_pack\b"]
            for pat in stub_patterns:
                if re.search(pat, spec_text, re.IGNORECASE):
                    msg = f"Specification attempts to create prohibited stub/mock package: '{pat}'"
                    findings.append(FidelityValidationFinding(
                        category="CRITICAL_CONTRADICTION",
                        description=msg,
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
            if kw not in user_lower and kw in spec_lower and (
                spec_lower.count(kw) >= 3 or f"#{kw}" in spec_lower.replace(" ", "") or f"##{kw}" in spec_lower.replace(" ", "")
            ):
                msg = f"Specification introduced unrequested protocol '{label}' not requested by user prompt."
                findings.append(FidelityValidationFinding(category="SCOPE_EXPANSION", description=msg))
                scope_expansions.append(msg)
                violations.append(msg)

        # ------------------------------------------------------------------ #
        # 3. Required Capabilities Verification (for complete specifications)
        # ------------------------------------------------------------------ #
        is_comprehensive_spec = len(spec_text) > 250
        if is_comprehensive_spec:
            # Check HMAC-SHA256 signing & verification
            if "hmac" in user_lower:
                has_hmac = "hmac" in spec_lower
                has_sha256 = any(w in spec_lower for w in ["sha256", "sha-256", "sha", "hashlib"])
                has_signing = any(w in spec_lower for w in ["sign", "signing", "sign_token", "create_token"])
                has_verify = any(w in spec_lower for w in ["verify", "verification", "verify_token", "compare_digest"])

                if not (has_hmac or has_sha256):
                    msg = "Specification missing required HMAC-SHA256 cryptographic algorithm."
                    findings.append(FidelityValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

                if not has_signing:
                    msg = "Specification missing required token signing functionality."
                    findings.append(FidelityValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

                if not has_verify:
                    msg = "Specification missing required token verification functionality."
                    findings.append(FidelityValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

                if any(w in user_lower for w in ["constant-time", "compare_digest", "timing"]) and "compare_digest" not in spec_lower:
                    msg = "Specification must mandate constant-time signature comparison using hmac.compare_digest()."
                    findings.append(FidelityValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                    missing_requirements.append(msg)
                    violations.append(msg)

            # Check unit tests deliverable
            if ("pytest" in user_lower or "tests" in user_lower) and "test" not in spec_lower:
                msg = "Specification missing required unit test suite definition."
                findings.append(FidelityValidationFinding(category="MISSING_REQUIREMENT", description=msg))
                missing_requirements.append(msg)
                violations.append(msg)

        # ------------------------------------------------------------------ #
        # 4. RAG-Induced Scope Expansion Detection
        # ------------------------------------------------------------------ #
        if vault_context:
            vault_lower = vault_context.lower()
            # If RAG context contained JWT specifications but user did NOT request JWT
            if "jwt" in vault_lower and "jwt" not in user_lower and "jwt" in spec_lower:
                msg = "Specification incorporated unrequested JWT protocol from Obsidian RAG context."
                findings.append(FidelityValidationFinding(category="SCOPE_EXPANSION", description=msg))
                scope_expansions.append(msg)
                violations.append(msg)

        # ------------------------------------------------------------------ #
        # 4. Actionable Feedback Synthesis
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

        return SpecificationFidelityReport(
            passed=passed,
            findings=findings,
            violations=violations,
            critical_contradictions=critical_contradictions,
            missing_requirements=missing_requirements,
            scope_expansions=scope_expansions,
            actionable_feedback=actionable_feedback
        )


def validate_specification_fidelity(
    user_prompt: str,
    spec_md: str,
    task_requirements: TaskRequirements | None = None,
    vault_context: str = ""
) -> SpecificationFidelityReport:
    """Convenience functional wrapper for SpecificationFidelityValidator."""
    reqs = task_requirements or extract_task_requirements(user_prompt)
    validator = SpecificationFidelityValidator(task_requirements=reqs)
    return validator.validate(user_prompt=user_prompt, spec_md=spec_md, vault_context=vault_context)
