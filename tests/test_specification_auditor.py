"""Tests for Phase 4: Autonomous Prompt & Specification Auditor and Phase 5: Human Approval Governance.

Verifies:
1. Check A: Mandatory Requirement Coverage
2. Check B: Absolute Prohibition Enforcement
3. Check C: Scope Expansion & Creep Prevention
4. Check D: Historical Context & RAG Contamination Detection
5. Layer 2: Independent Semantic Audit (Paraphrased Contradiction Detection)
6. Central AuditEngine & AuditPolicy Decision Engine
7. Phase 5 Governance: Rich review presentation & immutable constraint enforcement
"""
from __future__ import annotations

from code_engine.governance import GovernanceEngine
from intent_engine.constraint_compiler import ConstraintCompiler
from specification_auditor.audit_engine import AuditEngine
from specification_auditor.context_contamination_checker import ContextContaminationChecker
from specification_auditor.contradiction_checker import ContradictionChecker
from specification_auditor.coverage_checker import CoverageChecker
from specification_auditor.models import (
    AuditOutcome,
    ContaminationSource,
    ScopeClassification,
)
from specification_auditor.scope_checker import ScopeChecker
from specification_auditor.semantic_auditor import SemanticAuditor
from specification_auditor.specification_parser import SpecificationParser


class TestSpecificationParser:
    def test_extracts_ast_elements(self) -> None:
        spec_md = """
# Architectural Specification: HMAC Signer

## Components
We define:
`class HMACTokenManager:`
And functions:
`def sign_token(payload: str, secret: str) -> str`
`def verify_token(token: str, secret: str) -> bool`

```python
import hmac
import hashlib

def serialize_payload(data: dict) -> str:
    raise ValueError("Invalid format")
```

- Assumption: Secret keys are at least 32 bytes in length.
"""
        parsed = SpecificationParser.parse(spec_md)
        assert "Architectural Specification: HMAC Signer" in parsed.headings
        assert "HMACTokenManager" in parsed.proposed_classes
        assert "sign_token" in parsed.proposed_functions
        assert "verify_token" in parsed.proposed_functions
        assert "serialize_payload" in parsed.proposed_functions
        assert "hmac" in parsed.referenced_imports
        assert "hashlib" in parsed.referenced_imports
        assert "ValueError" in parsed.declared_exceptions
        assert any("32 bytes" in a for a in parsed.declared_assumptions)


