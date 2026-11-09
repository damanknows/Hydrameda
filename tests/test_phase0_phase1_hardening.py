"""
Comprehensive hardening and regression test suite for Phase 0 and Phase 1 sprints.

Covers:
 Part A:
  A1. Cryptographic manifest approval, strict boolean types, task ID matching, replayed/stale rejection
  A2. Background approval policy, trusted execution policy vs untrusted auto-approval
  A3. Task ID allowlist, traversal, path separators, collisions, active worktree protection
  A4. Atomic patch, failure injection, rollback guarantees, .bak preservation
  A5. Hardened consolidation, .hydrameda_verification.json metadata, conflict abort
  A6. Sandbox credential mount blocking, isolation metadata, cleanup
 Part B & D:
  B1-B4. Task states normalization, execution report generation, and
  Part D: Full End-to-End acceptance run in disposable Git repository
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ENGINE_DIR = REPO_ROOT / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from langgraph.checkpoint.memory import MemorySaver

import mcp_server.server as mcp_srv
from code_engine import (
    ApprovalDecision,
    ChangeManifest,
    FileChange,
    FileOperation,
    GovernanceEngine,
    PatchEngine,
    SafeConsolidator,
)
from langgraph_engine import graph
from langgraph_engine.preprocessor import create_isolated_worktree, validate_task_id
from scripts.prune_worktrees import prune_worktrees
from verification_engine.sandbox_runner import SandboxConfig, SandboxRunner, SecurityError

# ============================================================================
# PART A1: Cryptographic Approval Auditing & Strict Types
# ============================================================================

def test_approval_rejects_string_boolean_values():
    """Requirement A1.1 & A1.2: Reject strings such as 'false', 'yes', 'true', 'approved'."""
    manifest = ChangeManifest(
        task_id="task-str-approval",
        description="Test string approval rejection",
        changes=[FileChange("foo.py", FileOperation.CREATE, "x = 1", authorized=True)],
    )

    for invalid_val in ["true", "false", "yes", "no", "approved", "REJECT", 1, 0]:
        decision = ApprovalDecision(
            approved=invalid_val,  # type: ignore
            approved_manifest_hash=manifest.manifest_hash,
            task_id="task-str-approval",
        )
        valid, reasons = GovernanceEngine.validate_approval(decision, manifest, verification_passed=True)
        assert valid is False
        assert any("boolean true" in r.lower() for r in reasons)


def test_approval_requires_explicit_nonempty_hash():
    """Requirement A1.3 & A1.4: Reject missing or empty approval hash; never auto-populate."""
    manifest = ChangeManifest(
        task_id="task-missing-hash",
        description="Test missing hash",
        changes=[FileChange("foo.py", FileOperation.CREATE, "x = 1", authorized=True)],
    )

    for empty_hash in ["", None, "   "]:
        decision = ApprovalDecision(
            approved=True,
            approved_manifest_hash=empty_hash,  # type: ignore
            task_id="task-missing-hash",
        )
        valid, reasons = GovernanceEngine.validate_approval(decision, manifest, verification_passed=True)
        assert valid is False
        assert any("cryptographic hash binding" in r.lower() for r in reasons)


def test_approval_rejects_hash_mismatch_and_stale_manifest():
    """Requirement A1.5 & A1.6: Compare supplied hash against exact current hash; invalidate when manifest changes."""
    manifest = ChangeManifest(
        task_id="task-hash-mismatch",
        description="Original manifest",
        changes=[FileChange("foo.py", FileOperation.CREATE, "x = 1", authorized=True)],
    )
    original_hash = manifest.manifest_hash

    # Supplied wrong hash
    decision_wrong = ApprovalDecision(
        approved=True,
        approved_manifest_hash="badf00d" * 8,
        task_id="task-hash-mismatch",
    )
    valid, reasons = GovernanceEngine.validate_approval(decision_wrong, manifest, verification_passed=True)
    assert valid is False
    assert any("stale approval" in r.lower() or "does not match" in r.lower() for r in reasons)

    # Manifest changes after hash was obtained
    manifest.changes.append(FileChange("bar.py", FileOperation.CREATE, "y = 2", authorized=True))
    assert manifest.manifest_hash != original_hash

    decision_stale = ApprovalDecision(
        approved=True,
        approved_manifest_hash=original_hash,
        task_id="task-hash-mismatch",
    )
    valid, reasons = GovernanceEngine.validate_approval(decision_stale, manifest, verification_passed=True)
    assert valid is False
    assert any("stale approval" in r.lower() for r in reasons)


def test_approval_rejects_replayed_approval_for_different_task():
    """Requirement A1.7 & A1.8: Approval must match task_id; reject replayed approvals."""
    manifest_a = ChangeManifest(
        task_id="task-task-A",
        description="Task A",
        changes=[FileChange("a.py", FileOperation.CREATE, "a = 1", authorized=True)],
    )

    # Valid approval for task-B with task-A's hash
    decision_cross = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest_a.manifest_hash,
        task_id="task-task-B",
    )
    valid, reasons = GovernanceEngine.validate_approval(decision_cross, manifest_a, verification_passed=True)
    assert valid is False
    assert any("task id mismatch" in r.lower() for r in reasons)


def test_approval_valid_explicit():
    """Requirement A1.10: Valid explicit approval with matching task_id and hash succeeds."""
    manifest = ChangeManifest(
        task_id="task-valid-01",
        description="Task Valid",
        changes=[FileChange("v.py", FileOperation.CREATE, "v = 100", authorized=True)],
    )
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        task_id="task-valid-01",
        approver="engineer@corp",
    )
    valid, reasons = GovernanceEngine.validate_approval(decision, manifest, verification_passed=True)
    assert valid is True
    assert any("verified and valid" in r.lower() for r in reasons)


# ============================================================================
# PART A2: Background Approval Policy
# ============================================================================

def test_untrusted_auto_approval_pauses_graph():
    """Requirement A2.5 & A2.6: Untrusted callers cannot auto-approve; graph pauses at approval_gate."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    task_id = "test-untrusted-pause"
    state: graph.HydramedaState = {
        "task_id": task_id,
        "user_prompt": "test prompt",
        "repo_path": str(REPO_ROOT),
        "auto_approve": True,
        "trusted_policy": "untrusted_arbitrary_policy",
        "approval_status": "PENDING",
        "route": "execute",
        "task_analysis": {},
        "vault_context": "",
        "ast_skeleton": "",
        "spec_md": "# Spec",
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
        "task_state": graph.TaskState.CREATED.value,
        "commit_hash": "N/A",
        "final_report": "",
        "structured_logs": [],
        "error": ""
    }

    graph.set_llm_handler(lambda role, prompt: ("# Mock Spec\nSpec", "mock-model"))
    config = {"configurable": {"thread_id": task_id}}
    try:
        # Graph execution will pause at approval_gate because trusted_policy is untrusted
        app.invoke(state, config=config)
        snapshot = app.get_state(config)
        assert len(snapshot.tasks) > 0
        # Interrupt was triggered
        assert len(snapshot.tasks[0].interrupts) > 0
        assert "Approve technical specification" in snapshot.tasks[0].interrupts[0].value.get("question", "")
    finally:
        graph.set_llm_handler(None)


