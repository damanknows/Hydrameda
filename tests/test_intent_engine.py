"""Tests for the Intent Engine subsystem.

Verifies:
- Precedence hierarchy and hard veto policies.
- Parsing of explicit prohibitions, positive requirements, and scope boundaries.
- Deterministic constraint normalization and immutable compilation.
- Active context firewalling (filtering RAG and repository context).
- Specification and code artifact constraint validation.
"""

from __future__ import annotations

from intent_engine.constraint_compiler import ConstraintCompiler
from intent_engine.constraint_validator import ConstraintValidator
from intent_engine.intent_guard import IntentGuard
from intent_engine.intent_parser import IntentParser
from intent_engine.models import (
    ConstraintPriority,
    EnforcementStrategy,
    RequirementItem,
    RequirementType,
    StructuredIntent,
    VerificationStatus,
)
from intent_engine.policy import PrecedencePolicy


class TestPrecedencePolicy:
    """Verifies constraint precedence rules and conflict resolution."""

    def test_priority_ordering(self) -> None:
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.EXPLICIT_PROHIBITION, ConstraintPriority.EXPLICIT_REQUIREMENT
        )
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.EXPLICIT_REQUIREMENT, ConstraintPriority.USER_SCOPE_BOUNDARY
        )
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.USER_SCOPE_BOUNDARY, ConstraintPriority.ACCEPTANCE_CRITERIA
        )
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.ACCEPTANCE_CRITERIA, ConstraintPriority.REPOSITORY_COMPATIBILITY
        )
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.REPOSITORY_COMPATIBILITY, ConstraintPriority.RETRIEVED_EXPERIENCE
        )
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.RETRIEVED_EXPERIENCE, ConstraintPriority.RAG_SUGGESTION
        )
        assert PrecedencePolicy.has_higher_precedence(
            ConstraintPriority.RAG_SUGGESTION, ConstraintPriority.MODEL_ASSUMPTION
        )

    def test_prohibition_hard_veto_over_lower_priority(self) -> None:
        prohib = RequirementItem(
            id="PROHIB-001",
            source_text="No JWT tokens or claims",
            normalized_description="Prohibit JWT tokens or claims",
            req_type=RequirementType.PROHIBITED,
            priority=ConstraintPriority.EXPLICIT_PROHIBITION,
            enforcement_strategy=EnforcementStrategy.HARD_GATE,
            verification_status=VerificationStatus.SATISFIED,
        )
        rag_item = RequirementItem(
            id="RAG-001",
            source_text="Use JWT standard claims",
            normalized_description="Recommend JWT claims from specs",
            req_type=RequirementType.CONTEXTUAL,
            priority=ConstraintPriority.RAG_SUGGESTION,
            enforcement_strategy=EnforcementStrategy.ADVISORY,
            verification_status=VerificationStatus.PENDING,
        )

        assert PrecedencePolicy.overrides(prohib, rag_item) is True
        assert PrecedencePolicy.overrides(rag_item, prohib) is False

    def test_context_conflict_detection(self) -> None:
        prompt = "Create HMAC token signer. Do not use JWT."
        intent = ConstraintCompiler.compile(prompt)

        conflicts = PrecedencePolicy.check_context_conflict(
            intent=intent,
            context_text="The recommended architecture includes a JWT token generator with expiration claims.",
            context_source="ObsidianRAG",
        )
        assert len(conflicts) > 0
        assert any("jwt" in c.lower() for c in conflicts)

    def test_filter_safe_context_redaction(self) -> None:
        prompt = "HMAC token signer. No JWT."
        intent = ConstraintCompiler.compile(prompt)

        dirty_vault_context = (
            "# Security Architecture Note\n"
            "Use HMAC-SHA256 for symmetric signing.\n"
            "For authentication, deploy a JWT validator with standard claims.\n"
            "Ensure proper key rotation."
        )

        clean_context, redacted = PrecedencePolicy.filter_safe_context(
            intent=intent,
            context_text=dirty_vault_context,
            context_source="ObsidianVault",
        )

        assert "HMAC-SHA256" in clean_context
        assert "key rotation" in clean_context
        assert "JWT validator" not in clean_context
        assert len(redacted) == 1


class TestIntentParser:
    """Verifies linguistic and regex parsing of task requirements."""

    def test_parse_prohibitions(self) -> None:
        prompt = (
            "Create an HMAC-SHA256 token signer and verifier.\n"
            "Do not implement JWT.\n"
            "No claims or expiration logic.\n"
            "Never create mock dependency packages."
        )
        parsed = IntentParser.parse_prompt(prompt)

        assert len(parsed["prohibitions"]) >= 3
        prohib_texts = [p.source_text.lower() for p in parsed["prohibitions"]]
        assert any("jwt" in t for t in prohib_texts)
        assert any("claims" in t or "expiration" in t for t in prohib_texts)
        assert any("mock" in t or "package" in t for t in prohib_texts)

    def test_parse_positive_requirements(self) -> None:
        prompt = (
            "Implement an HMAC-SHA256 signature generator and verifier.\n"
            "Include comprehensive pytest unit tests."
        )
        parsed = IntentParser.parse_prompt(prompt)

        req_texts = [r.normalized_description.lower() for r in parsed["requirements"]]
        assert any("hmac-sha256" in t or "signature" in t for t in req_texts)

        deliv_texts = [d.normalized_description.lower() for d in parsed["deliverables"]]
        assert any("pytest" in t or "test" in t for t in deliv_texts)

    def test_parse_scope_boundaries(self) -> None:
        prompt = "Create HMAC utility. Follow existing repository architecture. Modify only files required."
        parsed = IntentParser.parse_prompt(prompt)
        assert len(parsed["scope_boundaries"]) >= 1


