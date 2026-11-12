"""
Comprehensive test suite for Phase 6 (Constraint Propagation) and
Phase 7 (Semantic Task-Compliance Verification Layer).

Verifies:
1. Valid code & tests satisfy all 6 semantic compliance checks.
2. Technical verification success never implies overall success when semantic compliance fails.
3. Prohibited features (JWT, claims) cause semantic compliance failure.
4. Prohibited fake dependency stubs (litellm.py, langgraph/) are blocked.
5. Ineffective tests (0 assertions or unexercised code) fail compliance.
6. Missing required capabilities fail compliance.
7. Unrelated/sensitive files fail compliance.
8. Statuses remain strictly separated (technical, semantic, fidelity, overall).
9. Consolidation rejects changesets with scope or prohibition violations.
10. Prompt optimizer and repair agents receive canonical constraints without weakening.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ENGINE_DIR = REPO_ROOT / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from code_engine import (
    ApprovalDecision,
    ChangeManifest,
    FileChange,
    FileOperation,
    SafeConsolidator,
)
from intent_engine import ConstraintCompiler
from langgraph_engine import graph
from verification_engine.semantic_compliance import (
    ComplianceCategory,
    SemanticComplianceStatus,
    SemanticComplianceVerifier,
)


# ============================================================================
# 1. Test Semantic Compliance Passes on Valid Implementation
# ============================================================================
def test_semantic_compliance_passes_on_valid_implementation():
    prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests. Do not implement JWT."
    intent = ConstraintCompiler.compile(prompt)

    spec_md = """# Technical Specification: HMAC Token Signer
Functions:
- sign_token(key: bytes, message: str) -> str
- verify_token(key: bytes, message: str, signature: str) -> bool
Includes pytest unit tests in test_module.py.
"""
    current_files = {
        "module.py": """import hmac
import hashlib