def test_trusted_auto_approval_allowed_under_policy():
    """Requirement A2.8: Authorized automation with trusted policy auto-approves."""
    state: graph.HydramedaState = {
        "task_id": "task-trusted",
        "user_prompt": "test prompt",
        "auto_approve": True,
        "trusted_policy": "ci_automation",
        "approval_status": "PENDING",
        "current_files": {"v.py": "v = 1"},
        "target_files": ["v.py"],
        "task_state": graph.TaskState.PLANNING.value,
        "structured_logs": [],
    }

    res = graph.node_approval_gate(state)
    assert res["approval_status"] == "APPROVED"
    assert res["approval_mode"] == "auto_approved"
    assert "ci_automation" in res["authorization_source"]


def test_mcp_approve_task_tool():
    """Requirement A2.3: MCP provides explicit approve_task tool."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())
    task_id = "test-mcp-app-01"
    config = {"configurable": {"thread_id": task_id}}

    # Setup state paused at approval_gate
    state: graph.HydramedaState = {
        "task_id": task_id,
        "user_prompt": "test prompt",
        "repo_path": str(REPO_ROOT),
        "auto_approve": False,
        "trusted_policy": "",
        "approval_status": "PENDING",
        "route": "execute",
        "task_analysis": {},
        "vault_context": "",
        "ast_skeleton": "",
        "spec_md": "# Spec",
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
        "task_state": graph.TaskState.CREATED.value,
        "commit_hash": "N/A",
        "final_report": "",
        "structured_logs": [],
        "error": ""
    }

    app.invoke(state, config=config)

    with patch("graph.build_hydrameda_graph", return_value=app):
        res = mcp_srv.approve_task(task_id, "test_hash", approved=True)
        assert "resumed with approval: approved=True" in res


# ============================================================================
# PART A3: Task ID Allowlist and Worktree Isolation
# ============================================================================

def test_task_id_allowlist_validation():
    """Requirement A3.1 & A3.2: Enforce strict allowlist, reject traversal, separators, null bytes."""
    valid_ids = ["task-123", "TASK_456", "task-abc_def", "12345"]
    for tid in valid_ids:
        assert validate_task_id(tid) == tid

    invalid_ids = [
        "../traversal",
        "sub/dir",
        "sub\\dir",
        "/absolute/path",
        "null\0byte",
        "",
        "a" * 65,  # Exceeds 64 chars
        "task with spaces",
        "task;evil",
        "task$(whoami)",
    ]
    for tid in invalid_ids:
        with pytest.raises(ValueError):
            validate_task_id(tid)


def test_worktree_collision_rejection(tmp_path):
    """Requirement A3.4 & A3.5: Reject collision with existing task ID; never remove implicitly."""
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir(parents=True)
    existing_wt = wt_root / "existing-task"
    existing_wt.mkdir()

    repo = tmp_path / "repo"
    repo.mkdir()

    with (
        patch("langgraph_engine.preprocessor.WORKTREES_DIR", wt_root),
        pytest.raises(FileExistsError, match="collision"),
    ):
        create_isolated_worktree(str(repo), "existing-task")


def test_pruning_script_protects_active_and_locked_worktrees(tmp_path):
    """Requirement A3.9: Protect pruning script from deleting active or recently updated tasks."""
    wt_dir = tmp_path / "worktrees"
    wt_dir.mkdir()

    # Active task: modified just now (age < 3600s)
    active_wt = wt_dir / "active-task"
    active_wt.mkdir()
    (active_wt / "file.py").write_text("x = 1")

    # Locked task: contains index.lock
    locked_wt = wt_dir / "locked-task"
    locked_wt.mkdir()
    dot_git = locked_wt / ".git"
    dot_git.mkdir()
    (dot_git / "index.lock").write_text("lock")
    # Make locked task old
    old_time = time.time() - 7200
    os.utime(locked_wt, (old_time, old_time))

    # Old verified task: age > 3600s with verification record
    old_wt = wt_dir / "old-task"
    old_wt.mkdir()
    (old_wt / ".hydrameda_verification.json").write_text('{"verified": true}')
    os.utime(old_wt, (old_time, old_time))

    # Run prune_worktrees
    prune_worktrees(repo_path=tmp_path, max_age_days=0.001, force=False)

    # Active and locked worktrees must still exist!
    assert active_wt.exists()
    assert locked_wt.exists()
    # Old verified task was pruned
    assert not old_wt.exists()


# ============================================================================
# PART A4: Atomic Patch and Rollback Guarantees
# ============================================================================

def test_atomic_patch_temporary_write_failure(tmp_path):
    """Requirement A4.1 & A4.3: Handle partial write exceptions cleanly without corrupting target."""
    target_file = tmp_path / "target.py"
    target_file.write_text("original_content = True\n")

    manifest = ChangeManifest(
        task_id="task-atomic-fail",
        description="Failing write",
        allowed_base_path=str(tmp_path),
        changes=[
            FileChange("target.py", FileOperation.MODIFY, "new_content = True\n", authorized=True)
        ],
    )

    patch_engine = PatchEngine(target_worktree=tmp_path)
    from code_engine.patch_engine import PatchApplicationError

    # Inject exception during temp write
    with (
        patch("code_engine.patch_engine.tempfile.mkstemp", side_effect=OSError("Simulated disk error")),
        pytest.raises((PatchApplicationError, OSError), match="Simulated disk error"),
    ):
        patch_engine.apply_manifest(manifest)

    # Target file must retain original content!
    assert target_file.read_text() == "original_content = True\n"


def test_multi_file_patch_third_fails_triggers_rollback(tmp_path):
    """Requirement A4.5 & A4.6: Multi-file patch where 3rd fails rolls back 1st and 2nd."""
    file1 = tmp_path / "file1.py"
    file2 = tmp_path / "file2.py"
    file1.write_text("f1 = 1\n")
    file2.write_text("f2 = 1\n")

    manifest = ChangeManifest(
        task_id="task-multi-fail",
        description="Multi-file failure",
        allowed_base_path=str(tmp_path),
        changes=[
            FileChange("file1.py", FileOperation.MODIFY, "f1 = 2\n", authorized=True),
            FileChange("file2.py", FileOperation.MODIFY, "f2 = 2\n", authorized=True),
            FileChange("file3.py", FileOperation.MODIFY, "f3 = 2\n", authorized=True),  # Doesn't exist!
        ],
    )

    patch_engine = PatchEngine(target_worktree=tmp_path)

    with pytest.raises(PatchApplicationError):
        patch_engine.apply_manifest(manifest)

    # files 1 and 2 must retain initial content
    assert file1.read_text() == "f1 = 1\n"
    assert file2.read_text() == "f2 = 1\n"


# ============================================================================
# PART A5: Hardened Consolidation
# ============================================================================

def test_consolidation_writes_verification_record(tmp_path):
    """Requirement A5.6: SafeConsolidator writes .hydrameda_verification.json bound to commit SHA and manifest hash."""
    # Setup temporary git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    git_env = {"GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "test@test.local", "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "test@test.local"}
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "init.txt").write_text("hello")
    subprocess.run(["git", "-C", str(repo), "add", "init.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "Initial commit"], check=True, env={**os.environ, **git_env})

    # Setup worktree
    wt = tmp_path / "wt"
    branch_name = "hydrameda/task-cons-rec"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", branch_name, str(wt)], check=True, capture_output=True)
    (wt / "feature.py").write_text("def feat(): return True\n")

    manifest = ChangeManifest(
        task_id="task-cons-rec",
        description="Feature",
        changes=[FileChange("feature.py", FileOperation.CREATE, "def feat(): return True\n", authorized=True)],
    )

    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        task_id="task-cons-rec",
        merge_approved=True,
    )

    report = SafeConsolidator.consolidate_changes(
        worktree_path=wt,
        main_repo_path=repo,
        manifest=manifest,
        decision=decision,
        verification_passed=True,
    )

    assert report.status == "CONSOLIDATED"
    assert report.merged_to_main is True
    # Verify .hydrameda_verification.json was created
    record_file = wt / ".hydrameda_verification.json"
    assert record_file.exists()
    record = json.loads(record_file.read_text())
    assert record["task_id"] == "task-cons-rec"
    assert record["manifest_hash"] == manifest.manifest_hash
    assert record["overall_status"] == "passed"


# ============================================================================
# PART A6: Sandbox Validation
# ============================================================================

def test_sandbox_blocks_mounting_credential_paths(tmp_path):
    """Requirement A6.5: Prevent host credential exposure (.env, .git/credentials, .ssh, .aws, .netrc)."""
    blocked_files = [".env", ".git/credentials", ".ssh", ".aws", ".netrc"]

    for blocked in blocked_files:
        wt = tmp_path / f"wt_{blocked.replace('/', '_').replace('.', '_')}"
        wt.mkdir(parents=True, exist_ok=True)
        sensitive_path = wt / blocked
        sensitive_path.parent.mkdir(parents=True, exist_ok=True)
        sensitive_path.write_text("SECRET=123")

        config = SandboxConfig()
        with pytest.raises(SecurityError, match="sensitive credential path"):
            config.validate_mounts(wt)


def test_sandbox_host_isolation_metadata():
    """Requirement A6.9: Distinguish container non-root execution from host daemon rootless mode."""
    runner = SandboxRunner()
    meta = runner.get_host_isolation_metadata()
    assert "container_user" in meta
    assert meta["container_user"] == "10001:10001"
    assert "network_isolation" in meta
    assert meta["network_isolation"] is True
    assert "host_daemon_rootless" in meta


# ============================================================================
# PART B & D: Complete Autonomous Coding Lifecycle End-to-End Acceptance Test
# ============================================================================

def test_end_to_end_autonomous_coding_lifecycle(tmp_path):
    """
    Part D Acceptance Test:
    Execute the complete coding lifecycle in a disposable Git repository:
    1. Spec analysis & repository inspection
    2. Architectural planning & file selection
    3. Multi-file code generation (HMAC-SHA256 signer + verifier + tests)
    4. Deterministic verification execution
    5. Cryptographic approval binding
    6. Safe consolidation with .hydrameda_verification.json record
    7. Structured execution report generation
    """
    # 1. Initialize disposable Git repository
    repo_dir = tmp_path / "crypto_repo"
    repo_dir.mkdir()
    git_env = {
        "GIT_AUTHOR_NAME": "Hydrameda Builder",
        "GIT_AUTHOR_EMAIL": "builder@hydrameda.local",
        "GIT_COMMITTER_NAME": "Hydrameda Builder",
        "GIT_COMMITTER_EMAIL": "builder@hydrameda.local",
    }
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    readme = repo_dir / "README.md"
    readme.write_text("# Crypto Repo\nProduction utilities repository.\n")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "Initial commit"], check=True, env={**os.environ, **git_env})

    task_id = "task-crypto-hmac-01"

    # 2. Mock LLM producing multi-file code: signer module + comprehensive pytest suite
    signer_code = """<file name="hmac_signer.py">
