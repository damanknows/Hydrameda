#!/usr/bin/env python3
"""
Test suite for Hydrameda 12-Node Neuro-Symbolic LangGraph Engine.

Covers:
 1. Successful execution (Pass@1)
 2. Verification failure leading to max repair attempts exceeded
 3. Self-healing repair success (Pass@2)
 4. Three failed repair attempts (strictly capped at 3)
 5. Human rejection at approval gate
 6. Interrupted execution and resume via LangGraph Command
 7. Node exceptions handling
"""
import pytest
import sys
from pathlib import Path

# Ensure langgraph_engine is in sys.path
ENGINE_DIR = Path(__file__).resolve().parent.parent / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import graph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


def setup_mock_llm(code_override: str = "", spec_override: str = ""):
    """Sets a global mock LLM handler returning valid XML file tags and specs."""
    default_code = """<file name="module.py">
def add(a: int, b: int) -> int:
    return a + b
</file>
<file name="test_module.py">
from module import add
def test_add():
    assert add(2, 3) == 5
</file>"""

    def _mock(model_group: str, prompt: str) -> tuple[str, str]:
        if "architect" in model_group:
            spec = spec_override or "# Spec: Mathematical Addition Utility\nDefine add(a: int, b: int) -> int."
            return spec, "mock-architect"
        return (code_override or default_code), "mock-coder"

    graph.set_llm_handler(_mock)


