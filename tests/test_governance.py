#!/usr/bin/env python3
"""
Test suite for Hydrameda Governance, Risk Assessment, Cryptographic Approval Binding, and Safe Consolidation.

Covers:
 1. Risk classification (LOW, MEDIUM, HIGH, CRITICAL)
 2. Review display formatting (spec, diff, operations, hash, verification)
 3. Valid cryptographic approval validation
 4. Stale approval revocation (hash mutation prevents authorization)
 5. Destructive operation protection (DELETE requires explicit authorization)
 6. Unverified code cannot be consolidated
 7. Worktree isolation preservation (main repo untouched when merge_approved=False)
 8. Authorized merge into main branch when merge_approved=True
 9. Rejection triggers automatic rollback
10. Stale approval triggers automatic rollback
11. LangGraph pre-consolidation interrupt and resumption with approval
12. LangGraph pre-consolidation rejection halts execution without commit
"""
import pytest
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ENGINE_DIR = REPO_ROOT / "langgraph_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from code_engine import (
    FileOperation,
    FileChange,
    ChangeManifest,
    PatchEngine,
    DiffGenerator,
    RiskLevel,
    RiskAssessment,
    ApprovalDecision,
    ConsolidationReport,
    GovernanceEngine,
    SafeConsolidator,
)
from langgraph_engine import graph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


@pytest.fixture
def temp_git_repo():
    """Creates a temporary Git repository simulating the main repository."""
    repo_dir = Path(tempfile.mkdtemp(prefix="hydrameda_gov_test_repo_"))
    subprocess.run(["git", "init", str(repo_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test Runner"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@hydrameda.local"], check=True)
    
    # Create initial file
    initial_file = repo_dir / "existing.py"
    initial_file.write_text("def existing_func():\n    return 42\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "existing.py"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", "Initial commit"], check=True)

    yield repo_dir
    shutil.rmtree(repo_dir, ignore_errors=True)


@pytest.fixture
def temp_worktree(temp_git_repo):
    """Creates an isolated git worktree branch linked to temp_git_repo."""
    wt_dir = Path(tempfile.mkdtemp(prefix="hydrameda_gov_test_wt_"))
    branch_name = "hydrameda/test-gov-task"
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "worktree", "add", "-b", branch_name, str(wt_dir)],
        check=True,
        capture_output=True
    )
    yield wt_dir, temp_git_repo
    subprocess.run(["git", "-C", str(temp_git_repo), "worktree", "remove", "--force", str(wt_dir)], check=False, capture_output=True)
    subprocess.run(["git", "-C", str(temp_git_repo), "branch", "-D", branch_name], check=False, capture_output=True)
    shutil.rmtree(wt_dir, ignore_errors=True)


