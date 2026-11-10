"""
Hydrameda Specification Fidelity and Constraint Enforcement Regression Tests.

Validates:
 1. HMAC-SHA256 request cannot become JWT
 2. Explicit negative constraints are preserved
 3. RAG context cannot override user prompt
 4. Specifications are validated before approval gate
 5. Contradictions prevent code generation
 6. Architect retries after fidelity rejection and succeeds on valid revision
 7. Validation retry exhaustion (3 attempts) halts execution
 8. Prompt optimizer preserves negative constraints and does not treat prohibited libs as requested
 9. Repair agent preserves negative constraints
10. Valid non-JWT HMAC specification passes validation
11. Unrelated specifications pass without false positive regressions
12. Approval and governance compatibility is preserved
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure langgraph_engine is in sys.path
ENGINE_DIR = Path(__file__).resolve().parent.parent / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import graph
from fidelity import (
    NON_JWT_HMAC_SPECIFICATION,
    TaskRequirements,
    extract_task_requirements,
    validate_specification_fidelity,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# ============================================================================
# 1. Negative Constraint Extraction & Protocol Expansion Protection
# ============================================================================

def test_hmac_request_cannot_become_jwt():
    """Scenario 1: Prompt requesting HMAC-SHA256 cannot produce or pass a JWT specification."""
    prompt = (
        "Create a secure HMAC-SHA256 token signer and verifier with pytest tests. "
        "Use HMAC-SHA256 directly; do not implement JWT, claims, or unrelated authentication features."
    )
    jwt_spec = """# Technical Specification: Token Signer and Verifier
## Architecture
We implement JWT token signing using JSON Web Tokens.
- Class: JWTError(ValueError)
- Class: JWTInvalidSignature(JWTError)
- Header: {"alg": "HS256", "typ": "JWT"}
- Function: validate_jwt(token, secret)
- Function: validate_exp(payload)
Claims dictionary handles expiration.
"""
    report = validate_specification_fidelity(prompt, jwt_spec)
    assert report.passed is False
    assert len(report.violations) > 0
    assert any("JWT" in v for v in report.violations)
    assert report.actionable_feedback != ""


def test_explicit_negative_constraints_preserved():
    """Scenario 2: Explicit negative constraints in user prompt are parsed and strictly enforced."""
    prompt = "Create an HMAC signer. Do not implement JWT, claims, or mock packages."
    reqs = extract_task_requirements(prompt)

    assert any("JWT" in p for p in reqs.prohibited_capabilities)
    assert any("claims" in p.lower() for p in reqs.prohibited_capabilities)

    # Valid non-JWT spec must not trigger violations
    valid_spec = """# Technical Specification: HMAC Signer
## API
- sign_token(data, key): computes hmac sha256 and returns payload.signature
- verify_token(token, key): verifies signature with hmac.compare_digest
- Uses Python standard library hmac, hashlib, base64
"""
    report = validate_specification_fidelity(prompt, valid_spec, reqs)
    assert report.passed is True
    assert len(report.violations) == 0


def test_rag_context_cannot_override_user_prompt():
    """Scenario 3: Even when Obsidian RAG context contains JWT notes, HMAC prompt rejects JWT."""
    user_prompt = "Implement a secure HMAC token signer and verifier using standard library."
    rag_context = """
# Vault Context: Specs/bench-seceval-002.md
## JWT Authentication Engine
Defines encode_jwt and decode_jwt with HS256 algorithm and expiration claims.
"""
    # Spec seduced by RAG note into implementing JWT
    expanded_spec = """# Technical Specification
Following the vault context, we build a JWT token system:
- validate_jwt(token, key)
- claims verification with exp claim
- HS256 algorithm header
"""
    report = validate_specification_fidelity(user_prompt, expanded_spec, vault_context=rag_context)
    assert report.passed is False
    assert any("unrequested" in v.lower() or "jwt" in v.lower() for v in report.violations)


# ============================================================================
# 2. Graph Wiring, Validation Gate, and Bounded Revision Loop
# ============================================================================

def test_specifications_validated_before_approval():
    """Scenario 4: Graph executes spec_validator before reaching approval_gate."""
    visited_nodes: list[str] = []

    def mock_llm_handler(role: str, prompt: str):
        visited_nodes.append(role)
        # Return a valid HMAC specification
        return (
            """# Technical Specification: HMAC Token Signer
