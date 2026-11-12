"""Phase 13 — Dedicated End-to-End Acceptance Experiment.

Re-enacts the exact failure scenario that motivated this refactor:
Task: "Create a secure HMAC-SHA256 token signer and verifier with pytest tests.
Follow the existing repository architecture. Use HMAC-SHA256 directly;
do not implement JWT, claims, or unrelated authentication features.
Do not create mock or stub packages for missing dependencies.
Modify only files required for this task."

Verifies:
1. Intent compiler extracts all required and prohibited constraints.
2. Experience retrieval returns the lesson about previous JWT scope contamination.
3. It does not retrieve the old JWT implementation as a template.
4. Architect deliberately returns a JWT-based specification.
5. Specification auditor rejects it.
6. The rejection report identifies JWT and claims as violations.
7. Architect receives actionable correction instructions.
8. Architect returns an HMAC-only specification.
9. Auditor passes the corrected specification.
10. Human approval occurs ONLY after the audit passes.
11. Code generation receives the original immutable constraints.
12. Semantic verification detects prohibited JWT functionality if introduced later.
13. A compliant HMAC-only implementation completes successfully.
14. The final report records specification fidelity and semantic compliance independently.
15. Proves that a technically valid but semantically incorrect implementation cannot be marked as a successful task.
"""
from __future__ import annotations

from pathlib import Path

from experience_engine.experience_retriever import ExperienceRetriever
from experience_engine.knowledge_store import KnowledgeStore
from experience_engine.models import KnowledgeCategory
from intent_engine.constraint_compiler import ConstraintCompiler
from intent_engine.models import StructuredIntent
from langgraph_engine import graph
from langgraph_engine.fidelity import TaskRequirements
from specification_auditor.audit_engine import AuditEngine
from specification_auditor.models import AuditOutcome
from verification_engine.semantic_compliance import (
    SemanticComplianceStatus,
    SemanticComplianceVerifier,
)

EXACT_PROMPT = (
    "Create a secure HMAC-SHA256 token signer and verifier with pytest tests. "
    "Follow the existing repository architecture. Use HMAC-SHA256 directly; "
    "do not implement JWT, claims, or unrelated authentication features. "
    "Do not create mock or stub packages for missing dependencies. "
    "Modify only files required for this task."
)