class TestConstraintNormalizerAndCompiler:
    """Verifies constraint compilation, ledger generation, and serialization."""

    def test_compilation_and_ledger(self) -> None:
        prompt = (
            "Create an HMAC-SHA256 token signer and verifier with pytest tests.\n"
            "Strictly follow existing repo architecture.\n"
            "Do not use JWT or claims."
        )
        intent = ConstraintCompiler.compile(prompt)

        assert intent.original_prompt == prompt
        assert len(intent.get_prohibitions()) >= 1
        assert len(intent.get_positive_requirements()) >= 1

        ledger = intent.to_markdown_ledger()
        assert "# Structured Task Intent & Constraint Ledger" in ledger
        assert "PROHIB-" in ledger
        assert "REQ-" in ledger

    def test_serialization_roundtrip(self) -> None:
        prompt = "Create HMAC module. No JWT."
        intent = ConstraintCompiler.compile(prompt)

        data = intent.to_dict()
        reconstructed = StructuredIntent.from_dict(data)

        assert reconstructed.original_prompt == intent.original_prompt
        assert len(reconstructed.requirements) == len(intent.requirements)
        assert reconstructed.requirements[0].id == intent.requirements[0].id
        assert reconstructed.requirements[0].req_type == intent.requirements[0].req_type

    def test_format_agent_prompt_directives(self) -> None:
        prompt = "Build HMAC module. Prohibit JWT."
        intent = ConstraintCompiler.compile(prompt)

        directives = ConstraintCompiler.format_agent_prompt_directives(intent, role="architect")
        assert "CANONICAL TASK INTENT & CONSTRAINTS" in directives
        assert "STRICT PROHIBITION DIRECTIVES (ABSOLUTE VETO)" in directives
        assert "jwt" in directives.lower()


class TestIntentGuard:
    """Verifies RAG filtering and repository context firewalling."""

    def test_filter_repository_context(self) -> None:
        prompt = "Create HMAC signer. Do not use JWT."
        intent = ConstraintCompiler.compile(prompt)

        files = [
            "crypto/hmac_signer.py",
            "exported_modules/jwt_validator.py",
            "tests/test_crypto.py",
            "auth/jwt_tokens.py",
        ]
        deps = ["cryptography", "pyjwt", "pytest"]

        safe_files, safe_deps = IntentGuard.filter_repository_context(
            intent=intent,
            relevant_files=files,
            detected_deps=deps,
        )

        assert "crypto/hmac_signer.py" in safe_files
        assert "tests/test_crypto.py" in safe_files
        assert "exported_modules/jwt_validator.py" not in safe_files
        assert "auth/jwt_tokens.py" not in safe_files
        assert "pyjwt" not in safe_deps
        assert "cryptography" in safe_deps

    def test_validate_code_artifacts_violations(self) -> None:
        prompt = "Build HMAC signer. No JWT."
        intent = ConstraintCompiler.compile(prompt)

        dirty_files = {
            "token_signer.py": "import jwt\n\nclass JWTTokenSigner:\n    def validate_jwt(self):\n        pass",
            "litellm.py": "# Stub litellm",
        }

        violations = IntentGuard.validate_code_artifacts(intent, dirty_files)
        assert len(violations) >= 3
        assert any("stub/mock" in v for v in violations)
        assert any("JWT exception/class" in v or "validate_jwt" in v for v in violations)
        assert any("import" in v and "jwt" in v for v in violations)

    def test_validate_code_artifacts_clean(self) -> None:
        prompt = "Build HMAC signer. No JWT."
        intent = ConstraintCompiler.compile(prompt)

        clean_files = {
            "token_signer.py": (
                "import hmac\nimport hashlib\n\n"
                "class HMACTokenSigner:\n"
                "    def __init__(self, key: bytes):\n"
                "        self.key = key\n"
                "    def sign(self, msg: bytes) -> str:\n"
                "        return hmac.new(self.key, msg, hashlib.sha256).hexdigest()\n"
            ),
        }

        violations = IntentGuard.validate_code_artifacts(intent, clean_files)
        assert violations == []


class TestConstraintValidator:
    """Verifies specification validation against user intent constraints."""

    def test_spec_validation_failure_on_prohibited_terms(self) -> None:
        prompt = "Create HMAC token signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        validator = ConstraintValidator(intent)

        dirty_spec = (
            "# Architecture Specification: HMAC Token Signer\n\n"
            "## Architecture\n"
            "We will implement a JWT token signer that encodes claims and expiration.\n"
            "### Exports\n"
            "- validate_jwt()\n"
        )

        report = validator.validate_specification(dirty_spec)
        assert report.is_valid is False
        assert len(report.violations) > 0
        assert any("jwt" in v.lower() for v in report.violations)

        # Verify tuple unpacking compatibility
        is_valid, violations = validator.validate_specification(dirty_spec)
        assert is_valid is False
        assert len(violations) > 0

    def test_spec_validation_success_on_compliant_spec(self) -> None:
        prompt = "Create HMAC token signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        validator = ConstraintValidator(intent)

        compliant_spec = (
            "# Architecture Specification: HMAC Token Signer\n\n"
            "## Summary\n"
            "Implements HMAC-SHA256 signature generation and verification.\n"
            "## Components\n"
            "- HMACTokenSigner: signs payload with secret key using HMAC-SHA256\n"
            "- HMACVerifier: constant-time comparison of HMAC signatures\n"
            "## Deliverables\n"
            "- hmac_token.py\n"
            "- tests/test_hmac_token.py\n"
        )

        report = validator.validate_specification(compliant_spec)
        assert report.is_valid is True
        assert report.violations == []