# ============================================================================
# 1. Test Risk Classification Levels
# ============================================================================
def test_risk_classification_levels():
    """Verifies LOW, MEDIUM, HIGH, and CRITICAL risk levels."""
    # 1. LOW Risk: Additive changes, 1-2 files, verification passed
    m_low = ChangeManifest(
        task_id="task-low",
        description="Add utility function",
        changes=[
            FileChange("utils.py", FileOperation.CREATE, "def util(): pass", authorized=True)
        ]
    )
    risk_low = GovernanceEngine.assess_risk(m_low, verification_passed=True)
    assert risk_low.level == RiskLevel.LOW
    assert not risk_low.destructive_operations

    # 2. MEDIUM Risk: Modifying multiple existing files
    m_med = ChangeManifest(
        task_id="task-med",
        description="Modify 3 files",
        changes=[
            FileChange("file1.py", FileOperation.MODIFY, "x = 1", authorized=True),
            FileChange("file2.py", FileOperation.MODIFY, "y = 2", authorized=True),
            FileChange("file3.py", FileOperation.MODIFY, "z = 3", authorized=True),
        ]
    )
    risk_med = GovernanceEngine.assess_risk(m_med, verification_passed=True)
    assert risk_med.level == RiskLevel.MEDIUM

    # 3. HIGH Risk: Critical infrastructure files (e.g. pyproject.toml) or >5 files
    m_high = ChangeManifest(
        task_id="task-high",
        description="Modify setup config",
        changes=[
            FileChange("pyproject.toml", FileOperation.MODIFY, "[project]", authorized=True)
        ]
    )
    risk_high = GovernanceEngine.assess_risk(m_high, verification_passed=True)
    assert risk_high.level == RiskLevel.HIGH
    assert any("critical infrastructure" in r for r in risk_high.reasons)

    # 4. CRITICAL Risk: Destructive DELETE operations
    m_crit_del = ChangeManifest(
        task_id="task-crit-del",
        description="Delete file",
        changes=[
            FileChange("obsolete.py", FileOperation.DELETE, authorized=True)
        ]
    )
    risk_crit_del = GovernanceEngine.assess_risk(m_crit_del, verification_passed=True)
    assert risk_crit_del.level == RiskLevel.CRITICAL
    assert "obsolete.py" in risk_crit_del.destructive_operations

    # 5. CRITICAL Risk: Verification failed
    risk_crit_unverified = GovernanceEngine.assess_risk(m_low, verification_passed=False)
    assert risk_crit_unverified.level == RiskLevel.CRITICAL
    assert any("failed" in r.lower() for r in risk_crit_unverified.reasons)


# ============================================================================
# 2. Test Review Display Formatting
# ============================================================================
def test_format_review_display():
    """Verifies that format_review_display produces all required transparency components."""
    manifest = ChangeManifest(
        task_id="task-display-01",
        description="Implement user authentication module",
        changes=[
            FileChange("auth.py", FileOperation.CREATE, "def authenticate(): return True", authorized=True),
            FileChange("old_auth.py", FileOperation.DELETE, authorized=True),
        ]
    )
    DiffGenerator.generate_manifest_diff(manifest)
    risk = GovernanceEngine.assess_risk(manifest, verification_passed=True)

    spec = "# Auth Spec\nProvide secure authentication."
    verification_summary = {
        "overall_status": "passed",
        "checks": {
            "syntax_validation": {"status": "passed"},
            "pytest": {"status": "passed"}
        }
    }

    display = GovernanceEngine.format_review_display(
        spec_md=spec,
        manifest=manifest,
        risk=risk,
        verification_summary=verification_summary
    )

    # Validate essential display sections
    assert manifest.task_id in display
    assert manifest.manifest_hash in display
    assert "Risk Level" in display
    assert "auth.py" in display
    assert "old_auth.py" in display
    assert "CREATE" in display
    assert "DELETE" in display
    assert "Verification Status" in display
    assert "syntax_validation" in display
    assert "Proposed Unified Diff" in display
    assert "Auth Spec" in display


# ============================================================================
# 3. Test Cryptographic Approval Validation (Success)
# ============================================================================
def test_cryptographic_approval_validation_success():
    """Verifies that valid approval matching the exact manifest hash succeeds."""
    manifest = ChangeManifest(
        task_id="task-approve-01",
        description="Safe modification",
        changes=[
            FileChange("calc.py", FileOperation.CREATE, "def add(a, b): return a + b", authorized=True)
        ]
    )
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        approver="senior-engineer",
        destructive_approved=False,
        merge_approved=True
    )

    valid, reasons = GovernanceEngine.validate_approval(decision, manifest, verification_passed=True)
    assert valid is True
    assert "verified and valid" in reasons[0]


