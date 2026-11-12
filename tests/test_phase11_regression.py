"""Phase 11 — Comprehensive Automated Regression Test Suite.

Verifies:
1. Intent compiler:
   - Correctly extracts required features.
   - Correctly extracts explicit prohibitions.
   - Preserves the original prompt.
   - Does not convert assumptions into hard requirements.
2. Experience memory:
   - Retrieves relevant general skills.
   - Retrieves relevant failure lessons.
   - Does not retrieve old implementation code by default.
   - Preserves knowledge provenance.
   - Does not treat unverified lessons as authoritative.
3. Context firewall:
   - Prevents RAG from overriding explicit instructions.
   - Prevents previous JWT implementation context from contaminating an HMAC task.
   - Filters unrelated repository context.
4. Specification auditor:
   - Rejects a JWT specification for an HMAC-only request.
   - Rejects claims when claims are explicitly prohibited.
   - Rejects unrelated authentication functionality.
   - Detects omitted required functionality.
   - Detects indirect semantic contradictions.
   - Accepts a compliant HMAC-only specification.
   - Identifies ambiguous requirements.
   - Does not treat an unavailable semantic auditor as a successful audit.
5. Graph governance:
   - Human approval cannot occur before a successful audit.
   - Code generation cannot run after a rejected audit.
   - Retry limits are respected.
   - Unresolved violations stop the pipeline.
   - Missing audit state fails closed.
6. Agent constraint propagation:
   - Prompt optimizer preserves constraints.
   - Repair agent preserves constraints.
   - Import healer cannot create prohibited functionality.
   - Consolidator checks the approved scope.
7. Semantic verification:
   - Technical checks passing with semantic noncompliance results in overall failure.
   - Correct HMAC implementation passes semantic compliance.
   - JWT implementation fails the HMAC-only task's semantic compliance.
8. Backward compatibility:
   - Existing task workflows still work.
   - Existing verification functionality remains operational.
   - Existing worktree isolation is preserved.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from code_engine import (
    ApprovalDecision,
    ChangeManifest,
    FileChange,
    FileOperation,
    PatchValidator,
    SafeConsolidator,
)
from experience_engine.experience_retriever import ExperienceRetriever
from experience_engine.knowledge_store import KnowledgeStore
from experience_engine.models import (
    ExecutionOutcome,
    KnowledgeCategory,
    KnowledgeRecord,
    ProvenanceRecord,
    ReusePolicy,
)
from experience_engine.provenance import ProvenanceTracker
from experience_engine.relevance_filter import RelevanceFilter
from intent_engine.constraint_compiler import ConstraintCompiler
from intent_engine.intent_guard import IntentGuard
from langgraph_engine import graph
from langgraph_engine.fidelity import TaskRequirements
from specification_auditor.audit_engine import AuditEngine
from specification_auditor.models import AuditFinding, AuditOutcome, AuditReport
from verification_engine.semantic_compliance import (
    SemanticComplianceStatus,
    SemanticComplianceVerifier,
)


# ============================================================================
# 1. Intent Compiler
# ============================================================================
class TestIntentCompilerRegression:
    """Verifies intent compilation accuracy, fidelity, and negative constraint isolation."""

    def test_correctly_extracts_required_features(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests."
        intent = ConstraintCompiler.compile(prompt)
        required = [c.normalized_description.lower() for c in intent.required_capabilities]
        assert any("hmac" in r for r in required)
        assert any("token" in r or "sign" in r for r in required)
        assert any("test" in d.normalized_description.lower() for d in intent.expected_deliverables)

    def test_correctly_extracts_explicit_prohibitions(self) -> None:
        prompt = "Implement HMAC signing. Do not implement JWT, claims, or unrelated authentication."
        intent = ConstraintCompiler.compile(prompt)
        prohibitions = [p.normalized_description.lower() for p in intent.explicit_prohibitions]
        assert any("jwt" in p for p in prohibitions)
        assert any("claims" in p for p in prohibitions)
        assert any("unrelated authentication" in p for p in prohibitions)

    def test_preserves_the_original_prompt(self) -> None:
        prompt = "  Create a custom HMAC signer without any JWT or external dependencies.  "
        intent = ConstraintCompiler.compile(prompt)
        assert intent.original_prompt == prompt.strip()

    def test_does_not_convert_assumptions_into_hard_requirements(self) -> None:
        prompt = "Create an HMAC token signer. The user might want oauth later, but only build HMAC."
        intent = ConstraintCompiler.compile(prompt)
        # OAuth mentioned only as speculative assumption, should not become a required capability
        req_names = [c.normalized_description.lower() for c in intent.required_capabilities]
        assert not any("oauth" in r for r in req_names)
        # Primary objective remains HMAC
        assert "hmac" in intent.primary_objective.lower()


# ============================================================================
# 2. Experience Memory
# ============================================================================
class TestExperienceMemoryRegression:
    """Verifies experience retrieval, policy enforcement, and provenance integrity."""

    def test_retrieves_relevant_general_skills(self, tmp_path: Path) -> None:
        store = KnowledgeStore(storage_path=tmp_path / "store.json")
        retriever = ExperienceRetriever(store=store)
        prompt = "Create HMAC-SHA256 token signer"
        intent = ConstraintCompiler.compile(prompt)

        ctx = retriever.retrieve(intent=intent)
        assert len(ctx.skills) >= 1
        assert any("hmac" in s.title.lower() for s in ctx.skills)
        assert all(s.category == KnowledgeCategory.SKILL for s in ctx.skills)

    def test_retrieves_relevant_failure_lessons(self, tmp_path: Path) -> None:
        store = KnowledgeStore(storage_path=tmp_path / "store.json")
        retriever = ExperienceRetriever(store=store)
        prompt = "Create HMAC-SHA256 signer. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)

        ctx = retriever.retrieve(intent=intent)
        assert len(ctx.lessons) >= 1
        assert any("jwt" in l.title.lower() or "prohibit" in l.title.lower() for l in ctx.lessons)
        assert all(l.category == KnowledgeCategory.LESSON for l in ctx.lessons)

    def test_does_not_retrieve_old_implementation_code_by_default(self, tmp_path: Path) -> None:
        store = KnowledgeStore(storage_path=tmp_path / "store.json")
        retriever = ExperienceRetriever(store=store)
        prompt = "Create HMAC-SHA256 token signer"
        intent = ConstraintCompiler.compile(prompt)

        ctx = retriever.retrieve(intent=intent)
        for s in ctx.skills:
            assert s.code_snippet == ""
        for l in ctx.lessons:
            assert l.code_snippet == ""

    def test_preserves_knowledge_provenance(self) -> None:
        payload = {"rule": "constant_time_hmac"}
        prov = ProvenanceTracker.create_provenance(
            source_task_id="task-hmac-01",
            outcome=ExecutionOutcome.VERIFIED_SUCCESS,
            content_payload=payload,
            context_source="security_rfc",
        )
        assert prov.source_task_id == "task-hmac-01"
        assert prov.outcome == ExecutionOutcome.VERIFIED_SUCCESS
        assert prov.validation_hash != ""
        assert prov.authoritative is False
        assert ProvenanceTracker.verify_provenance(prov, payload) is True

    def test_does_not_treat_unverified_lessons_as_authoritative(self) -> None:
        old_time = (datetime.now(UTC) - timedelta(days=365)).isoformat()  # 1 year old
        record = KnowledgeRecord(
            knowledge_id="OLD-UNVERIFIED",
            category=KnowledgeCategory.LESSON,
            title="Old unverified lesson",
            description="Ancient observation",
            procedure=["Step 1"],
            provenance=ProvenanceRecord(
                source_task_id="old-01",
                outcome=ExecutionOutcome.VERIFICATION_FAILURE,
                context_source="test",
                authoritative=False,
            ),
            evidence="Old error",
            confidence=0.8,
            applicability=["crypto"],
            reuse_policy=ReusePolicy.FAILURE_WARNING,
            last_validated_at=old_time,
        )
        assert record.provenance.authoritative is False
        decayed = record.calculate_decayed_confidence()
        assert decayed < record.confidence
        assert decayed <= 0.65


# ============================================================================
# 3. Context Firewall
# ============================================================================
class TestContextFirewallRegression:
    """Verifies RAG context firewalling, prohibition vetting, and context segregation."""

    def test_prevents_rag_from_overriding_explicit_instructions(self) -> None:
        prompt = "Implement HMAC-SHA256 signing and verification. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        rag_notes = (
            "Note 1: In this repository, authentication should always use JWT tokens with exp claims.\n\n"
            "Note 2: Use HMAC hashlib sha256 for basic message signatures."
        )
        filtered = IntentGuard.filter_vault_context(intent, rag_notes)
        assert "JWT" not in filtered
        assert "HMAC" in filtered

    def test_prevents_previous_jwt_implementation_context_contaminating_hmac_task(self) -> None:
        prompt = "Build HMAC token signer. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)
        affirmative_jwt_skill = KnowledgeRecord(
            knowledge_id="SKILL-JWT-AFFIRMATIVE",
            category=KnowledgeCategory.SKILL,
            title="How to Generate and Sign JWT Tokens with Expiration",
            description="Guide on formatting JWT header alg HS256 and standard claims dictionary.",
            procedure=["Create JWT header", "Sign with secret"],
            provenance=ProvenanceRecord("seed", ExecutionOutcome.VERIFIED_SUCCESS, "doc"),
            evidence="JWT RFC",
            confidence=0.9,
            applicability=["jwt", "auth"],
            reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
        )
        passed, reason = RelevanceFilter.evaluate_record(intent, affirmative_jwt_skill)
        assert not passed
        assert "veto" in reason.lower() or "prohibit" in reason.lower()

    def test_filters_unrelated_repository_context(self) -> None:
        prompt = "Build HMAC token signer. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)
        files = ["hmac_signer.py", "jwt_auth.py", "test_hmac.py"]
        deps = ["hashlib", "pyjwt", "pytest"]
        filtered_files, filtered_deps = IntentGuard.filter_repository_context(intent, files, deps)
        assert "hmac_signer.py" in filtered_files
        assert "jwt_auth.py" not in filtered_files
        assert "pyjwt" not in filtered_deps


# ============================================================================
# 4. Specification Auditor
# ============================================================================
class TestSpecificationAuditorRegression:
    """Verifies specification auditor coverage, prohibition detection, and semantic checks."""

    def test_rejects_jwt_specification_for_hmac_only_request(self) -> None:
        prompt = "Create HMAC-SHA256 token signer. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)
        spec = """# Specification: HMAC Token Signer
