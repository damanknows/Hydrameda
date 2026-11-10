"""
Hydrameda Security & Reliability Remediation Sprint Test Suite.

Covers:
 1. Approval defaults (fail-closed, auto_approve=False by default)
 2. Missing approval data rejected (never interpreted as consent)
 3. Unauthorized deletion protection
 4. Path traversal reads prevented in prepare_manifest (no outside-worktree reads)
 5. Symlink escape detection and prevention
 6. Rollback failure handling, exception propagation, and backup preservation
 7. Sandbox timeout handling and container cleanup (kill + rm -f)
 8. Benchmark result schema, methodology distinctions, and archiving
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ENGINE_DIR = REPO_ROOT / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from langgraph.checkpoint.memory import MemorySaver

from code_engine import (
    ApprovalDecision,
    ChangeManifest,
    FileChange,
    FileOperation,
    GovernanceEngine,
    PatchEngine,
    PatchValidator,
    ValidationError,
)
from evaluate_benchmark import write_reports
from langgraph_engine import graph
from verification_engine.models import CheckStatus
from verification_engine.sandbox_runner import SandboxConfig, SandboxRunner


@pytest.fixture
def temp_worktree():
    """Creates a temporary worktree with a mock git repository."""
    d = Path(tempfile.mkdtemp(prefix="hydrameda_remediation_wt_"))
    src = d / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.py").write_text("def app(): return 'clean'\n", encoding="utf-8")
    (src / "config.py").write_text("PORT = 8080\n", encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================================
# 1. Test Approval Defaults (Fail-Closed)
# ============================================================================
def test_approval_defaults_fail_closed():
    """Verifies auto_approve, destructive_approved, and merge_approved default to False."""
    # 1. ApprovalDecision model defaults
    decision = ApprovalDecision()
    assert decision.approved is False
    assert decision.destructive_approved is False
    assert decision.merge_approved is False
    assert decision.approved_manifest_hash is None

    # 2. Node approval_gate defaults to auto_approve=False
    unconfigured_state: graph.HydramedaState = {
        "task_id": "test-default-gate",
        "user_prompt": "Test prompt",
        "spec_md": "# Spec",
        # Note: auto_approve is omitted; must default to False!
    }

    # Verify interrupt is triggered rather than auto-approving
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-default-gate"}}
    app.invoke(unconfigured_state, config)
    snapshot = app.get_state(config)
    assert snapshot.next == ("approval_gate",)
    assert len(snapshot.tasks[0].interrupts) == 1
    assert snapshot.tasks[0].interrupts[0].value["action"] == "review_specification"


# ============================================================================
# 2. Test Missing Approval Data Rejected
# ============================================================================
def test_missing_approval_data_rejected():
    """Verifies that missing or invalid approval data is never interpreted as consent."""
    manifest = ChangeManifest(
        task_id="test-missing-auth",
        description="Test",
        changes=[FileChange("file.py", FileOperation.CREATE, "x = 1", authorized=True)]
    )

    # 1. None decision
    valid, reasons = GovernanceEngine.validate_approval(None, manifest)
    assert valid is False
    assert any("Missing approval decision" in r for r in reasons)

    # 2. Unapproved decision
    decision_unapproved = ApprovalDecision(approved=False)
    valid, reasons = GovernanceEngine.validate_approval(decision_unapproved, manifest)
    assert valid is False
    assert any("Approval explicitly denied" in r for r in reasons)

    # 3. Missing manifest hash binding
    decision_no_hash = ApprovalDecision(approved=True, approved_manifest_hash="")
    valid, reasons = GovernanceEngine.validate_approval(decision_no_hash, manifest)
    assert valid is False
    assert any("Approval lacks cryptographic hash binding" in r for r in reasons)


# ============================================================================
# 3. Test Unauthorized Deletion Protection
# ============================================================================
def test_unauthorized_deletion_protection(temp_worktree):
    """Verifies that DELETE without explicit authorization is strictly rejected."""
    validator = PatchValidator(temp_worktree, allow_delete=False)

    # 1. Validator rejects unauthorized delete
    del_change = FileChange(file_path="src/app.py", operation=FileOperation.DELETE, authorized=False)
    with pytest.raises(ValidationError) as exc:
        validator.validate_change(del_change)
    assert "Destructive operation REJECTED" in str(exc.value)

    # 2. Governance rejects destructive manifest without destructive_approved=True
    manifest = ChangeManifest(
        task_id="test-destruct-auth",
        description="Delete file",
        changes=[FileChange("src/app.py", FileOperation.DELETE, authorized=True)]
    )
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        destructive_approved=False
    )
    valid, reasons = GovernanceEngine.validate_approval(decision, manifest)
    assert valid is False
    assert any("DESTRUCTIVE OPERATIONS UNAPPROVED" in r for r in reasons)


# ============================================================================
# 4. Test Path Traversal Reads Prevented in prepare_manifest
# ============================================================================
def test_path_traversal_reads_prevented_in_prepare_manifest(temp_worktree):
    """Verifies prepare_manifest validates safety BEFORE accessing disk, preventing outside-content exposure."""
    engine = PatchEngine(temp_worktree, allow_delete=True)

    # Attempt path traversal targeting a hypothetical host file
    traversal_changes = [
        {"file_path": "../../etc/shadow", "operation": "MODIFY", "proposed_content": "hacked"}
    ]

    with pytest.raises(ValidationError) as exc:
        engine.prepare_manifest(traversal_changes, task_id="test-traversal-read")

    assert "Path traversal detected" in str(exc.value)

    # Absolute path attempt
    absolute_changes = [
        {"file_path": "/etc/passwd", "operation": "MODIFY", "proposed_content": "root:x:0:0"}
    ]
    with pytest.raises(ValidationError) as exc_abs:
        engine.prepare_manifest(absolute_changes, task_id="test-abs-read")

    assert "Path traversal detected" in str(exc_abs.value)


# ============================================================================
# 5. Test Symlink Escape Detection and Prevention
# ============================================================================
def test_symlink_escape_prevention(temp_worktree):
    """Verifies that symlinks pointing outside the worktree boundary are rejected."""
    validator = PatchValidator(temp_worktree, allow_delete=True)

    # Create an outside secret file
    outside_dir = Path(tempfile.mkdtemp(prefix="hydrameda_outside_"))
    outside_secret = outside_dir / "secret.txt"
    outside_secret.write_text("CONFIDENTIAL", encoding="utf-8")

    try:
        # Create a symlink inside worktree pointing outside
        symlink_path = temp_worktree / "src" / "symlink_leak"
        symlink_path.symlink_to(outside_secret)

        # 1. Validation must detect symlink escape
        with pytest.raises(ValidationError) as exc:
            validator.validate_path_safety("src/symlink_leak")
        assert "Symlink traversal detected" in str(exc.value)

        # 2. prepare_manifest must reject symlink escape without reading outside content
        engine = PatchEngine(temp_worktree, allow_delete=True)
        with pytest.raises(ValidationError) as exc_manifest:
            engine.prepare_manifest([
                {"file_path": "src/symlink_leak", "operation": "MODIFY", "proposed_content": "leak"}
            ])
        assert "Symlink traversal detected" in str(exc_manifest.value)

    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


# ============================================================================
# 6. Test Rollback Failure Handling and Backup Preservation
# ============================================================================
def test_rollback_failure_handling_and_backup_preservation(temp_worktree):
    """Verifies that rollback errors are not swallowed, restoration is tracked, and backups are preserved."""
    engine = PatchEngine(temp_worktree, allow_delete=True)
    manifest = engine.prepare_manifest(
        changes=[
            {"file_path": "src/config.py", "operation": "MODIFY", "proposed_content": "TIMEOUT = 999\n"},
            {"file_path": "src/new_file.py", "operation": "CREATE", "proposed_content": "X = 1\n"}
        ],
        task_id="test-rollback-fail"
    )
    engine.apply_manifest(manifest)
    backup_dir = Path(manifest.metadata["backup_dir"])
    assert backup_dir.exists()

    # Simulate an un-unlinkable file or permission failure during rollback
    target_new_file = temp_worktree / "src" / "new_file.py"
    assert target_new_file.exists()

    # Mock unlink to raise PermissionError
    with patch.object(Path, "unlink", side_effect=PermissionError("Permission denied: cannot delete")):
        success = engine.rollback_manifest(manifest)

        # Must return False (failure is NOT swallowed!)
        assert success is False

        # Incomplete rollback data is recorded
        assert "rollback_failures" in manifest.metadata
        assert "src/new_file.py" in manifest.metadata["rollback_failures"]

        # CRITICAL SAFETY REQUIREMENT: Backup directory MUST be preserved when rollback fails!
        assert backup_dir.exists(), "Backup directory was prematurely deleted during failed rollback!"


# ============================================================================
# 7. Test Sandbox Timeout Handling and Container Cleanup
# ============================================================================
def test_sandbox_timeout_handling_and_cleanup(temp_worktree):
    """Verifies that when a sandbox command times out, both docker kill and docker rm -f are called."""
    runner = SandboxRunner(SandboxConfig(timeout_seconds=0.1))

    with patch("subprocess.run") as mock_run:
        # First call (docker run) raises TimeoutExpired
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=["docker", "run"], timeout=0.1),
            MagicMock(returncode=0),  # docker kill
            MagicMock(returncode=0),  # docker rm -f
        ]

        result = runner.run_tool_check(temp_worktree, "pytest", "pytest", custom_timeout=0.1)

        assert result.status == CheckStatus.FAILED
        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

        # Verify docker kill and docker rm -f were executed
        calls = [c[0][0] for c in mock_run.call_args_list]
        kill_calls = [c for c in calls if len(c) >= 2 and c[0] == "docker" and c[1] == "kill"]
        rm_calls = [c for c in calls if len(c) >= 3 and c[0] == "docker" and c[1] == "rm" and c[2] == "-f"]

        assert len(kill_calls) == 1, "docker kill was not invoked upon timeout"
        assert len(rm_calls) == 1, "docker rm -f was not invoked upon timeout"


# ============================================================================
# 8. Test Benchmark Result Schema, Methodology, and Archiving
# ============================================================================
def test_benchmark_result_schema_and_archiving():
    """Verifies benchmark schema distinguishes zero-shot from self-healed, records env, and archives past runs."""
    temp_dir = Path(tempfile.mkdtemp(prefix="hydrameda_bench_test_"))
    results_path = temp_dir / "benchmark_results.json"
    report_md = temp_dir / "report.md"
    archive_dir = temp_dir / "archive"

    mock_env = {
        "python_version": "3.12.16",
        "platform": "Linux-test",
        "git_commit": "abc1234",
        "models": {"coder": "hybrid-coder"}
    }

    mock_tasks = [
        {
            "task_id": "task-01",
            "category": "HumanEval",
            "passed": True,
            "initial_pass": True,
            "repair_cycles_used": 0,
            "failure_type": None,
            "elapsed_sec": 10.5
        },
        {
            "task_id": "task-02",
            "category": "SecurityEval",
            "passed": True,
            "initial_pass": False,
            "repair_cycles_used": 2,
            "failure_type": None,
            "elapsed_sec": 30.2
        },
        {
            "task_id": "task-03",
            "category": "RepoBench",
            "passed": False,
            "initial_pass": False,
            "repair_cycles_used": 3,
            "failure_type": "task_failure",
            "elapsed_sec": 45.0
        }
    ]

    try:
        # Run 1: Write reports
        write_reports(mock_tasks, "all", results_path, report_md, archive_dir, mock_env)

        assert results_path.exists()
        assert report_md.exists()
        data1 = json.loads(results_path.read_text(encoding="utf-8"))

        # Verify rigorous schema distinction
        assert data1["summary"]["total"] == 3
        assert data1["summary"]["initial_pass"] == 1
        assert data1["summary"]["self_healed_completion"] == 2
        assert data1["summary"]["initial_pass_rate"] == 33.3
        assert data1["summary"]["self_healed_completion_rate"] == 66.7
        assert "methodology" in data1["summary"]
        assert "environment" in data1["summary"]
        assert data1["summary"]["environment"]["git_commit"] == "abc1234"

        # Run 2: Write reports again -> must archive previous run
        write_reports(mock_tasks, "all", results_path, report_md, archive_dir, mock_env)
        archived_files = list(archive_dir.glob("benchmark_results_*.json"))
        assert len(archived_files) >= 1, "Previous benchmark run was not archived before writing!"

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