# ============================================================================
# 4. Test Stale Approval Revocation
# ============================================================================
def test_cryptographic_approval_stale_hash_revocation():
    """Verifies that if code changes after approval was granted, approval is revoked."""
    manifest_v1 = ChangeManifest(
        task_id="task-stale-01",
        description="Version 1",
        changes=[
            FileChange("service.py", FileOperation.CREATE, "def serve(): return 'v1'", authorized=True)
        ]
    )
    v1_hash = manifest_v1.manifest_hash

    # Engineer approved Version 1
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=v1_hash,
        approver="tech-lead"
    )

    # Agent performs a repair cycle, mutating the proposed code
    manifest_v2 = ChangeManifest(
        task_id="task-stale-01",
        description="Version 2 (repaired)",
        changes=[
            FileChange("service.py", FileOperation.CREATE, "def serve(): return 'v2 repaired'", authorized=True)
        ]
    )
    v2_hash = manifest_v2.manifest_hash
    assert v1_hash != v2_hash

    # Validating the old decision against mutated manifest MUST fail
    valid, reasons = GovernanceEngine.validate_approval(decision, manifest_v2, verification_passed=True)
    assert valid is False
    assert any("STALE APPROVAL" in r for r in reasons)


# ============================================================================
# 5. Test Destructive Operation Protection
# ============================================================================
def test_destructive_operation_protection():
    """Verifies that DELETE operations require explicit destructive_approved=True."""
    manifest = ChangeManifest(
        task_id="task-destruct-01",
        description="Delete database credentials",
        changes=[
            FileChange("secrets.json", FileOperation.DELETE, authorized=True)
        ]
    )

    # 1. Approval without destructive authorization
    decision_no_destruct = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        destructive_approved=False
    )
    valid, reasons = GovernanceEngine.validate_approval(decision_no_destruct, manifest, verification_passed=True)
    assert valid is False
    assert any("DESTRUCTIVE OPERATIONS UNAPPROVED" in r for r in reasons)

    # 2. Approval with explicit destructive authorization
    decision_with_destruct = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        destructive_approved=True
    )
    valid, reasons = GovernanceEngine.validate_approval(decision_with_destruct, manifest, verification_passed=True)
    assert valid is True


# ============================================================================
# 6. Test Unverified Code Cannot Be Consolidated
# ============================================================================
def test_unverified_code_cannot_be_consolidated():
    """Verifies that verification failure blocks consolidation regardless of approval."""
    manifest = ChangeManifest(
        task_id="task-unverified-01",
        description="Broken logic",
        changes=[
            FileChange("bad.py", FileOperation.CREATE, "syntax error here", authorized=True)
        ]
    )
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash
    )

    valid, reasons = GovernanceEngine.validate_approval(decision, manifest, verification_passed=False)
    assert valid is False
    assert any("verification checks failed" in r for r in reasons)


# ============================================================================
# 7. Test Worktree Isolation (Main Repo Preserved When Merge Not Approved)
# ============================================================================
def test_safe_consolidation_worktree_only_when_merge_not_approved(temp_worktree):
    """Verifies that changes stay inside the worktree branch when merge_approved=False."""
    wt_dir, main_repo = temp_worktree

    patch_engine = PatchEngine(target_worktree=wt_dir, allow_delete=True)
    manifest = patch_engine.prepare_manifest(
        changes=[{
            "file_path": "new_feature.py",
            "operation": "CREATE",
            "proposed_content": "def new_feature(): return True\n",
            "authorized": True
        }],
        description="Isolated feature branch work",
        task_id="task-wt-only"
    )
    patch_engine.apply_manifest(manifest)

    # Decision approves worktree commit, but NOT merge into main
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        merge_approved=False
    )

    report = SafeConsolidator.consolidate_changes(
        worktree_path=wt_dir,
        main_repo_path=main_repo,
        manifest=manifest,
        decision=decision,
        verification_passed=True
    )

    assert report.status == "COMMITTED_WORKTREE_ONLY"
    assert report.approved is True
    assert report.merged_to_main is False
    assert report.worktree_commit is not None
    assert report.main_commit is None

    # Main repository must NOT have new_feature.py
    assert not (main_repo / "new_feature.py").exists()
    # Worktree MUST have new_feature.py
    assert (wt_dir / "new_feature.py").exists()