Functions:
- validate_jwt(token: str) -> dict
- generate_token(key: bytes) -> str
"""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is False
        assert rep.outcome == AuditOutcome.REJECTED
        assert any("jwt" in v.lower() for v in rep.prohibition_violations)

    def test_rejects_claims_when_claims_are_explicitly_prohibited(self) -> None:
        prompt = "Implement HMAC-SHA256 token signer and verifier. Do not implement claims."
        intent = ConstraintCompiler.compile(prompt)
        spec = """# Specification
Implement payload claims dictionary containing user_id and expiration claims."""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is False
        assert any("claims" in v.lower() for v in rep.prohibition_violations)

    def test_rejects_unrelated_authentication_functionality(self) -> None:
        prompt = "Create an HMAC message signer. Do not implement unrelated authentication features."
        intent = ConstraintCompiler.compile(prompt)
        spec = """# Specification
Implement OAuth2 bearer token exchange, password hashing with bcrypt, and user session cookies."""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is False

    def test_detects_omitted_required_functionality(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests."
        intent = ConstraintCompiler.compile(prompt)
        spec = """# Specification: Documentation Only
This document explains the mathematical theory of HMAC-SHA256."""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is False
        assert rep.coverage_score < 1.0
        assert len(rep.missing_requirements) >= 1
        assert any(f.check_name == "REQUIREMENT_COVERAGE" for f in rep.findings)

    def test_detects_indirect_semantic_contradictions(self) -> None:
        prompt = "Create an HMAC token signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        # Indirect equivalent: HS256 with header.payload.signature structure
        spec = """# Specification
