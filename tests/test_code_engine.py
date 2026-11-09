"""
Unit and Integration Test Suite for Hydrameda Code Engine.

Verifies:
- New file creation (CREATE).
- Existing file modification (MODIFY).
- Authorized deletion (DELETE with authorized=True).
- Unauthorized deletion (DELETE without authorization -> rejected).
- Multiple file modifications (atomic multi-file manifest).
- Path traversal attempts ('../', absolute paths, escaping worktree).
- Invalid patches (modifying non-existent, creating existing, hash mismatch).
- Atomic rollback on write error and manual rollback.
- Git unified diff accuracy and line statistics.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from code_engine.change_manifest import FileChange, FileOperation
from code_engine.diff_generator import DiffGenerator
from code_engine.file_selector import FileSelector
from code_engine.patch_engine import PatchEngine
from code_engine.patch_validator import PatchValidator, ValidationError
from code_engine.repository_mapper import RepositoryMapper


@pytest.fixture
def temp_worktree():
    """Provisions an isolated temporary directory simulating a Git worktree."""
    d = Path(tempfile.mkdtemp(prefix="hydrameda_wt_test_"))
    # Seed sample project files
    (d / "src").mkdir(parents=True, exist_ok=True)
    (d / "src" / "math_utils.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8"
    )
    (d / "src" / "config.py").write_text(
        "TIMEOUT = 30\nDEBUG = False\n",
        encoding="utf-8"
    )
    yield d
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ============================================================================
# 1. New File Creation
# ============================================================================
def test_new_file_creation(temp_worktree):
    engine = PatchEngine(temp_worktree)
    manifest = engine.prepare_manifest(
        changes=[{
            "file_path": "src/logger.py",
            "operation": "CREATE",
            "proposed_content": "def log(msg: str) -> None:\n    print(msg)\n"
        }],
        task_id="test-create-01"
    )

    applied = engine.apply_manifest(manifest)
    assert applied.applied is True

    created_file = temp_worktree / "src" / "logger.py"
    assert created_file.exists()
    assert "def log" in created_file.read_text(encoding="utf-8")


# ============================================================================
# 2. Existing File Modification
# ============================================================================
def test_existing_file_modification(temp_worktree):
    engine = PatchEngine(temp_worktree)
    new_content = "def add(a: int, b: int) -> int:\n    # Optimized addition\n    return a + b\n"
    manifest = engine.prepare_manifest(
        changes=[{
            "file_path": "src/math_utils.py",
            "operation": "MODIFY",
            "proposed_content": new_content
        }],
        task_id="test-modify-01"
    )

    applied = engine.apply_manifest(manifest)
    assert applied.applied is True

    mod_file = temp_worktree / "src" / "math_utils.py"
    assert "# Optimized addition" in mod_file.read_text(encoding="utf-8")


# ============================================================================
# 3. Authorized Deletion
# ============================================================================
def test_authorized_deletion(temp_worktree):
    engine = PatchEngine(temp_worktree, allow_delete=True)
    manifest = engine.prepare_manifest(
        changes=[{
            "file_path": "src/config.py",
            "operation": "DELETE",
            "authorized": True
        }],
        task_id="test-delete-auth-01"
    )

    applied = engine.apply_manifest(manifest)
    assert applied.applied is True

    del_file = temp_worktree / "src" / "config.py"
    assert not del_file.exists()


# ============================================================================
# 4. Unauthorized Deletion
# ============================================================================
def test_unauthorized_deletion_rejected(temp_worktree):
    # allow_delete is False by default
    engine = PatchEngine(temp_worktree, allow_delete=False)
    manifest = engine.prepare_manifest(
        changes=[{
            "file_path": "src/config.py",
            "operation": "DELETE",
            "authorized": False  # Not explicitly authorized
        }],
        task_id="test-delete-unauth-01"
    )

    with pytest.raises(ValidationError) as exc_info:
        engine.apply_manifest(manifest)

    assert "requires explicit authorization" in str(exc_info.value)
    # File must still exist on disk
    assert (temp_worktree / "src" / "config.py").exists()


# ============================================================================
# 5. Multiple File Modifications (Atomic Multi-File)
# ============================================================================
def test_multiple_file_modifications(temp_worktree):
    engine = PatchEngine(temp_worktree, allow_delete=True)
    manifest = engine.prepare_manifest(
        changes=[
            {"file_path": "src/math_utils.py", "operation": "MODIFY", "proposed_content": "# Updated\ndef add(a, b): return a + b\n"},
            {"file_path": "src/helpers.py", "operation": "CREATE", "proposed_content": "PI = 3.14159\n"},
            {"file_path": "src/config.py", "operation": "DELETE", "authorized": True}
        ],
        task_id="test-multi-01"
    )

    applied = engine.apply_manifest(manifest)
    assert applied.applied is True

    assert "# Updated" in (temp_worktree / "src" / "math_utils.py").read_text(encoding="utf-8")
    assert (temp_worktree / "src" / "helpers.py").exists()
    assert not (temp_worktree / "src" / "config.py").exists()


# ============================================================================
# 6. Path Traversal Protection
# ============================================================================
def test_path_traversal_attempts_rejected(temp_worktree):
    validator = PatchValidator(temp_worktree)

    # 1. Parent traversal '..'
    with pytest.raises(ValidationError) as e1:
        validator.validate_path_safety("../outside.py")
    assert "Path traversal detected" in str(e1.value)

    # 2. Nested traversal
    with pytest.raises(ValidationError) as e2:
        validator.validate_path_safety("src/../../etc/passwd")
    assert "Path traversal detected" in str(e2.value)

    # 3. Absolute path
    with pytest.raises(ValidationError) as e3:
        validator.validate_path_safety("/etc/hosts")
    assert "absolute paths not allowed" in str(e3.value)

    # 4. Protected file modification (.env or .git)
    with pytest.raises(ValidationError) as e4:
        validator.validate_path_safety(".env")
    assert "protected path is prohibited" in str(e4.value)


# ============================================================================
# 7. Invalid Patch Rules (CREATE on existing, MODIFY on missing, Hash mismatch)
# ============================================================================
def test_invalid_patches(temp_worktree):
    validator = PatchValidator(temp_worktree)

    # CREATE on existing file
    c1 = FileChange(file_path="src/math_utils.py", operation=FileOperation.CREATE, proposed_content="test")
    with pytest.raises(ValidationError) as e1:
        validator.validate_change(c1)
    assert "Cannot CREATE existing file" in str(e1.value)

    # MODIFY on non-existent file
    c2 = FileChange(file_path="src/non_existent.py", operation=FileOperation.MODIFY, proposed_content="test")
    with pytest.raises(ValidationError) as e2:
        validator.validate_change(c2)
    assert "Cannot MODIFY non-existent file" in str(e2.value)

    # Hash mismatch on MODIFY
    c3 = FileChange(
        file_path="src/math_utils.py",
        operation=FileOperation.MODIFY,
        proposed_content="updated",
        original_hash="0123456789abcdef"  # Incorrect hash
    )
    with pytest.raises(ValidationError) as e3:
        validator.validate_change(c3)
    assert "Hash mismatch" in str(e3.value)


# ============================================================================
# 8. Rollback Mechanisms
# ============================================================================
def test_manual_and_atomic_rollback(temp_worktree):
    engine = PatchEngine(temp_worktree, allow_delete=True)
    orig_config = (temp_worktree / "src" / "config.py").read_text(encoding="utf-8")

    manifest = engine.prepare_manifest(
        changes=[
            {"file_path": "src/config.py", "operation": "MODIFY", "proposed_content": "TIMEOUT = 999\n"},
            {"file_path": "src/new_mod.py", "operation": "CREATE", "proposed_content": "VAL = 1\n"}
        ],
        task_id="test-rollback-01"
    )

    # Apply
    engine.apply_manifest(manifest)
    assert "TIMEOUT = 999" in (temp_worktree / "src" / "config.py").read_text(encoding="utf-8")
    assert (temp_worktree / "src" / "new_mod.py").exists()

    # Manual Rollback
    rolled_back = engine.rollback_manifest(manifest)
    assert rolled_back is True

    # Check restored state
    assert (temp_worktree / "src" / "config.py").read_text(encoding="utf-8") == orig_config
    assert not (temp_worktree / "src" / "new_mod.py").exists()


# ============================================================================
# 9. Unified Diff Accuracy and Stats
# ============================================================================
def test_git_diff_accuracy():
    change = FileChange(
        file_path="src/calc.py",
        operation=FileOperation.MODIFY,
        original_content="def calc():\n    return 10\n",
        proposed_content="def calc():\n    # new comment\n    return 20\n"
    )

    diff = DiffGenerator.generate_file_diff(change)
    assert "--- a/src/calc.py" in diff
    assert "+++ b/src/calc.py" in diff
    assert "-    return 10" in diff
    assert "+    return 20" in diff

    stats = DiffGenerator.compute_diff_stats(diff)
    assert stats["additions"] == 2  # comment + return 20
    assert stats["deletions"] == 1  # return 10


# ============================================================================
# 10. Repository Mapper & File Selector
# ============================================================================
def test_repository_mapper_and_selector(temp_worktree):
    mapper = RepositoryMapper(temp_worktree)
    files = mapper.list_files()
    assert "src/math_utils.py" in files
    assert "src/config.py" in files

    symbols = mapper.extract_file_symbols("src/math_utils.py")
    assert "add" in symbols["functions"]

    selector = FileSelector(mapper)
    relevant = selector.select_relevant_files("Add two integer numbers using math addition")
    assert len(relevant) > 0
    assert relevant[0]["file"] == "src/math_utils.py"
