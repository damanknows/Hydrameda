import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "langgraph_engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))


@pytest.fixture(autouse=True)
def clean_test_worktrees():
    """Ensures test worktrees (test-*, task-*) are cleaned up before and after each test."""
    wt_dir = ROOT / "worktrees"

    def _cleanup():
        if wt_dir.exists():
            for p in wt_dir.glob("test-*"):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            for p in wt_dir.glob("task-*"):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
        subprocess.run(["git", "-C", str(ROOT), "worktree", "prune"], check=False, capture_output=True)

    _cleanup()
    yield
    _cleanup()