def sign_token(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()

def verify_token(key: bytes, message: str, signature: str) -> bool:
    expected = sign_token(key, message)
    return hmac.compare_digest(expected, signature)
""",
        "test_module.py": """import pytest
from module import sign_token, verify_token

def test_sign_and_verify_roundtrip():
    key = b"secret-key-123"
    msg = "payload-data"
    sig = sign_token(key, msg)
    assert verify_token(key, msg, sig) is True
    assert verify_token(key, "tampered", sig) is False
"""
    }

    report = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=prompt,
        intent=intent,
        spec_md=spec_md,
        current_files=current_files
    )

    assert report.passed is True
    assert report.status == SemanticComplianceStatus.PASSED
    assert len(report.missing_features) == 0
    assert len(report.prohibition_violations) == 0
    assert len(report.findings) == 0


# ============================================================================
# 2. Test Prohibited JWT Implementation Fails Semantic Compliance
# ============================================================================
def test_semantic_compliance_fails_when_prohibited_jwt_introduced():
    prompt = "Create an HMAC token signer. Do not implement JWT, claims, or unrelated auth features."
    intent = ConstraintCompiler.compile(prompt)

    current_files = {
        "module.py": """import hmac
import hashlib

class JWTError(Exception):
    pass

def validate_jwt(token: str, claims: dict) -> bool:
    if "exp" in claims:
        return True
    return False
""",
        "test_module.py": """from module import validate_jwt

def test_jwt():
    assert validate_jwt("tok", {"exp": 123}) is True
"""
    }

    report = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=prompt,
        intent=intent,
        spec_md="# Spec",
        current_files=current_files
    )

    assert report.passed is False
    assert report.status == SemanticComplianceStatus.FAILED
    assert len(report.prohibition_violations) > 0
    assert any("jwt" in v.lower() for v in report.prohibition_violations)


# ============================================================================
# 3. Test Prohibited Fake Dependency Stubs Are Blocked
# ============================================================================
def test_semantic_compliance_fails_on_fake_stub_packages():
    prompt = "Implement rate limiting utility with pytest tests."
    intent = ConstraintCompiler.compile(prompt)

    current_files = {
        "module.py": "def check_rate(): return True\n",
        "test_module.py": "from module import check_rate\ndef test_check(): assert check_rate() is True\n",
        "litellm.py": "# fake stub package to make imports pass\nclass LiteLLM: pass\n"
    }

    report = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=prompt,
        intent=intent,
        spec_md="# Spec",
        current_files=current_files
    )

    assert report.passed is False
    assert report.status == SemanticComplianceStatus.FAILED
    assert any("litellm.py" in v for v in report.prohibition_violations)


# ============================================================================
# 4. Test Ineffective Tests (0 Assertions) Fail Compliance
# ============================================================================
def test_semantic_compliance_fails_on_empty_or_trivial_tests():
    prompt = "Create an HMAC token signer with pytest unit tests."
    intent = ConstraintCompiler.compile(prompt)

    current_files = {
        "module.py": "def sign_token(k: bytes, m: str) -> str: return 'sig'\n",
        "test_module.py": """# Empty superficial test without assertions
def test_dummy():
    pass
"""
    }

    report = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=prompt,
        intent=intent,
        spec_md="# Spec",
        current_files=current_files
    )

    assert report.passed is False
    assert report.status == SemanticComplianceStatus.FAILED
    assert any(f.category == ComplianceCategory.TEST_EFFECTIVENESS.value for f in report.findings)
    assert any("0 assertions" in f.description for f in report.findings)


# ============================================================================
# 5. Test Missing Mandatory Features Fail Compliance
# ============================================================================
def test_semantic_compliance_fails_when_required_functionality_missing():
    prompt = "Create an HMAC-SHA256 token signer and token verifier with pytest tests."
    intent = ConstraintCompiler.compile(prompt)

    # Implements only sign_token, completely omits verifier
    current_files = {
        "module.py": "def sign_token(k: bytes, m: str) -> str: return 'sig'\n",
        "test_module.py": "from module import sign_token\ndef test_sign(): assert sign_token(b'k', 'm') == 'sig'\n"
    }

    report = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=prompt,
        intent=intent,
        spec_md="# Spec",
        current_files=current_files
    )

    assert report.passed is False
    assert report.status == SemanticComplianceStatus.FAILED
    assert len(report.missing_features) > 0


# ============================================================================
# 6. Test Unrelated Sensitive Files Fail Compliance
# ============================================================================
def test_semantic_compliance_fails_on_unauthorized_sensitive_file():
    prompt = "Create an addition function."
    intent = ConstraintCompiler.compile(prompt)

    current_files = {
        "module.py": "def add(a, b): return a + b\n",
        "test_module.py": "from module import add\ndef test_add(): assert add(1, 2) == 3\n",
        ".env": "SECRET_KEY=leaked_credentials\n"
    }

    report = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=prompt,
        intent=intent,
        spec_md="# Spec",
        current_files=current_files
    )

    assert report.passed is False
    assert any(f.category == ComplianceCategory.CHANGESET_JUSTIFICATION.value for f in report.findings)


# ============================================================================
# 7. Invariant: Technical Pass Does NOT Imply Overall Success
# ============================================================================
def test_technical_pass_does_not_imply_overall_success():
    """Verifies that when technical verifier passes (exit code 0), but code implements prohibited JWT,
    the overall status is FAILED and technical_verification_status != overall_task_status."""
    state: graph.HydramedaState = {
        "task_id": "test-semantic-gate-01",
        "user_prompt": "Create an HMAC token signer. Do not implement JWT.",
        "spec_md": "# Spec: HMAC Token Signer\nDefine sign_token().\nIncludes pytest tests.",
        "current_files": {
            # Code passes syntax, but introduces prohibited validate_jwt
            "module.py": "def validate_jwt(tok): return True\n",
            "test_module.py": "from module import validate_jwt\ndef test_jwt(): assert validate_jwt('t')\n"
        },
        "target_files": ["module.py", "test_module.py"],
        "repair_count": 0,
    }

    # Mock technical verifier to return 0 (all technical checks passed)
    graph.set_verifier_handler(lambda s: (0, "All 8 technical checks passed"))

    try:
        res = graph.node_sandbox_verifier(state)

        # Technical check succeeded
        assert res["technical_verification_status"] == "PASSED"
        assert res["verifier_exit_code"] == 0

        # BUT semantic compliance failed!
        assert res["semantic_compliance_status"] == "FAILED"
        assert res["status"] == "FAILED"
        assert res["overall_task_status"] == "FAILED"
        assert "Semantic task compliance failed" in res["pytest_stderr"]
    finally:
        graph.set_verifier_handler(None)


# ============================================================================
# 8. Test Status Separation in Execution Report
# ============================================================================
def test_status_separation_in_execution_report():
    """Verifies that technical_verification_status, semantic_compliance_status,
    specification_fidelity_status, and overall_task_status remain separate and distinct."""
    state: graph.HydramedaState = {
        "task_id": "test-status-separation",
        "user_prompt": "Create math add utility",
        "spec_md": "# Spec\nDefine add(a, b).",
        "status": "PASSED",
        "overall_task_status": "PASSED",
        "technical_verification_status": "PASSED",
        "semantic_compliance_status": "PASSED",
        "specification_fidelity_status": "PASSED",
        "auto_approve": True,
        "trusted_policy": "ci_automation",
        "current_files": {
            "module.py": "def add(a: int, b: int) -> int: return a + b\n"
        },
        "target_files": ["module.py"]
    }

    res = graph.node_consolidator(state)
    report = res["execution_report"]

    assert report["technical_verification_status"] == "PASSED"
    assert report["semantic_compliance_status"] == "PASSED"
    assert report["specification_fidelity_status"] == "PASSED"
    assert report["overall_task_status"] == "PASSED"


# ============================================================================
# 9. Test Consolidation Rejects Changeset Violating Scope or Prohibitions
# ============================================================================
def test_consolidation_rejects_scope_and_prohibition_violations(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    manifest = ChangeManifest(
        task_id="test-consolidation-scope",
        description="Test",
        changes=[
            FileChange("module.py", FileOperation.CREATE, "def validate_jwt(): pass", authorized=True),
            FileChange("litellm.py", FileOperation.CREATE, "class Stub: pass", authorized=True),
        ]
    )

    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        task_id="test-consolidation-scope"
    )

    prompt = "Create an HMAC token signer. Do not implement JWT."
    intent = ConstraintCompiler.compile(prompt)

    report = SafeConsolidator.consolidate_changes(
        worktree_path=repo,
        main_repo_path=repo,
        manifest=manifest,
        decision=decision,
        verification_passed=True,
        intent=intent
    )

    assert report.approved is False
    assert report.status in ("PROHIBITION_VIOLATION_REJECTED", "SCOPE_VIOLATION_REJECTED", "REJECTED")
    assert any("PROHIBITED" in r for r in report.rejection_reasons)


# ============================================================================
# 10. Test Prompt Optimizer & Repair Agent Receive Canonical Directives
# ============================================================================
def test_prompt_optimizer_propagates_all_canonical_constraints():
    prompt = "Implement HMAC-SHA256 signer. Do not implement JWT. Scope limited to module.py and tests."
    state: graph.HydramedaState = {
        "task_id": "test-prompt-prop",
        "user_prompt": prompt,
        "spec_md": "# Spec for HMAC",
    }

    res = graph.node_prompt_optimizer(state)
    optimized = res["optimized_prompt"]

    # Must contain all 5 canonical elements
    assert "ORIGINAL TASK OBJECTIVE:" in optimized
    assert "STRICT PROHIBITION DIRECTIVES (ABSOLUTE VETO):" in optimized
    assert "DO NOT IMPLEMENT, USE, OR INTRODUCE: JWT" in optimized
    assert "SCOPE BOUNDARIES" in optimized
    assert "RELEVANT ACCEPTANCE CRITERIA:" in optimized
    assert "ROLE DIRECTIVE FOR PROMPT OPTIMIZER:" in optimized