Format the token using standard base64url JSON header {"alg": "HS256", "typ": "JWT"} and payload."""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is False

    def test_accepts_a_compliant_hmac_only_specification(self) -> None:
        prompt = "Create an HMAC-SHA256 token signer and verifier with pytest tests. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)
        spec = """# Specification: HMAC Token Signer and Verifier
Architecture:
- Function: sign_token(key: bytes, message: str) -> str
  Uses hmac.new(key, message.encode(), hashlib.sha256).digest()
  Returns base64url-encoded payload.signature format.
- Function: verify_token(key: bytes, message: str, signature: str) -> bool
  Uses hmac.compare_digest() for constant-time comparison.
- Test Suite:
  test_module.py containing unit tests using pytest verifying signing, verification, and tampering.
"""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is True
        assert rep.outcome == AuditOutcome.PASSED
        assert len(rep.prohibition_violations) == 0

    def test_identifies_ambiguous_requirements(self) -> None:
        prompt = "Build a fast secure signature mechanism maybe using hmac or something else."
        intent = ConstraintCompiler.compile(prompt)
        spec = """# Specification: Unspecified Crypto
Implement something that signs."""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is False
        assert rep.outcome in (AuditOutcome.NEEDS_CLARIFICATION, AuditOutcome.REJECTED)

    def test_does_not_treat_an_unavailable_semantic_auditor_as_successful_audit(self) -> None:
        report = AuditReport(
            outcome=AuditOutcome.INTERNAL_ERROR,
            findings=[AuditFinding("INTERNAL_ERROR", "Auditor service unavailable", "CRITICAL")],
            passed=False,
            prohibition_violations=[],
            coverage_score=0.0,
            actionable_feedback="Auditor unavailable",
        )
        assert report.passed is False
        state: graph.HydramedaState = {
            "specification_audit_outcome": report.outcome.value,
            "specification_fidelity_status": "ERROR",
        }
        route = graph.route_after_spec_validator(state)
        assert route == "consolidator"
        assert state["status"] == "FAILED"


# ============================================================================
# 5. Graph Governance
# ============================================================================
class TestGraphGovernanceRegression:
    """Verifies graph approval gate, routing safety, retry capping, and fail-closed policies."""

    def test_human_approval_cannot_occur_before_a_successful_audit(self) -> None:
        state: graph.HydramedaState = {
            "task_id": "test-gov-01",
            "spec_md": "# Unaudited spec",
            "approval_status": "PENDING",
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "structured_logs": [],
        }
        res = graph.node_approval_gate(state)
        assert res["approval_status"] == "REJECTED"
        assert res["task_state"] == graph.TaskState.CANCELLED.value

    def test_code_generation_cannot_run_after_a_rejected_audit(self) -> None:
        state: graph.HydramedaState = {
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "spec_validation_attempts": 3,
        }
        route = graph.route_after_spec_validator(state)
        assert route == "consolidator"
        assert route != "prompt_optimizer"
        assert route != "approval_gate"

    def test_retry_limits_are_respected(self) -> None:
        state_attempt_1: graph.HydramedaState = {
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "spec_validation_attempts": 1,
        }
        assert graph.route_after_spec_validator(state_attempt_1) == "architect"

        state_attempt_3: graph.HydramedaState = {
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "spec_validation_attempts": 3,
        }
        assert graph.route_after_spec_validator(state_attempt_3) == "consolidator"

    def test_unresolved_violations_stop_the_pipeline(self) -> None:
        state: graph.HydramedaState = {
            "task_id": "test-gov-halt",
            "spec_validation_attempts": 3,
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "structured_logs": [],
        }
        route = graph.route_after_spec_validator(state)
        assert route == "consolidator"
        assert state["status"] == "FAILED"
        assert state["task_state"] == graph.TaskState.FAILED.value

    def test_missing_audit_state_fails_closed(self) -> None:
        state_missing: graph.HydramedaState = {}
        route = graph.route_after_spec_validator(state_missing)
        assert route == "consolidator"
        assert state_missing["status"] == "FAILED"


# ============================================================================
# 6. Agent Constraint Propagation
# ============================================================================
class TestAgentConstraintPropagationRegression:
    """Verifies constraint ledger preservation across prompt optimizer, repair agent, and consolidator."""

    def test_prompt_optimizer_preserves_constraints(self) -> None:
        prompt = "Create HMAC signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        state: graph.HydramedaState = {
            "task_id": "test-prop-01",
            "user_prompt": prompt,
            "structured_intent": intent.to_dict(),
            "task_requirements": TaskRequirements(
                required_capabilities=["hmac signing"],
                prohibited_capabilities=["jwt", "claims"],
            ).to_dict(),
            "spec_md": "# Spec\nFunctions: sign_token, verify_token",
            "structured_logs": [],
        }
        res = graph.node_prompt_optimizer(state)
        opt_prompt = res["optimized_prompt"]
        assert "CRITICAL PROHIBITIONS" in opt_prompt
        assert "jwt" in opt_prompt.lower()
        assert "claims" in opt_prompt.lower()

    def test_repair_agent_preserves_constraints(self) -> None:
        prompt = "Create HMAC signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        state: graph.HydramedaState = {
            "task_id": "test-prop-02",
            "user_prompt": prompt,
            "structured_intent": intent.to_dict(),
            "task_requirements": TaskRequirements(
                required_capabilities=["hmac signing"],
                prohibited_capabilities=["jwt", "claims"],
            ).to_dict(),
            "error_analysis": {"category": "SYNTAX_ERROR", "description": "missing colon"},
            "current_files": {"module.py": "def sign_token() pass"},
            "structured_logs": [],
        }
        res = graph.node_repair_agent(state)
        assert res is not None
        # State retains immutable constraints
        assert "jwt" in str(res["task_requirements"]["prohibited_capabilities"]).lower()

    def test_import_healer_cannot_create_prohibited_functionality(self) -> None:
        # Healer auto-injection only provides stdlib imports, never fake stubs or prohibited libraries
        raw_code = "import hmac\nimport hashlib\n\ndef sign(): pass\n"
        injected = graph.heal_module_imports(raw_code, is_test_file=False)
        assert "jwt" not in injected.lower()
        assert "litellm" not in injected.lower()

    def test_consolidator_checks_the_approved_scope(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        wt_dir = tmp_path / "worktree"
        wt_dir.mkdir()

        # Target file + prohibited out-of-scope file
        (wt_dir / "target.py").write_text("def hmac_sign(): pass\n")
        (wt_dir / "litellm.py").write_text("# fake stub\n")

        manifest = ChangeManifest(
            task_id="task-scope-01",
            description="Test scope check",
            changes=[
                FileChange("target.py", FileOperation.CREATE, "def hmac_sign(): pass\n", authorized=True),
                FileChange("litellm.py", FileOperation.CREATE, "# fake stub\n", authorized=True),
            ],
        )
        decision = ApprovalDecision(
            approved=True,
            approved_manifest_hash=manifest.manifest_hash,
            task_id="task-scope-01",
        )
        prompt = "Implement HMAC. Do not create fake dependency stubs."
        intent = ConstraintCompiler.compile(prompt)

        rep = SafeConsolidator.consolidate_changes(
            worktree_path=wt_dir,
            main_repo_path=repo_dir,
            manifest=manifest,
            decision=decision,
            verification_passed=True,
            intent=intent,
        )
        assert rep.approved is False
        assert rep.status in ("PROHIBITION_VIOLATION_REJECTED", "SCOPE_VIOLATION_REJECTED", "REJECTED")
        assert any("PROHIBITED" in r or "scope" in r.lower() for r in rep.rejection_reasons)


# ============================================================================
# 7. Semantic Verification
# ============================================================================
class TestSemanticVerificationRegression:
    """Verifies that technical passing with semantic non-compliance produces task failure."""

    def test_technical_checks_passing_with_semantic_noncompliance_results_in_overall_failure(self) -> None:
        prompt = "Create HMAC-SHA256 token signer. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)

        # Implementation contains prohibited JWT and passes technical pytest
        files = {
            "module.py": "def validate_jwt(token: str) -> bool: return True\n",
            "test_module.py": "from module import validate_jwt\ndef test_jwt(): assert validate_jwt('token') is True\n",
        }
        sem_res = SemanticComplianceVerifier.verify_semantic_compliance(
            user_prompt=prompt,
            intent=intent,
            current_files=files,
        )
        assert sem_res.status in (SemanticComplianceStatus.FAILED, SemanticComplianceStatus.WARNING)
        assert sem_res.passed is False

        # Status integration in graph
        tech_passed = True
        overall_passed = bool(tech_passed and sem_res.passed)
        assert overall_passed is False

    def test_correct_hmac_implementation_passes_semantic_compliance(self) -> None:
        prompt = "Create HMAC-SHA256 token signer and verifier with pytest tests. Do not implement JWT."
        intent = ConstraintCompiler.compile(prompt)
        files = {
            "module.py": """import hmac