## Design
Format: <payload_b64>.<signature_b64>
Functions:
- sign_token(payload, secret_key)
- verify_token(token, secret_key)
Uses hmac.compare_digest() for verification.
Includes pytest unit tests.
""",
            "mock-model"
        )

    graph.set_llm_handler(mock_llm_handler)
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-spec-validation-order"
    state = {
        "task_id": task_id,
        "user_prompt": "Create an HMAC-SHA256 token signer and verifier using stdlib. Do not implement JWT.",
        "repo_path": "/tmp",
        "auto_approve": False
    }

    config = {"configurable": {"thread_id": task_id}}
    app.invoke(state, config=config)

    snapshot = app.get_state(config)
    # The graph must have halted at approval_gate after passing spec_validator
    assert snapshot.next == ("approval_gate",)
    assert snapshot.values.get("specification_fidelity_status") == "PASSED"
    assert snapshot.values.get("spec_validation_attempts") == 1
    assert snapshot.values.get("original_requirements_preserved") is True


def test_contradictions_prevent_code_generation():
    """Scenario 5: Critical contradictions halt before approval_gate and code_builder."""
    def mock_llm_handler(role: str, prompt: str):
        # Stubbornly return a contradictory JWT specification on all attempts
        return (
            """# Contradictory Spec
## JWT Token Implementation
Class: JWTError
Function: validate_jwt()
Header: {'alg': 'HS256', 'typ': 'JWT'}
""",
            "mock-model"
        )

    graph.set_llm_handler(mock_llm_handler)
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-contradiction-prevents-code"
    state = {
        "task_id": task_id,
        "user_prompt": "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT.",
        "repo_path": "/tmp",
        "auto_approve": True,
        "trusted_policy": "ci_automation"
    }

    config = {"configurable": {"thread_id": task_id}}
    final_state = app.invoke(state, config=config)

    # Must be marked FAILED without ever provisioning worktree or generating code
    assert final_state["task_state"] == graph.TaskState.FAILED.value
    assert final_state["status"] == "FAILED"
    assert final_state["specification_fidelity_status"] == "FAILED"
    assert final_state["spec_validation_attempts"] == 3
    assert "worktree_path" not in final_state or not final_state.get("worktree_path")
    assert "current_files" not in final_state or not final_state.get("current_files")


def test_architect_retries_after_fidelity_rejection():
    """Scenario 6: Architect retries upon rejection with actionable feedback and succeeds on revision."""
    attempt = 0

    def mock_llm_handler(role: str, prompt: str):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            # First attempt produces contradictory JWT specification
            return (
                """# Contradictory Spec
Class: JWTError
Function: validate_jwt()
""",
                "mock-model"
            )
        # Second attempt incorporates revision feedback and produces valid HMAC spec
        assert "PREVIOUS ATTEMPT REJECTED" in prompt
        assert "MANDATORY ACTION: Remove all JWT classes" in prompt
        return (
            """# Revised Technical Specification: HMAC Token Signer