# ============================================================================
# 8. Test Authorized Merge into Main Branch
# ============================================================================
def test_safe_consolidation_merge_to_main_when_approved(temp_worktree):
    """Verifies that when merge_approved=True, changes safely merge into main."""
    wt_dir, main_repo = temp_worktree

    patch_engine = PatchEngine(target_worktree=wt_dir, allow_delete=True)
    manifest = patch_engine.prepare_manifest(
        changes=[{
            "file_path": "feature_main.py",
            "operation": "CREATE",
            "proposed_content": "def feature_main(): return 100\n",
            "authorized": True
        }],
        description="Merge to main feature",
        task_id="test-gov-task"
    )
    patch_engine.apply_manifest(manifest)

    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash=manifest.manifest_hash,
        merge_approved=True
    )

    report = SafeConsolidator.consolidate_changes(
        worktree_path=wt_dir,
        main_repo_path=main_repo,
        manifest=manifest,
        decision=decision,
        verification_passed=True
    )

    assert report.status == "CONSOLIDATED"
    assert report.approved is True
    assert report.merged_to_main is True
    assert report.worktree_commit is not None
    assert report.main_commit is not None

    # Main repository now contains the merged file
    assert (main_repo / "feature_main.py").exists()


# ============================================================================
# 9. Test Rejection Triggers Automatic Rollback
# ============================================================================
def test_rejection_triggers_automatic_rollback(temp_worktree):
    """Verifies that explicit human rejection rolls back applied worktree changes."""
    wt_dir, main_repo = temp_worktree

    patch_engine = PatchEngine(target_worktree=wt_dir, allow_delete=True)
    # Modify existing.py
    manifest = patch_engine.prepare_manifest(
        changes=[{
            "file_path": "existing.py",
            "operation": "MODIFY",
            "proposed_content": "def existing_func(): return 'MODIFIED'\n",
            "authorized": True
        }],
        description="Proposed modification",
        task_id="task-reject-rollback"
    )
    patch_engine.apply_manifest(manifest)
    assert (wt_dir / "existing.py").read_text(encoding="utf-8") == "def existing_func(): return 'MODIFIED'\n"

    # Human rejects the change
    decision = ApprovalDecision(approved=False)

    report = SafeConsolidator.consolidate_changes(
        worktree_path=wt_dir,
        main_repo_path=main_repo,
        manifest=manifest,
        decision=decision,
        verification_passed=True
    )

    assert report.status == "REJECTED"
    assert report.approved is False
    assert not manifest.applied

    # File was rolled back to original content
    assert "return 42" in (wt_dir / "existing.py").read_text(encoding="utf-8")


# ============================================================================
# 10. Test Stale Approval Triggers Automatic Rollback
# ============================================================================
def test_stale_approval_triggers_automatic_rollback(temp_worktree):
    """Verifies that stale approval hash mismatch rolls back applied changes."""
    wt_dir, main_repo = temp_worktree

    patch_engine = PatchEngine(target_worktree=wt_dir, allow_delete=True)
    manifest = patch_engine.prepare_manifest(
        changes=[{
            "file_path": "stale_target.py",
            "operation": "CREATE",
            "proposed_content": "# New code\n",
            "authorized": True
        }],
        description="Stale check",
        task_id="task-stale-rollback"
    )
    patch_engine.apply_manifest(manifest)
    assert (wt_dir / "stale_target.py").exists()

    # Decision carries a stale/different hash
    decision = ApprovalDecision(
        approved=True,
        approved_manifest_hash="bad_stale_hash_12345"
    )

    report = SafeConsolidator.consolidate_changes(
        worktree_path=wt_dir,
        main_repo_path=main_repo,
        manifest=manifest,
        decision=decision,
        verification_passed=True
    )

    assert report.status == "STALE_APPROVAL_REJECTED"
    assert report.approved is False
    assert not manifest.applied
    # Created file rolled back (deleted)
    assert not (wt_dir / "stale_target.py").exists()


