import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verification_engine import (
    CheckName,
    CheckResult,
    CheckStatus,
    SandboxConfig,
    SandboxRunner,
    VerificationPipeline,
    VerificationReport,
    run_ast_structural_check,
    run_dependency_security_check,
    run_import_resolution_check,
    run_syntax_check,
)

# ============================================================================
# 1. Verification Report & Status Tests
# ============================================================================

def test_check_status_values():
    assert CheckStatus.PASSED == "passed"
    assert CheckStatus.FAILED == "failed"
    assert CheckStatus.SKIPPED == "skipped"
    assert CheckStatus.NOT_APPLICABLE == "not_applicable"


def test_do_not_treat_skipped_as_successful():
    """Strict verification rule: Skipped checks must NEVER be treated as successful."""
    checks = {
        CheckName.SYNTAX.value: CheckResult(name=CheckName.SYNTAX.value, status=CheckStatus.PASSED),
        CheckName.PYTEST.value: CheckResult(name=CheckName.PYTEST.value, status=CheckStatus.SKIPPED, diagnostic="Pytest was skipped")
    }
    report = VerificationReport(task_id="test-task", overall_status="passed", checks=checks)
    # is_passed() must reject the report because PYTEST was skipped!
    assert report.is_passed() is False


def test_report_serialization():
    checks = {
        CheckName.SYNTAX.value: CheckResult(
            name=CheckName.SYNTAX.value,
            status=CheckStatus.PASSED,
            exit_code=0,
            stdout="OK",
            duration_ms=12.5
        )
    }
    report = VerificationReport(
        task_id="test-report-01",
        overall_status="passed",
        checks=checks,
        total_duration_ms=12.5,
        summary="Report Summary"
    )
    assert report.is_passed() is True
    data = report.to_dict()
    assert data["task_id"] == "test-report-01"
    assert data["checks"][CheckName.SYNTAX.value]["status"] == "passed"
    json_str = report.to_json()
    assert "test-report-01" in json_str


# ============================================================================
# 2. Deterministic Static, AST, & Import Checks
# ============================================================================