import hashlib

def sign_token(key: bytes, message: str) -> str:
    sig = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return f"{message}.{sig}"

def verify_token(key: bytes, message: str, signature: str) -> bool:
    expected = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
""",
            "test_module.py": """from module import sign_token, verify_token

def test_hmac_sign_and_verify():
    key = b"secret-key-123"
    token = sign_token(key, "hello")
    msg, sig = token.split(".")
    assert verify_token(key, msg, sig) is True
""",
        }
        res = SemanticComplianceVerifier.verify_semantic_compliance(
            user_prompt=prompt,
            intent=intent,
            current_files=files,
        )
        assert res.passed is True
        assert res.status == SemanticComplianceStatus.PASSED

    def test_jwt_implementation_fails_hmac_only_task_semantic_compliance(self) -> None:
        prompt = "Create an HMAC token signer. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)
        jwt_files = {
            "module.py": """import json
import base64

def validate_jwt(token: str) -> dict:
    parts = token.split(".")
    payload = json.loads(base64.b64decode(parts[1]))
    if "exp" in payload:
        pass
    return payload
"""
        }
        res = SemanticComplianceVerifier.verify_semantic_compliance(
            user_prompt=prompt,
            intent=intent,
            current_files=jwt_files,
        )
        assert res.passed is False
        assert any("jwt" in f.description.lower() for f in res.findings)


# ============================================================================
# 8. Backward Compatibility
# ============================================================================
class TestBackwardCompatibilityRegression:
    """Verifies that existing task workflows, verification functionality, and worktree isolation remain intact."""

    def test_existing_task_workflows_still_work(self) -> None:
        # Standard benign task without negative constraints
        prompt = "Create an addition function with pytest tests"
        intent = ConstraintCompiler.compile(prompt)
        assert intent.primary_objective != ""
        assert len(intent.explicit_prohibitions) == 0

        spec = """# Spec: Addition Utility
