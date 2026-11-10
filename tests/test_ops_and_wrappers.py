"""
Unit and regression tests for Hydrameda Phase Operations, Portable Scripts,
Worktree TTL Pruning, MCP Safety, and Multi-Interrupt Lifecycle.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ENGINE_DIR = REPO_ROOT / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import mcp_server.server as mcp_srv
from langgraph_engine import graph
from scripts.prune_worktrees import prune_worktrees


# ============================================================================
# 1. Test Architect Prompt Adapts for Third-Party Libraries
# ============================================================================
def test_architect_prompt_adapts_for_third_party_libraries():
    """Verifies that node_architect relaxes stdlib-only constraint when prompt requests Flask/PyYAML."""
    captured_prompts = []

    def mock_llm_handler(role, prompt):
        captured_prompts.append(prompt)
        return "# Mock Spec\nDefines Flask endpoints.", "test-model"

    graph.set_llm_handler(mock_llm_handler)
    try:
        # Case A: Standard prompt -> stdlib only
        state_stdlib: graph.HydramedaState = {
            "task_id": "test-stdlib",
            "user_prompt": "Create an HMAC signer and verifier",
            "vault_context": "",
            "ast_skeleton": "",
        }
        graph.node_architect(state_stdlib)
        assert len(captured_prompts) == 1
        assert "Use ONLY Python standard library modules" in captured_prompts[0]

        # Case B: Third-party library prompt (Flask / PyYAML)
        captured_prompts.clear()
        state_third_party: graph.HydramedaState = {
            "task_id": "test-flask",
            "user_prompt": "Create a secure Flask REST endpoint to parse YAML payloads",
            "vault_context": "",
            "ast_skeleton": "",
        }
        graph.node_architect(state_third_party)
        assert len(captured_prompts) == 1
        assert "Rely on Python standard library and explicitly requested packages (e.g. Flask, PyYAML" in captured_prompts[0]
        assert "Use ONLY Python standard library modules" not in captured_prompts[0]
    finally:
        graph.set_llm_handler(None)


# ============================================================================
# 2. Test Multi-Interrupt Lifecycle in StateGraph
# ============================================================================
def test_multi_interrupt_specification_and_consolidation_lifecycle():
    """Verifies that both approval_gate and consolidator interrupt when auto_approve=False."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-multi-interrupt-thread"}}

    def mock_llm_handler(role, prompt):
        if "Architect" in prompt:
            return (
                "# Spec for Token Bucket\n"
                "Create a rate limiter module.\n"
                "Define check() -> bool.\n"
                "Includes pytest tests."
            ), "mock-architect"
        elif "Repair" in prompt or "Coder" in prompt:
            return '<file name="module.py">\ndef check(): return True\n</file>\n<file name="test_module.py">\nfrom module import check\ndef test_check(): assert check()\n</file>', "mock-coder"
        return "Summary", "mock-model"

    def mock_verifier_handler(state):
        return 0, "All 8 checks passed"

    graph.set_llm_handler(mock_llm_handler)
    graph.set_verifier_handler(mock_verifier_handler)

    try:
        # Start without auto-approval
        initial_state: graph.HydramedaState = {
            "task_id": "test-multi-interrupt",
            "user_prompt": "Create a rate limiter",
            "auto_approve": False,
            "destructive_approved": False,
            "merge_approved": False,
        }

        # Step 1: Runs to approval_gate (Node 5) and interrupts
        app.invoke(initial_state, config)
        snapshot1 = app.get_state(config)
        assert snapshot1.next == ("approval_gate",)
        assert snapshot1.tasks[0].interrupts[0].value["action"] == "review_specification"

        # Step 2: Resume specification review with approval
        app.invoke(Command(resume={"approved": True}), config)

        # Step 3: Proceeds through build and verification, then interrupts at consolidator (Node 12)
        snapshot2 = app.get_state(config)
        assert snapshot2.next == ("consolidator",)
        assert snapshot2.tasks[0].interrupts[0].value["action"] == "review_and_consolidate"
        manifest_hash = snapshot2.tasks[0].interrupts[0].value["manifest_hash"]
        assert manifest_hash is not None

        # Step 4: Resume consolidation review with approval bound to manifest hash
        final_state = app.invoke(Command(resume={
            "approved": True,
            "approved_manifest_hash": manifest_hash,
            "destructive_approved": False,
            "merge_approved": False
        }), config)

        assert final_state["status"] == "PASSED"
        assert final_state["consolidation_report"]["status"] == "COMMITTED_WORKTREE_ONLY"
        assert final_state["commit_hash"] != "N/A"
    finally:
        graph.set_llm_handler(None)
        graph.set_verifier_handler(None)


