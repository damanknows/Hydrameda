"""Tests for MCP tools, Gateway security, and Preprocessor AST extraction."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "langgraph_engine"))

from preprocessor import extract_ast_skeleton

import mcp_server.server as mcp_srv


def test_ast_skeleton_ignores_hidden_and_virtualenvs(tmp_path):
    # Setup mock repo with hidden directory and normal directory
    normal_dir = tmp_path / "src"
    normal_dir.mkdir()
    (normal_dir / "app.py").write_text("def hello() -> str:\n    return 'world'\n")

    hidden_dir = tmp_path / ".python-3.12"
    hidden_dir.mkdir()
    (hidden_dir / "stdlib.py").write_text("def internal_cpython():\n    pass\n")

    skeleton = extract_ast_skeleton(str(tmp_path), max_chars=2000)
    assert "src/app.py" in skeleton or "app.py" in skeleton
    assert "hello" in skeleton
    assert ".python-3.12" not in skeleton
    assert "internal_cpython" not in skeleton


def test_gateway_token_verification(monkeypatch):
    import hydrameda_openai_gateway as gw

    # Valid token passes
    gw.verify_token(f"Bearer {gw.MASTER_KEY}")

    # Missing header raises 401
    with pytest.raises(HTTPException) as exc1:
        gw.verify_token(None)
    assert exc1.value.status_code == 401

    # Invalid token raises 401
    with pytest.raises(HTTPException) as exc2:
        gw.verify_token("Bearer invalid-secret-token")
    assert exc2.value.status_code == 401

    # Wrong scheme raises 401
    with pytest.raises(HTTPException) as exc3:
        gw.verify_token(f"Basic {gw.MASTER_KEY}")
    assert exc3.value.status_code == 401


def test_mcp_verified_worktree_merge_guard(tmp_path, monkeypatch):
    # Setup temporary mock worktree
    fake_wt_dir = tmp_path / "worktrees"
    fake_wt_dir.mkdir()
    task_dir = fake_wt_dir / "test-unverified-task"
    task_dir.mkdir()
    (task_dir / "module.py").write_text("def dummy(): pass\n")

    # Initialize git repo in task_dir with unverified commit message
    import subprocess
    subprocess.run(["git", "init", str(task_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(task_dir), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(task_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(task_dir), "add", "."], check=True)
    subprocess.run(["git", "-C", str(task_dir), "commit", "-m", "Initial root commit"], check=True, capture_output=True)

    monkeypatch.setattr(mcp_srv, "WORKTREES_DIR", fake_wt_dir)

    target_export = tmp_path / "export"

    # Merge should fail without verified commit
    res = mcp_srv.merge_verified_worktree("test-unverified-task", str(target_export), force=False)
    assert "Refusing to merge unverified worktree" in res
    assert not (target_export / "module.py").exists()

    # Merge succeeds with force=True
    res_forced = mcp_srv.merge_verified_worktree("test-unverified-task", str(target_export), force=True)
    assert "Exported 1 verified files" in res_forced
    assert (target_export / "module.py").exists()