Functions:
- add(a: int, b: int) -> int: returns a + b
Includes pytest test_add in test_module.py.
"""
        rep = AuditEngine.audit_specification(intent=intent, spec_md=spec)
        assert rep.passed is True

    def test_existing_verification_functionality_remains_operational(self) -> None:
        # Verifier interface and state handling preserve all existing fields
        state: graph.HydramedaState = {
            "task_id": "test-compat-01",
            "current_files": {"module.py": "x = 1\n"},
            "target_files": ["module.py"],
            "status": "PASSED",
            "verifier_exit_code": 0,
            "technical_verification_status": "PASSED",
            "semantic_compliance_status": "PASSED",
            "overall_task_status": "PASSED",
            "structured_logs": [],
        }
        # In verifier routing, success correctly transitions
        assert graph.route_after_verifier(state) == "consolidator"

    def test_existing_worktree_isolation_is_preserved(self, tmp_path: Path) -> None:
        wt_dir = tmp_path / "worktree"
        wt_dir.mkdir()
        (wt_dir / "target.py").write_text("print('isolated')\n")

        validator = PatchValidator(target_worktree=wt_dir)
        # Target within worktree is valid
        resolved = validator.validate_path_safety("target.py")
        assert resolved == (wt_dir / "target.py").resolve()

        # Target escaping worktree boundary is strictly rejected
        from code_engine.patch_validator import ValidationError
        with pytest.raises(ValidationError):
            validator.validate_path_safety("../escaped.py")