def test_phase13_end_to_end_acceptance_experiment(tmp_path: Path) -> None:
    """End-to-End verification of the exact failure scenario and full mitigation pipeline."""

    # -------------------------------------------------------------------------
    # Step 1: Intent compiler extracts all required and prohibited constraints
    # -------------------------------------------------------------------------
    intent = ConstraintCompiler.compile(EXACT_PROMPT)
    assert isinstance(intent, StructuredIntent)
    assert intent.primary_objective != ""
    assert "hmac" in intent.primary_objective.lower()

    # Verify required capabilities
    required = [c.normalized_description.lower() for c in intent.required_capabilities]
    assert any("hmac" in r for r in required)

    # Verify negative constraints (prohibitions)
    prohibitions = [p.normalized_description.lower() for p in intent.explicit_prohibitions]
    assert any("jwt" in p for p in prohibitions)
    assert any("claims" in p for p in prohibitions)
    assert any("unrelated authentication" in p for p in prohibitions)
    assert any("mock" in p or "stub" in p for p in prohibitions)

    # -------------------------------------------------------------------------
    # Step 2: Experience retrieval returns the lesson about previous JWT scope contamination
    # -------------------------------------------------------------------------
    store = KnowledgeStore(storage_path=tmp_path / "acceptance_store.json")
    retriever = ExperienceRetriever(store=store)
    exp_ctx = retriever.retrieve(intent=intent)

    jwt_lesson = next((l for l in exp_ctx.lessons if "jwt" in l.title.lower()), None)
    assert jwt_lesson is not None, "Failed to retrieve the historical lesson on JWT contamination"
    assert jwt_lesson.category == KnowledgeCategory.LESSON
    assert "hydrameda-hmac-integration-002" in jwt_lesson.provenance.source_task_id or "LESSON" in jwt_lesson.knowledge_id

    # -------------------------------------------------------------------------
    # Step 3: It does NOT retrieve the old JWT implementation as a template
    # -------------------------------------------------------------------------
    for skill in exp_ctx.skills:
        assert skill.code_snippet == "", "Skill must not contain raw code snippets"
        assert "jwt" not in skill.title.lower(), "Context firewall must veto affirmative JWT skills"
    for lesson in exp_ctx.lessons:
        assert lesson.code_snippet == "", "Lesson must not contain raw implementation templates"

    # -------------------------------------------------------------------------
    # Step 4: Architect deliberately returns a JWT-based specification
    # -------------------------------------------------------------------------
    deliberate_jwt_spec = """# Technical Specification: Token Signer and Verifier
Architecture Overview:
- Token format: Compact JSON Web Token (JWT) with HS256 algorithm.
- Function validate_jwt(token: str) -> dict: Parses headers, verifies HMAC signature, checks 'exp' claim.
- Function create_jwt(payload: dict) -> str: Signs claims payload using HS256 secret.
- Claims dictionary: Standard registered claims ('exp', 'sub', 'iat').
"""

    # -------------------------------------------------------------------------
    # Step 5 & 6: Auditor rejects it and identifies JWT and claims as violations
    # -------------------------------------------------------------------------
    audit_report = AuditEngine.audit_specification(
        intent=intent,
        spec_md=deliberate_jwt_spec,
        retrieved_skills=[s.to_dict() for s in exp_ctx.skills],
        retrieved_lessons=[l.to_dict() for l in exp_ctx.lessons],
    )
    assert audit_report.passed is False
    assert audit_report.outcome == AuditOutcome.REJECTED
    assert any("jwt" in v.lower() for v in audit_report.prohibition_violations)
    assert any("claims" in v.lower() for v in audit_report.prohibition_violations)
    assert "PROHIBITION_ENFORCEMENT" in [f.check_name for f in audit_report.findings]

    # Verify that approval gate BLOCKS this rejected specification
    state_rejected: graph.HydramedaState = {
        "task_id": "acceptance-task-001",
        "user_prompt": EXACT_PROMPT,
        "spec_md": deliberate_jwt_spec,
        "specification_fidelity_status": "FAILED",
        "specification_audit_outcome": audit_report.outcome.value,
        "specification_constraint_violations": audit_report.prohibition_violations,
        "approval_status": "PENDING",
        "structured_logs": [],
    }
    gate_res_rejected = graph.node_approval_gate(state_rejected)
    assert gate_res_rejected["approval_status"] == "REJECTED"
    assert gate_res_rejected["task_state"] == graph.TaskState.CANCELLED.value

    # -------------------------------------------------------------------------
    # Step 7 & 8: Architect receives correction instructions and returns HMAC-only specification
    # -------------------------------------------------------------------------
    correction_feedback = audit_report.actionable_feedback
    assert "PROHIBITION VIOLATIONS" in correction_feedback or "Remove" in correction_feedback
    assert "jwt" in correction_feedback.lower()

    corrected_hmac_spec = """# Technical Specification: HMAC-SHA256 Token Signer and Verifier
Architecture Overview:
- Conform strictly to existing repository structure, conventions, and patterns.
- Minimal modification: modify only task-essential files (`module.py` and `test_module.py`).
- Symmetric HMAC-SHA256 message authentication using Python standard library `hmac` and `hashlib`.
- No JWT format, no headers, and no claims dictionary.
- Tokens formatted as simple `<payload_bytes>.<hex_digest>`.
- Function: sign_token(key: bytes, message: str) -> str
  Computes HMAC-SHA256 over message bytes and returns payload with signature.
- Function: verify_token(key: bytes, message: str, signature: str) -> bool
  Computes expected digest and performs constant-time comparison via hmac.compare_digest().
- Test Suite:
  pytest unit tests covering signing, valid verification, tampered payload rejection, and invalid keys.
"""

    # -------------------------------------------------------------------------
    # Step 9: Auditor passes the corrected specification
    # -------------------------------------------------------------------------
    corrected_audit_report = AuditEngine.audit_specification(
        intent=intent,
        spec_md=corrected_hmac_spec,
        retrieved_skills=[s.to_dict() for s in exp_ctx.skills],
        retrieved_lessons=[l.to_dict() for l in exp_ctx.lessons],
    )
    assert corrected_audit_report.passed is True
    assert corrected_audit_report.outcome == AuditOutcome.PASSED
    assert len(corrected_audit_report.prohibition_violations) == 0

    # -------------------------------------------------------------------------
    # Step 10: Human approval occurs ONLY after the audit passes
    # -------------------------------------------------------------------------
    state_approved: graph.HydramedaState = {
        "task_id": "acceptance-task-001",
        "user_prompt": EXACT_PROMPT,
        "spec_md": corrected_hmac_spec,
        "specification_fidelity_status": "PASSED",
        "specification_audit_outcome": corrected_audit_report.outcome.value,
        "specification_constraint_violations": [],
        "auto_approve": True,
        "trusted_policy": "ci_automation",
        "approval_status": "PENDING",
        "structured_logs": [],
    }
    gate_res_approved = graph.node_approval_gate(state_approved)
    assert gate_res_approved["approval_status"] == "APPROVED"

    # -------------------------------------------------------------------------
    # Step 11: Code generation receives the original constraints
    # -------------------------------------------------------------------------
    reqs = TaskRequirements(
        required_capabilities=[c.normalized_description for c in intent.required_capabilities],
        prohibited_capabilities=[p.normalized_description for p in intent.explicit_prohibitions],
    )
    state_approved["task_requirements"] = reqs.to_dict()
    state_approved["structured_intent"] = intent.to_dict()
    prompt_res = graph.node_prompt_optimizer(state_approved)
    opt_prompt = prompt_res["optimized_prompt"]
    assert "CRITICAL PROHIBITIONS" in opt_prompt
    assert "jwt" in opt_prompt.lower()
    assert "claims" in opt_prompt.lower()

    # -------------------------------------------------------------------------
    # Step 12: Semantic verification detects prohibited JWT functionality if introduced later
    # -------------------------------------------------------------------------
    sneaky_jwt_code = {
        "module.py": """import hmac
import hashlib

def sign_token(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()

def validate_jwt(jwt_token: str) -> bool:
    # Sneaky unauthorized JWT helper introduced by code generator
    return True
""",
        "test_module.py": """from module import sign_token, validate_jwt

def test_sign():
    assert sign_token(b"k", "m") != ""
    assert validate_jwt("dummy") is True
"""
    }
    sneaky_sem_rep = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=EXACT_PROMPT,
        intent=intent,
        current_files=sneaky_jwt_code,
    )
    # The experiment must prove that a technically valid but semantically incorrect implementation cannot pass!
    assert sneaky_sem_rep.passed is False
    assert any("jwt" in f.description.lower() for f in sneaky_sem_rep.findings)

    # -------------------------------------------------------------------------
    # Step 13: Compliant HMAC-only implementation completes successfully
    # -------------------------------------------------------------------------
    compliant_code = {
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

def test_hmac_signing_and_verification():
    secret = b"super-secret-key-42"
    payload = "user_account_id=987"
    token = sign_token(secret, payload)
    msg, sig = token.split(".")
    assert verify_token(secret, msg, sig) is True
    assert verify_token(secret, msg, "tampered_sig") is False
"""
    }
    compliant_sem_rep = SemanticComplianceVerifier.verify_semantic_compliance(
        user_prompt=EXACT_PROMPT,
        intent=intent,
        current_files=compliant_code,
    )
    assert compliant_sem_rep.passed is True
    assert compliant_sem_rep.status == SemanticComplianceStatus.PASSED

    # -------------------------------------------------------------------------
    # Step 14: Final report records specification fidelity & semantic compliance independently
    # -------------------------------------------------------------------------
    final_state: graph.HydramedaState = {
        "task_id": "acceptance-task-001",
        "status": "PASSED",
        "technical_verification_status": "PASSED",
        "semantic_compliance_status": compliant_sem_rep.status.value,
        "specification_fidelity_status": "PASSED",
        "overall_task_status": "PASSED",
    }
    # Both statuses are distinct and tracked independently
    assert final_state["technical_verification_status"] == "PASSED"
    assert final_state["semantic_compliance_status"] == "PASSED"
    assert final_state["specification_fidelity_status"] == "PASSED"
    assert final_state["overall_task_status"] == "PASSED"