class TestCoverageChecker:
    def test_valid_coverage_passes(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
`def sign_token(payload: str, secret: str) -> str`
`def verify_token(token: str, secret: str) -> bool`
## Tests
Tests implemented with pytest covering signing and verification.
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, score, missing, _findings = CoverageChecker.check_coverage(intent, parsed)
        assert passed is True
        assert score >= 0.8
        assert len(missing) == 0

    def test_missing_mandatory_feature_fails(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
`def sign_token(payload: str, secret: str) -> str`
(Verification is omitted for future work)
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, _score, missing, findings = CoverageChecker.check_coverage(intent, parsed)
        assert passed is False
        assert any("verify" in m.lower() or "test" in m.lower() for m in missing)
        assert any(f.severity == "CRITICAL" for f in findings)


class TestContradictionChecker:
    def test_prohibited_jwt_rejected(self) -> None:
        prompt = "Create an HMAC token signer. Do not implement JWT, claims, or unrelated auth."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
`def sign_token(payload: str) -> str`
`def validate_jwt(token: str) -> bool`
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, violations, findings = ContradictionChecker.check_prohibitions(intent, parsed)
        assert passed is False
        assert any("validate_jwt" in v for v in violations)
        assert any(f.severity == "CRITICAL" for f in findings)

    def test_negative_disclaimer_does_not_trigger_false_positive(self) -> None:
        prompt = "Create an HMAC token signer. Do not implement JWT, claims, or unrelated auth."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
`def sign_token(payload: str, secret: str) -> str`
`def verify_token(token: str, secret: str) -> bool`
STRICT RULE: Do NOT implement JWT headers or claims. This is a raw HMAC protocol.
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, violations, _findings = ContradictionChecker.check_prohibitions(intent, parsed)
        assert passed is True
        assert len(violations) == 0


class TestScopeChecker:
    def test_necessary_implementation_details_permitted(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
`def sign_token(payload: str, secret: str) -> str`
`def verify_token(token: str, secret: str) -> bool`
`def compare_digest(a: bytes, b: bytes) -> bool`
`def test_hmac_roundtrip()`
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, items, _findings = ScopeChecker.check_scope(intent, parsed)
        assert passed is True
        detail_items = [i for i in items if i.classification == ScopeClassification.NECESSARY_IMPLEMENTATION_DETAIL]
        assert len(detail_items) > 0

    def test_unrequested_feature_rejected(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
`def sign_token(payload: str, secret: str) -> str`
`def verify_token(token: str, secret: str) -> bool`
`def refresh_token(token: str) -> str`
`def validate_expiry(exp_timestamp: int) -> bool`
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, items, _findings = ScopeChecker.check_scope(intent, parsed)
        assert passed is False
        unrequested = [i for i in items if i.classification == ScopeClassification.UNREQUESTED_FEATURE]
        assert len(unrequested) >= 1


class TestContextContaminationChecker:
    def test_historical_rag_concept_leak_detected(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)

        vault_notes = "Historical architecture: System relies on JWT tokens with HS256 headers."
        spec_md = """
# Specification
`def sign_token(payload: str) -> str`
Uses compact JWT format inspired by vault documentation.
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, contaminations, _findings = ContextContaminationChecker.check_contamination(
            intent=intent,
            parsed_spec=parsed,
            vault_context=vault_notes
        )
        assert passed is False
        rag_items = [c for c in contaminations if c.source == ContaminationSource.HISTORICAL_RAG]
        assert len(rag_items) > 0

    def test_mock_package_import_detected(self) -> None:
        prompt = "Create an HMAC token module."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
```python
import litellm
import langgraph
```
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, contaminations, _findings = ContextContaminationChecker.check_contamination(
            intent=intent,
            parsed_spec=parsed
        )
        assert passed is False
        prev_task_items = [c for c in contaminations if c.source == ContaminationSource.PREVIOUS_TASK]
        assert len(prev_task_items) >= 1


class TestSemanticAuditor:
    def test_paraphrased_jwt_without_acronym_detected(self) -> None:
        prompt = "Create an HMAC token signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Specification
The token format consists of a base64url encoded header containing algorithm declaration,
followed by payload, followed by signature: `header.payload.signature`.
It includes a dictionary containing exp timestamp.
"""
        parsed = SpecificationParser.parse(spec_md)
        passed, findings, _assumptions = SemanticAuditor.audit_semantics(intent, parsed)
        assert passed is False
        assert any("header.payload.signature" in f.description or "exp" in f.description for f in findings)


class TestAuditEngineAndPolicy:
    def test_compliant_spec_passes_audit(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Technical Specification: HMAC-SHA256 Token Manager

## Functions
`def sign_token(payload: str, secret: str) -> str`
Signs payload with HMAC-SHA256 and formats as `<b64_payload>.<b64_signature>`.

`def verify_token(token: str, secret: str) -> bool`
Validates signature using `hmac.compare_digest()`.

## Tests
Pytest unit tests verifying valid token signature, altered token rejection, and malformed inputs.
"""
        report = AuditEngine.audit_specification(intent=intent, spec_md=spec_md)
        assert report.outcome == AuditOutcome.PASSED
        assert report.passed is True
        assert report.coverage_score >= 0.8
        assert len(report.prohibition_violations) == 0

    def test_jwt_spec_rejected_with_actionable_feedback(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Technical Specification
`def sign_token(payload: str) -> str`
`def validate_jwt(token: str) -> bool`
Includes claims dictionary with exp expiry validation.
"""
        report = AuditEngine.audit_specification(intent=intent, spec_md=spec_md)
        assert report.outcome == AuditOutcome.REJECTED
        assert report.passed is False
        assert len(report.prohibition_violations) > 0
        assert "PROHIBITION VIOLATIONS:" in report.actionable_feedback


class TestPhase5Governance:
    def test_format_specification_approval_display(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)

        spec_md = """
# Spec: HMAC Signer
`def sign_token(payload: str, secret: str) -> str`
`def verify_token(token: str, secret: str) -> bool`
"""
        audit_rep = AuditEngine.audit_specification(intent=intent, spec_md=spec_md)
        display = GovernanceEngine.format_specification_approval_display(
            task_id="test-task-001",
            user_prompt=prompt,
            spec_md=spec_md,
            intent=intent,
            audit_report=audit_rep,
            retrieved_skills=[{"title": "Safe HMAC Procedure"}],
            retrieved_lessons=[{"title": "Negative Constraint Warning"}],
        )

        assert "Original User Objective" in display
        assert "Required Features" in display
        assert "Prohibited Features (Strict Invariants)" in display
        assert "Autonomous Audit Result" in display
        assert "Retrieved Experience Summary" in display
        assert "Safe HMAC Procedure" in display

    def test_human_approval_cannot_override_hard_prohibition(self) -> None:
        prompt = "Create an HMAC token signer. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)

        bad_spec = "def validate_jwt(token: str):\n    pass"
        is_valid, reasons = GovernanceEngine.validate_specification_approval(
            spec_md=bad_spec,
            intent=intent,
            audit_report={"outcome": "REJECTED"}
        )
        assert is_valid is False
        assert any("IMMUTABLE CONSTRAINT VIOLATION" in r or "Autonomous audit outcome" in r for r in reasons)