# ============================================================================
# 11. Test LangGraph Pre-Consolidation Interrupt and Resumption
# ============================================================================
def test_langgraph_consolidation_interrupt_and_resume():
    """Verifies LangGraph interrupts at consolidator when approval is required and resumes with approval."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    def mock_llm(model_group: str, prompt: str):
        if "architect" in model_group:
            return "# Spec for Gov Test\nCreate math module.", "mock-architect"
        code = (
            '<file name="calc.py">\n'
            'def add(a: int, b: int) -> int:\n    return a + b\n'
            '</file>\n'
            '<file name="test_calc.py">\n'
            'from calc import add\n'
            'def test_add():\n    assert add(1, 2) == 3\n'
            '</file>'
        )
        return code, "mock-coder"

    def mock_verifier(state):
        return 0, "1 passed in 0.01s"

    graph.set_llm_handler(mock_llm)
    graph.set_verifier_handler(mock_verifier)

    task_id = "test-gov-interrupt-01"
    initial_state: graph.HydramedaState = {
        "task_id": task_id,
        "user_prompt": "Create an addition function",
        "repo_path": str(Path(__file__).parent.parent),
        "auto_approve": True,  # Spec is auto-approved, but consolidation requires review
        "trusted_policy": "eval_harness",
        "require_consolidation_approval": True,
        "approval_status": "APPROVED",
        "route": "execute",
        "current_files": {},
        "target_files": [],
        "repair_count": 0,
        "structured_logs": []
    }
    config = {"configurable": {"thread_id": task_id}}

    # 1. Execute up to consolidator interrupt
    app.invoke(initial_state, config)
    snapshot = app.get_state(config)
    assert snapshot.next == ("consolidator",)
    assert len(snapshot.tasks[0].interrupts) == 1

    # Inspect interrupt payload
    interrupt_data = snapshot.tasks[0].interrupts[0].value
    assert interrupt_data["action"] == "review_and_consolidate"
    assert "calc.py" in interrupt_data["affected_files"]
    assert "review_display" in interrupt_data

    # 2. Resume execution with human approval
    manifest_hash = interrupt_data["manifest_hash"]
    final_state = app.invoke(
        Command(resume={
            "approved": True,
            "approved_manifest_hash": manifest_hash,
            "merge_approved": False
        }),
        config
    )

    assert final_state["status"] == "PASSED"
    assert final_state["consolidation_report"]["status"] == "COMMITTED_WORKTREE_ONLY"
    assert final_state["commit_hash"] != "N/A"
    assert "consolidation_report" in final_state
    assert final_state["consolidation_report"]["approved"] is True


# ============================================================================
# 12. Test LangGraph Pre-Consolidation Rejection Halts Execution
# ============================================================================
def test_langgraph_consolidation_rejection_halts_execution():
    """Verifies that rejecting changes at consolidator sets status to REJECTED with no commit."""
    app = graph.build_hydrameda_graph(checkpointer=MemorySaver())

    def mock_llm(model_group: str, prompt: str):
        if "architect" in model_group:
            return "# Spec for Reject Test", "mock-architect"
        return '<file name="scratch.py">\nx = 10\n</file>', "mock-coder"

    def mock_verifier(state):
        return 0, "1 passed"

    graph.set_llm_handler(mock_llm)
    graph.set_verifier_handler(mock_verifier)

    task_id = "test-gov-reject-01"
    initial_state: graph.HydramedaState = {
        "task_id": task_id,
        "user_prompt": "Create scratch file",
        "repo_path": str(Path(__file__).parent.parent),
        "auto_approve": True,
        "require_consolidation_approval": True,
        "approval_status": "APPROVED",
        "route": "execute",
        "current_files": {},
        "target_files": [],
        "repair_count": 0,
        "structured_logs": []
    }
    config = {"configurable": {"thread_id": task_id}}

    app.invoke(initial_state, config)
    snapshot = app.get_state(config)
    assert snapshot.next == ("consolidator",)

    # Human rejects the proposed changes
    final_state = app.invoke(Command(resume={"approved": False}), config)

    assert final_state["status"] == "REJECTED"
    assert final_state["approval_status"] == "REJECTED"
    assert final_state["commit_hash"] == "N/A"
    assert final_state["consolidation_report"]["approved"] is False