## Architecture
Token format: <payload_b64>.<sig_b64>
Functions: sign_token(data, key), verify_token(token, key)
Uses hmac.compare_digest() for verification.
Includes pytest unit tests.
""",
            "mock-model"
        )

    graph.set_llm_handler(mock_llm_handler)
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-architect-retry-loop"
    state = {
        "task_id": task_id,
        "user_prompt": "Create an HMAC token signer. Do not implement JWT.",
        "repo_path": "/tmp",
        "auto_approve": False
    }

    config = {"configurable": {"thread_id": task_id}}
    app.invoke(state, config=config)

    snapshot = app.get_state(config)
    assert snapshot.next == ("approval_gate",)
    assert snapshot.values.get("specification_fidelity_status") == "PASSED"
    assert snapshot.values.get("spec_validation_attempts") == 2
    assert snapshot.values.get("original_requirements_preserved") is True


def test_validation_retry_exhaustion_halts_execution():
    """Scenario 7: Exceeding 3 validation attempts stops task with failure report and consolidation skipped."""
    call_count = 0

    def mock_llm_handler(role: str, prompt: str):
        nonlocal call_count
        call_count += 1
        return "# Invalid Spec with JWT\nclass JWTError(Exception): pass\ndef validate_jwt(): pass", "mock-model"

    graph.set_llm_handler(mock_llm_handler)
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-retry-exhaustion"
    state = {
        "task_id": task_id,
        "user_prompt": "Create an HMAC token signer. Do not implement JWT.",
        "repo_path": "/tmp",
        "auto_approve": True,
        "trusted_policy": "ci_automation"
    }

    config = {"configurable": {"thread_id": task_id}}
    final = app.invoke(state, config=config)

    assert final["status"] == "FAILED"
    assert final["specification_fidelity_status"] == "FAILED"
    assert final["spec_validation_attempts"] == 3
    assert call_count == 3
    assert final["execution_report"]["specification_fidelity_status"] == "FAILED"
    assert final["execution_report"]["specification_validation_attempts"] == 3
    assert final["execution_report"]["consolidation_status"] == "NOT_REACHED"


# ============================================================================
# 3. Downstream Constraint Preservation (Optimizer & Repair)
# ============================================================================

def test_prompt_optimizer_preserves_negative_constraints():
    """Scenario 8: Prompt optimizer injects prohibitions and does not treat prohibited libs as requested."""
    state: graph.HydramedaState = {
        "task_id": "test-optimizer-prohibitions",
        "user_prompt": "Create an HMAC-SHA256 signer. Do not implement JWT, claims, or Flask.",
        "spec_md": "# HMAC Spec\nUses hmac and hashlib.",
        "task_requirements": TaskRequirements(
            required_capabilities=["HMAC-SHA256"],
            prohibited_capabilities=["JWT", "claims", "Flask"]
        ).to_dict()
    }

    res_state = graph.node_prompt_optimizer(state)
    prompt = res_state["optimized_prompt"]

    assert "CRITICAL PROHIBITIONS (DO NOT IMPLEMENT):" in prompt
    assert "- JWT" in prompt
    # Even though "Flask" was in user_prompt, it is prohibited and must NOT be declared in lib_rule
    assert "(e.g. Flask" not in prompt


def test_repair_agent_preserves_negative_constraints():
    """Scenario 9: Repair agent prompt contains negative constraints and prohibited features."""
    captured_repair_prompts = []

    def mock_llm_handler(role: str, prompt: str):
        captured_repair_prompts.append(prompt)
        return (
            '<file name="module.py">\ndef sign_token(): pass\n</file>\n'
            '<file name="test_module.py">\ndef test_sign(): pass\n</file>'
        ), "mock-model"

    graph.set_llm_handler(mock_llm_handler)
    state: graph.HydramedaState = {
        "task_id": "test-repair-prohibitions",
        "spec_md": "# HMAC Spec",
        "current_files": {"module.py": "def broken(): pass"},
        "error_analysis": {"category": "RUNTIME_ERROR", "actionable_guidance": "Fix tests"},
        "pytest_stderr": "AssertionError",
        "repair_count": 0,
        "task_requirements": TaskRequirements(
            prohibited_capabilities=["JWT", "claims", "unrelated authentication"]
        ).to_dict()
    }

    graph.node_repair_agent(state)
    assert len(captured_repair_prompts) == 1
    prompt = captured_repair_prompts[0]
    assert "CRITICAL PROHIBITIONS (DO NOT INTRODUCE DURING REPAIR):" in prompt
    assert "- JWT" in prompt


# ============================================================================
# 4. Canonical Non-JWT HMAC Specification & Unrelated Tasks
# ============================================================================

def test_valid_non_jwt_hmac_spec_passes():
    """Scenario 10: Canonical non-JWT HMAC blueprint passes validation on first try."""
    prompt = (
        "Create a secure HMAC-SHA256 token signer and verifier with pytest tests. "
        "Use HMAC-SHA256 directly; do not implement JWT, claims, or unrelated authentication features."
    )
    reqs = extract_task_requirements(prompt)
    report = validate_specification_fidelity(prompt, NON_JWT_HMAC_SPECIFICATION, reqs)

    assert report.passed is True
    assert len(report.violations) == 0
    assert len(report.critical_contradictions) == 0


def test_unrelated_specifications_pass_without_regression():
    """Scenario 11: Unrelated tasks (LRU cache, calculator, etc.) pass validation without false positives."""
    cache_prompt = "Create a thread-safe LRU cache in Python with pytest unit tests."
    cache_spec = """# Technical Specification: Thread-Safe LRU Cache
## Data Structures
- OrderedDict with threading.Lock
- Capacity parameter
## API
- get(key)
- put(key, value)
## Unit Tests
- test_eviction()
- test_concurrency()
"""
    cache_reqs = extract_task_requirements(cache_prompt)
    report = validate_specification_fidelity(cache_prompt, cache_spec, cache_reqs)

    assert report.passed is True
    assert len(report.violations) == 0


def test_approval_and_governance_compatibility():
    """Scenario 12: Interactive interrupt/resume governance functions smoothly after validation passes."""
    def mock_llm_handler(role: str, prompt: str):
        return NON_JWT_HMAC_SPECIFICATION, "mock-model"

    graph.set_llm_handler(mock_llm_handler)
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-governance-fidelity-compat"
    state = {
        "task_id": task_id,
        "user_prompt": "Create an HMAC token signer. Do not implement JWT.",
        "repo_path": "/tmp",
        "auto_approve": False
    }

    config = {"configurable": {"thread_id": task_id}}
    app.invoke(state, config=config)

    # 1. First interrupt at approval_gate
    snap1 = app.get_state(config)
    assert snap1.next == ("approval_gate",)
    assert snap1.values.get("specification_fidelity_status") == "PASSED"

    # 2. Reject specification
    app.invoke(Command(resume={"approved": False, "task_id": task_id}), config=config)
    snap2 = app.get_state(config)
    assert snap2.values.get("status") in ("REJECTED", "CANCELLED")
    assert snap2.values.get("task_state") == graph.TaskState.CANCELLED.value