# ============================================================================
# 3. Test MCP Merge Restricted Destination & Path Safety
# ============================================================================
def test_mcp_merge_restricted_destination_blocked(tmp_path, monkeypatch):
    """Verifies that merge_verified_worktree rejects sensitive system destinations."""
    fake_wt_dir = tmp_path / "worktrees"
    fake_wt_dir.mkdir()
    task_dir = fake_wt_dir / "task-sec-test"
    task_dir.mkdir()
    (task_dir / "module.py").write_text("def run(): pass\n")

    # Mock git verified commit
    subprocess.run(["git", "init", str(task_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(task_dir), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(task_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(task_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(task_dir), "commit", "-m", "feat: verified autonomous build"], check=True, capture_output=True)

    monkeypatch.setattr(mcp_srv, "WORKTREES_DIR", fake_wt_dir)

    # Restricted paths must be blocked
    for restricted in ("/etc", "/var/run", "/root", "/sys", "/proc"):
        res = mcp_srv.merge_verified_worktree("task-sec-test", restricted, force=False)
        assert "Security Error: Target directory" in res
        assert "restricted" in res.lower()


def test_mcp_merge_valid_destination_success(tmp_path, monkeypatch):
    """Verifies that merge_verified_worktree exports files to a valid target."""
    fake_wt_dir = tmp_path / "worktrees"
    fake_wt_dir.mkdir()
    task_dir = fake_wt_dir / "task-merge-valid"
    task_dir.mkdir()
    (task_dir / "module.py").write_text("def secret(): return 42\n")
    (task_dir / "test_module.py").write_text("from module import secret\ndef test_secret(): assert secret() == 42\n")

    subprocess.run(["git", "init", str(task_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(task_dir), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(task_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(task_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(task_dir), "commit", "-m", "feat: verified autonomous build"], check=True, capture_output=True)

    monkeypatch.setattr(mcp_srv, "WORKTREES_DIR", fake_wt_dir)

    target_export = tmp_path / "safe_export"
    res = mcp_srv.merge_verified_worktree("task-merge-valid", str(target_export), module_rename="token_vault.py")
    assert "Exported 2 verified files" in res
    assert (target_export / "token_vault.py").exists()
    assert (target_export / "test_token_vault.py").exists()
    # Check that import was rewritten in test file
    test_content = (target_export / "test_token_vault.py").read_text(encoding="utf-8")
    assert "from token_vault import secret" in test_content


# ============================================================================
# 4. Test Worktree TTL Pruning Utility
# ============================================================================
def test_worktree_ttl_pruning(tmp_path):
    """Verifies prune_worktrees correctly handles age cutoffs and git worktrees."""
    repo = tmp_path / "repo"
    repo.mkdir()
    wt_dir = repo / "worktrees"
    wt_dir.mkdir()

    # Init repo
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "Initial root commit"], check=True, capture_output=True)

    # Worktree 1: Stale (10 days old)
    wt1 = wt_dir / "task-stale-01"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "hydrameda/task-stale-01", str(wt1)], check=True, capture_output=True)
    old_time = time.time() - (10 * 86400)
    os.utime(wt1, (old_time, old_time))

    # Worktree 2: Fresh (1 day old)
    wt2 = wt_dir / "task-fresh-02"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "hydrameda/task-fresh-02", str(wt2)], check=True, capture_output=True)
    fresh_time = time.time() - (1 * 86400)
    os.utime(wt2, (fresh_time, fresh_time))

    # Test Dry-Run
    dry_res = prune_worktrees(repo_path=repo, max_age_days=5.0, dry_run=True)
    assert len(dry_res["pruned"]) == 1
    assert dry_res["pruned"][0]["task_id"] == "task-stale-01"
    assert len(dry_res["kept"]) == 1
    assert dry_res["kept"][0]["task_id"] == "task-fresh-02"
    assert wt1.exists()  # Not deleted in dry run

    # Test Live Prune
    live_res = prune_worktrees(repo_path=repo, max_age_days=5.0, dry_run=False)
    assert len(live_res["pruned"]) == 1
    assert live_res["pruned"][0]["task_id"] == "task-stale-01"
    assert not wt1.exists()  # Deleted
    assert wt2.exists()  # Kept


# ============================================================================
# 5. Test Portable Wrappers and Auto-Approve Invocations
# ============================================================================
def test_portable_scripts_exist_and_executable():
    """Verifies scripts/hydrameda, scripts/hydrameda-merge, scripts/hydrameda-voice exist and are executable."""
    for script_name in ("hydrameda", "hydrameda-merge", "hydrameda-voice", "prune_worktrees.py"):
        path = REPO_ROOT / "scripts" / script_name
        assert path.exists(), f"Missing required wrapper: {path}"
        assert os.access(path, os.X_OK), f"Script is not executable: {path}"

        content = path.read_text(encoding="utf-8")
        assert "/home/daman" not in content, f"Script {script_name} contains hardcoded /home/daman path!"


def test_hermes_and_mcp_autonomous_invocations_pass_auto_approve():
    """Verifies that autonomous triggers in hermes_bridge and mcp_server do NOT pass unconstrained --auto-approve,
    and backlog_runner passes an authorized trusted-policy."""
    # 1. Hermes Bridge runs non-interactive without unconstrained auto-approve
    hermes_content = (REPO_ROOT / "langgraph_engine" / "hermes_bridge.py").read_text(encoding="utf-8")
    assert "--non-interactive" in hermes_content
    assert "--auto-approve" not in hermes_content

    # 2. MCP Server trigger_autonomous_build runs non-interactive without unconstrained auto-approve
    mcp_content = (REPO_ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8")
    assert "--non-interactive" in mcp_content
    assert "--auto-approve" not in mcp_content

    # 3. Backlog runner uses an explicitly authorized trusted-policy
    backlog_content = (REPO_ROOT / "backlog_runner.sh").read_text(encoding="utf-8")
    assert "--trusted-policy" in backlog_content