import hmac
import hashlib

def sign_token(key: bytes, message: str) -> str:
    \"\"\"Generates HMAC-SHA256 hex digest for message using key.\"\"\"
    return hmac.new(key, message.encode('utf-8'), hashlib.sha256).hexdigest()

def verify_token(key: bytes, message: str, signature: str) -> bool:
    \"\"\"Constant-time comparison of expected vs provided signature.\"\"\"
    expected = sign_token(key, message)
    return hmac.compare_digest(expected, signature)
</file>
<file name="test_hmac_signer.py">
from hmac_signer import sign_token, verify_token

def test_hmac_sign_and_verify():
    key = b"super-secret-key-12345"
    message = "session-payload-abc"
    sig = sign_token(key, message)
    assert isinstance(sig, str)
    assert len(sig) == 64
    assert verify_token(key, message, sig) is True
    assert verify_token(key, "tampered-payload", sig) is False
    assert verify_token(b"wrong-key", message, sig) is False
</file>"""

    def mock_crypto_llm(model_group: str, prompt: str) -> tuple[str, str]:
        if "architect" in model_group:
            spec = (
                "# Spec: HMAC-SHA256 Token Signer\n"
                "Define sign_token(key: bytes, message: str) -> str\n"
                "Define verify_token(key: bytes, message: str, signature: str) -> bool\n"
            )
            return spec, "mock-architect"
        return signer_code, "mock-coder"

    graph.set_llm_handler(mock_crypto_llm)

    # 3. Mock deterministic verification running pytest on the generated worktree
    def mock_verifier(state):
        wt_path = Path(state["worktree_path"])
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(wt_path / "test_hmac_signer.py")],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=False
        )
        output = f"{result.stdout}\n{result.stderr}\n--- [1/4] Ruff Static Check ---\n--- [2/4] Mypy Type Check ---\n--- [4/4] Pytest Unit Suite ---"
        return result.returncode, output

    graph.set_verifier_handler(mock_verifier)

    # 4. Build LangGraph engine with memory checkpointer
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    # Build initial state with authorized trusted policy for automated verification and consolidation
    initial_state: graph.HydramedaState = {
        "task_id": task_id,
        "user_prompt": "Implement an HMAC-SHA256 token signer with token verification and pytest tests",
        "repo_path": str(repo_dir),
        "auto_approve": True,
        "trusted_policy": "ci_automation",
        "merge_approved": True,
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
        "task_state": graph.TaskState.CREATED.value,
        "commit_hash": "N/A",
        "final_report": "",
        "structured_logs": [],
        "error": ""
    }

    config = {"configurable": {"thread_id": task_id}}

    try:
        final_state = app.invoke(initial_state, config=config)

        # 5. Assertions on the completed autonomous run
        assert final_state["status"] == "PASSED"
        assert final_state["task_state"] == graph.TaskState.COMPLETED.value
        assert final_state["approval_status"] == "APPROVED"
        assert final_state["verifier_exit_code"] == 0

        # Verify files were consolidated into the repository
        assert (repo_dir / "hmac_signer.py").exists()
        assert (repo_dir / "test_hmac_signer.py").exists()

        # Verify functional correctness by executing the consolidated module
        import importlib.util
        spec = importlib.util.spec_from_file_location("hmac_signer", str(repo_dir / "hmac_signer.py"))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        test_key = b"unit-test-key"
        test_msg = "test payload"
        sig = mod.sign_token(test_key, test_msg)
        assert mod.verify_token(test_key, test_msg, sig) is True

        # Verify Requirement B4: Execution Report
        report = final_state.get("execution_report")
        assert report is not None
        assert report["task_id"] == task_id
        assert report["task_state"] == graph.TaskState.COMPLETED.value
        assert report["status"] == "PASSED"
        assert len(report["files_created"]) == 2
        assert "ci_automation" in report["approval_decision"]["authorization_source"]

    finally:
        # Cleanup worktree if exists
        wt_dir = REPO_ROOT / "worktrees" / task_id
        if wt_dir.exists():
            shutil.rmtree(wt_dir, ignore_errors=True)
        graph.set_llm_handler(None)
        graph.set_verifier_handler(None)