def test_syntax_validation_pass(tmp_path):
    (tmp_path / "valid.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    res = run_syntax_check(tmp_path)
    assert res.status == CheckStatus.PASSED
    assert res.exit_code == 0


def test_syntax_validation_fail(tmp_path):
    (tmp_path / "bad_syntax.py").write_text("def broken(\n    return 42\n")
    res = run_syntax_check(tmp_path)
    assert res.status == CheckStatus.FAILED
    assert res.exit_code == 1
    assert "SyntaxError" in res.stderr
    assert "bad_syntax.py" in res.diagnostic


def test_ast_structural_validation_disallows_eval_exec(tmp_path):
    # Safe code
    (tmp_path / "safe.py").write_text("def compute():\n    return 10 * 20\n")
    res_safe = run_ast_structural_check(tmp_path)
    assert res_safe.status == CheckStatus.PASSED

    # Dangerous code with eval
    (tmp_path / "unsafe.py").write_text("def run_dyn(user_input: str):\n    return eval(user_input)\n")
    res_unsafe = run_ast_structural_check(tmp_path)
    assert res_unsafe.status == CheckStatus.FAILED
    assert res_unsafe.exit_code == 1
    assert "eval" in res_unsafe.diagnostic


def test_import_resolution_pass(tmp_path):
    (tmp_path / "module.py").write_text("def calculate_sum(a, b):\n    return a + b\n")
    (tmp_path / "test_module.py").write_text("from module import calculate_sum\ndef test():\n    assert calculate_sum(1, 2) == 3\n")

    res = run_import_resolution_check(tmp_path)
    assert res.status == CheckStatus.PASSED


def test_import_resolution_fail_missing_symbol(tmp_path):
    (tmp_path / "module.py").write_text("def calculate_sum(a, b):\n    return a + b\n")
    # Trying to import non-existent 'multiply'
    (tmp_path / "test_module.py").write_text("from module import multiply\ndef test(): pass\n")

    res = run_import_resolution_check(tmp_path)
    assert res.status == CheckStatus.FAILED
    assert "Symbol 'multiply' not found in module 'module'" in res.diagnostic


def test_dependency_security_check(tmp_path):
    # Restricted network module (paramiko / telnetlib)
    (tmp_path / "restricted.py").write_text("import paramiko\ndef backdoor(): pass\n")
    res = run_dependency_security_check(tmp_path)
    assert res.status == CheckStatus.FAILED
    assert "paramiko" in res.diagnostic


# ============================================================================
# 3. Sandbox Restrictions & Mount Security
# ============================================================================

def test_sandbox_docker_socket_access_strictly_prohibited(tmp_path):
    """Sandbox security: Any attempt to mount host docker socket must be immediately blocked."""
    config = SandboxConfig()
    runner = SandboxRunner(config=config)

    fake_sock = tmp_path / "docker.sock"
    fake_sock.touch()

    res = runner.run_tool_check(fake_sock, "ruff --version", CheckName.RUFF)
    assert res.status == CheckStatus.FAILED
    assert res.exit_code == 126
    assert "host Docker socket access is strictly prohibited" in res.diagnostic


def test_sandbox_host_root_mount_blocked():
    """Sandbox security: Mounting root or critical host trees is prohibited."""
    config = SandboxConfig()
    runner = SandboxRunner(config=config)

    res = runner.run_tool_check(Path("/etc"), "pytest", CheckName.PYTEST)
    assert res.status == CheckStatus.FAILED
    assert res.exit_code == 126
    assert "critical host directory" in res.diagnostic


def test_sandbox_configuration_invariants():
    """Verifies that SandboxConfig enforces non-root, network isolation, tmpfs, and resource caps."""
    config = SandboxConfig()
    assert config.user == "10001:10001"
    assert config.network == "none"
    assert config.read_only_root is True
    assert "size=256m" in config.tmpfs_mount
    assert config.memory_limit == "512m"
    assert config.cpu_limit == "1.0"
    assert config.pids_limit == 100


# ============================================================================
# 4. Pipeline Execution & Diagnostic Propagation
# ============================================================================

def test_verification_pipeline_with_mock_runner(tmp_path):
    (tmp_path / "module.py").write_text("def solve():\n    return 42\n")
    (tmp_path / "test_module.py").write_text("from module import solve\ndef test_solve():\n    assert solve() == 42\n")

    runner = SandboxRunner()

    def mock_sandbox(wt, cmd, name):
        # All tools succeed
        return 0, f"{name} passed successfully", ""

    runner.set_mock_executor(mock_sandbox)
    pipeline = VerificationPipeline(sandbox_runner=runner)

    report = pipeline.run_all_checks(tmp_path, task_id="test-full-pipeline")
    assert report.is_passed() is True
    assert report.overall_status == "passed"
    assert len(report.checks) == 8
    for chk in [
        CheckName.SYNTAX.value,
        CheckName.AST_STRUCTURE.value,
        CheckName.IMPORT_RESOLUTION.value,
        CheckName.RUFF.value,
        CheckName.MYPY.value,
        CheckName.BANDIT.value,
        CheckName.PYTEST.value,
        CheckName.DEPENDENCY_SECURITY.value
    ]:
        assert chk in report.checks
        assert report.checks[chk].status == CheckStatus.PASSED


def test_verification_pipeline_failure_diagnostics(tmp_path):
    """Verifies that failure in pytest produces structured diagnostics for error_analyzer."""
    (tmp_path / "module.py").write_text("def solve():\n    return 0\n")
    (tmp_path / "test_module.py").write_text("from module import solve\ndef test_solve():\n    assert solve() == 42\n")

    runner = SandboxRunner()

    def mock_sandbox(wt, cmd, name):
        if name == CheckName.PYTEST.value:
            return 1, "FAILED test_module.py::test_solve - AssertionError: assert 0 == 42", ""
        return 0, "OK", ""

    runner.set_mock_executor(mock_sandbox)
    pipeline = VerificationPipeline(sandbox_runner=runner)

    report = pipeline.run_all_checks(tmp_path, task_id="test-fail-diag")
    assert report.is_passed() is False
    assert report.overall_status == "failed"

    diag = report.failure_diagnostics
    assert diag["failed_check"] == CheckName.PYTEST.value
    assert diag["category"] == "ASSERTION_FAILURE"
    assert "test_solve" in diag["failing_tests"]
    assert "assert 0 == 42" in diag["diagnostic"]


def test_sandbox_timeout_handling(tmp_path, monkeypatch):
    """Verifies that sandbox timeouts return exit code 124 and proper failure diagnostic."""
    runner = SandboxRunner(config=SandboxConfig(timeout_seconds=0.1))

    import subprocess
    def mock_subprocess_run(cmd, *args, **kwargs):
        if "kill" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0)
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=0.1)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    res = runner.run_tool_check(tmp_path, "sleep 10", CheckName.PYTEST)
    assert res.status == CheckStatus.FAILED
    assert res.exit_code == 124
    assert "timed out" in res.diagnostic