def make_initial_state(task_id: str, auto_approve: bool = True, **overrides) -> graph.HydramedaState:
    """Builds a test state dictionary."""
    base: graph.HydramedaState = {
        "task_id": task_id,
        "user_prompt": "Create an addition function with pytest tests",
        "repo_path": str(Path(__file__).parent.parent),
        "auto_approve": auto_approve,
        "trusted_policy": "eval_harness" if auto_approve else "",
        "approval_status": "PENDING",
        "route": "execute",
        "task_analysis": {},
        "vault_context": "",
        "ast_skeleton": "",
        "spec_md": "",
        "optimized_prompt": "",
        "worktree_path": "",
        "target_files": [],
        "current_files": {},
        "verifier_output": "",
        "pytest_stderr": "",
        "verifier_exit_code": 0,
        "repair_count": 0,
        "retry_count": 0,
        "error_analysis": {},
        "status": "PENDING",
        "commit_hash": "N/A",
        "final_report": "",
        "structured_logs": [],
        "error": ""
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def reset_handlers():
    """Ensures handlers are reset before and after every test."""
    setup_mock_llm()
    graph.set_verifier_handler(None)
    yield
    graph.set_llm_handler(None)
    graph.set_verifier_handler(None)


# ============================================================================
# 1. Test Successful Execution (Pass@1)
# ============================================================================
def test_successful_execution():
    """Verifies that when the sandbox verifier succeeds, the pipeline completes with PASSED."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    def mock_verifier(state):
        return 0, "4 passed in 0.12s\n--- [1/4] Ruff Static Check ---\n--- [2/4] Mypy Type Check ---\n--- [4/4] Pytest Unit Suite ---"

    graph.set_verifier_handler(mock_verifier)

    task_id = "test-success-01"
    initial_state = make_initial_state(task_id, auto_approve=True)
    config = {"configurable": {"thread_id": task_id}}

    final_state = app.invoke(initial_state, config)

    assert final_state["status"] == "PASSED"
    assert final_state["approval_status"] == "APPROVED"
    assert final_state["verifier_exit_code"] == 0
    assert final_state["repair_count"] == 0
    assert "module.py" in final_state["current_files"]
    assert "test_module.py" in final_state["current_files"]
    assert len(final_state["structured_logs"]) > 0


# ============================================================================
# 2. Test Verification Failure
# ============================================================================
def test_verification_failure_cannot_enter_success_path():
    """Strict security test: Verifies that a failing verifier CANNOT enter the success path."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    def mock_verifier(state):
        return 1, "FAILED test_module.py::test_add - AssertionError: assert 4 == 5"

    graph.set_verifier_handler(mock_verifier)

    task_id = "test-fail-safety-01"
    initial_state = make_initial_state(task_id, auto_approve=True)
    config = {"configurable": {"thread_id": task_id}}

    final_state = app.invoke(initial_state, config)

    # Must be FAILED, commit hash must remain uncommitted, error analysis must be populated
    assert final_state["status"] == "FAILED"
    assert final_state["verifier_exit_code"] != 0
    assert final_state["commit_hash"] == "N/A"
    assert final_state["error_analysis"]["category"] == "ASSERTION_FAILURE"


# ============================================================================
# 3. Test Self-Healing Repair Success
# ============================================================================
def test_repair_success():
    """Verifies that a failure on attempt 0 triggers error analysis, repair agent, and succeeds on attempt 1."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    calls = {"count": 0}
    def mock_verifier(state):
        calls["count"] += 1
        if calls["count"] == 1:
            return 1, "FAILED test_module.py::test_add - AssertionError: assert 4 == 5"
        return 0, "1 passed in 0.05s"

    graph.set_verifier_handler(mock_verifier)

    task_id = "test-repair-success-01"
    initial_state = make_initial_state(task_id, auto_approve=True)
    config = {"configurable": {"thread_id": task_id}}

    final_state = app.invoke(initial_state, config)

    assert final_state["status"] == "PASSED"
    assert final_state["repair_count"] == 1
    assert final_state["verifier_exit_code"] == 0
    assert calls["count"] == 2


# ============================================================================
# 4. Test Three Failed Repair Attempts (Hard Cap)
# ============================================================================
def test_three_failed_repair_attempts():
    """Verifies that repair attempts are strictly capped at 3, preventing infinite loops."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    verifier_runs = {"count": 0}
    def mock_verifier(state):
        verifier_runs["count"] += 1
        return 1, f"FAILED test_module.py::test_add - Attempt {verifier_runs['count']}"

    graph.set_verifier_handler(mock_verifier)

    task_id = "test-max-repairs-01"
    initial_state = make_initial_state(task_id, auto_approve=True)
    config = {"configurable": {"thread_id": task_id}}

    final_state = app.invoke(initial_state, config)

    # 1 initial verifier run + 3 repair runs = 4 verifier executions total
    assert verifier_runs["count"] == 4
    assert final_state["repair_count"] == 3
    assert final_state["status"] == "FAILED"
    assert final_state["commit_hash"] == "N/A"


# ============================================================================
# 5. Test Human Rejection
# ============================================================================
def test_human_rejection():
    """Verifies that when approval is rejected, execution cancels without generating code."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-rejection-01"
    # auto_approve=False causes interrupt
    initial_state = make_initial_state(task_id, auto_approve=False)
    config = {"configurable": {"thread_id": task_id}}

    # Run until approval_gate interrupt
    res = app.invoke(initial_state, config)
    snapshot = app.get_state(config)
    assert len(snapshot.tasks[0].interrupts) > 0

    # Resume with rejection
    final_state = app.invoke(Command(resume={"approved": False}), config)

    assert final_state["approval_status"] == "REJECTED"
    assert final_state["route"] == "cancel"
    assert final_state["status"] == "REJECTED"
    assert final_state["current_files"] == {}
    assert final_state["commit_hash"] == "N/A"


# ============================================================================
# 6. Test Interrupted Execution and Resume
# ============================================================================
def test_interrupted_execution_and_resume():
    """Verifies LangGraph interrupt at approval_gate and successful resumption with approval."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    def mock_verifier(state):
        return 0, "1 passed"

    graph.set_verifier_handler(mock_verifier)

    task_id = "test-interrupt-resume-01"
    initial_state = make_initial_state(task_id, auto_approve=False)
    config = {"configurable": {"thread_id": task_id}}

    # 1. Execute up to approval_gate interrupt
    app.invoke(initial_state, config)
    snapshot = app.get_state(config)
    assert snapshot.next == ("approval_gate",)
    assert len(snapshot.tasks[0].interrupts) == 1

    # 2. Resume execution with human approval
    final_state = app.invoke(Command(resume={"approved": True}), config)

    assert final_state["approval_status"] == "APPROVED"
    assert final_state["status"] == "PASSED"
    assert "module.py" in final_state["current_files"]


# ============================================================================
# 7. Test Node Exceptions Handling
# ============================================================================
def test_node_exception_handling():
    """Verifies that exceptions in nodes are explicitly caught, logged, and reflected in state['error']."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-exception-01"
    # Empty prompt triggers explicit ValueError in spec_analyzer
    initial_state = make_initial_state(task_id, user_prompt="")
    config = {"configurable": {"thread_id": task_id}}

    final_state = app.invoke(initial_state, config)

    assert final_state["status"] in ("ERROR", "FAILED", "REJECTED")
    assert "user_prompt cannot be empty" in final_state["error"]
    error_logs = [log for log in final_state["structured_logs"] if log["level"] == "ERROR"]
    assert len(error_logs) > 0
