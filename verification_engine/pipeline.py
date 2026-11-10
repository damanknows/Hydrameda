"""Independent Verification Pipeline executing the 8 required deterministic checks."""
import re
import time
from pathlib import Path

from verification_engine.models import CheckName, CheckResult, CheckStatus, VerificationReport
from verification_engine.sandbox_runner import SandboxRunner
from verification_engine.static_checks import (
    run_ast_structural_check,
    run_dependency_security_check,
    run_import_resolution_check,
    run_syntax_check,
)


class VerificationPipeline:
    """Orchestrates independent, deterministic verification across all 8 checks."""

    def __init__(self, sandbox_runner: SandboxRunner | None = None):
        self.runner = sandbox_runner or SandboxRunner()

    def run_all_checks(
        self,
        worktree_path: Path,
        task_id: str = "task-verify",
        target_files: list[str] | None = None,
        fail_fast: bool = False
    ) -> VerificationReport:
        """Executes all 8 deterministic verification checks in strict order."""
        total_start = time.perf_counter()
        checks: dict[str, CheckResult] = {}
        wt = Path(worktree_path).resolve()

        # Phase 1: Local Static & AST Checks
        # 1. Syntax Validation
        res_syntax = run_syntax_check(wt, target_files=target_files)
        checks[CheckName.SYNTAX.value] = res_syntax
        if fail_fast and res_syntax.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # 2. AST & Structural Validation
        res_ast = run_ast_structural_check(wt, target_files=target_files)
        checks[CheckName.AST_STRUCTURE.value] = res_ast
        if fail_fast and res_ast.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # 3. Import Resolution
        res_import = run_import_resolution_check(wt, target_files=target_files)
        checks[CheckName.IMPORT_RESOLUTION.value] = res_import
        if fail_fast and res_import.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # Format scoped target arguments if target_files provided
        target_args = ""
        if target_files:
            target_args = " ".join(f"/tmp/work/{Path(t).as_posix()}" for t in target_files)

        # Phase 2: Secure Sandbox Static Analysis & Security Scans
        # 4. Ruff
        ruff_target = target_args if target_args else "/tmp/work"
        ruff_cmd = f"ruff check --select E9,F63,F7,F82 --cache-dir /tmp/ruff_cache {ruff_target}"
        res_ruff = self.runner.run_tool_check(wt, ruff_cmd, CheckName.RUFF)
        checks[CheckName.RUFF.value] = res_ruff
        if fail_fast and res_ruff.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # 5. Mypy
        mypy_target = target_args if target_args else "/tmp/work"
        mypy_cmd = f"mypy --ignore-missing-imports --cache-dir /tmp/mypy_cache {mypy_target}"
        res_mypy = self.runner.run_tool_check(wt, mypy_cmd, CheckName.MYPY)
        checks[CheckName.MYPY.value] = res_mypy
        if fail_fast and res_mypy.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # 6. Bandit SAST Security Scan
        non_test_targets = [
            f"/tmp/work/{Path(t).as_posix()}" for t in (target_files or [])
            if not Path(t).name.startswith("test_") and not Path(t).name.endswith("_test.py")
        ]
        if target_files and not non_test_targets:
            res_bandit = CheckResult(
                name=CheckName.BANDIT.value,
                status=CheckStatus.PASSED,
                exit_code=0,
                stdout="Bandit skipped for test-only targets.",
                duration_ms=0.0
            )
        else:
            bandit_target = " ".join(non_test_targets) if non_test_targets else "-r /tmp/work -x '*/test_*.py,*/*_test.py'"
            bandit_cmd = (
                "if command -v bandit >/dev/null 2>&1; then "
                f"bandit -q {bandit_target} -s B101,B105,B106,B107; "
                "else echo 'Bandit not installed'; exit 1; fi"
            )
            res_bandit = self.runner.run_tool_check(wt, bandit_cmd, CheckName.BANDIT)
        checks[CheckName.BANDIT.value] = res_bandit
        if fail_fast and res_bandit.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # Phase 3: Sandboxed Execution & Testing
        # 7. Pytest
        test_targets = [
            f"/tmp/work/{Path(t).as_posix()}" for t in (target_files or [])
            if Path(t).name.startswith("test_") or Path(t).name.endswith("_test.py")
        ]
        pytest_target = " ".join(test_targets) if test_targets else "/tmp/work"
        pytest_cmd = f"pytest -v --tb=short -o cache_dir=/tmp/pytest_cache {pytest_target}"
        res_pytest = self.runner.run_tool_check(wt, pytest_cmd, CheckName.PYTEST)
        checks[CheckName.PYTEST.value] = res_pytest
        if fail_fast and res_pytest.status == CheckStatus.FAILED:
            return self._build_report(task_id, checks, total_start)

        # 8. Dependency / Security Checks
        res_dep = run_dependency_security_check(wt, target_files=target_files)
        checks[CheckName.DEPENDENCY_SECURITY.value] = res_dep

        return self._build_report(task_id, checks, total_start)

    def _build_report(
        self,
        task_id: str,
        checks: dict[str, CheckResult],
        total_start: float
    ) -> VerificationReport:
        total_duration = (time.perf_counter() - total_start) * 1000.0

        failed_checks = [k for k, v in checks.items() if v.status == CheckStatus.FAILED]
        skipped_checks = [k for k, v in checks.items() if v.status == CheckStatus.SKIPPED]

        # Strict Rule: Do not treat skipped checks as successful.
        # Pass requires 0 failed and 0 skipped checks among executed checks.
        is_passed = (len(failed_checks) == 0 and len(skipped_checks) == 0 and len(checks) > 0)
        overall_status = "passed" if is_passed else "failed"

        summary_lines = [
            f"# Verification Report for Task: {task_id}",
            f"- Overall Status: **{overall_status.upper()}**",
            f"- Total Duration: {total_duration:.2f} ms",
            "## Check Breakdown:"
        ]
        for name, res in checks.items():
            icon = {
                CheckStatus.PASSED: "✅",
                CheckStatus.FAILED: "❌",
                CheckStatus.SKIPPED: "⏭️",
                CheckStatus.NOT_APPLICABLE: "⚪"
            }.get(res.status, "•")
            summary_lines.append(f"  {icon} **{name}**: {res.status.value.upper()} ({res.duration_ms:.1f} ms)")

        summary = "\n".join(summary_lines)

        # Synthesize actionable failure diagnostics
        diagnostics = {}
        if not is_passed:
            primary_fail_name = failed_checks[0] if failed_checks else (skipped_checks[0] if skipped_checks else "unknown")
            primary_res = checks.get(primary_fail_name)
            raw_diag = primary_res.diagnostic if primary_res else "Verification failed"

            # Categorize failure for error_analyzer and repair_agent
            category_map = {
                CheckName.SYNTAX.value: "SYNTAX_ERROR",
                CheckName.AST_STRUCTURE.value: "SECURITY_ERROR",
                CheckName.IMPORT_RESOLUTION.value: "IMPORT_ERROR",
                CheckName.RUFF.value: "SYNTAX_ERROR",
                CheckName.MYPY.value: "TYPE_ERROR",
                CheckName.BANDIT.value: "SECURITY_ERROR",
                CheckName.PYTEST.value: "ASSERTION_FAILURE",
                CheckName.DEPENDENCY_SECURITY.value: "SECURITY_ERROR"
            }
            cat = category_map.get(primary_fail_name, "RUNTIME_ERROR")

            # Extract failing pytest test function names if applicable
            failing_tests = []
            if primary_fail_name == CheckName.PYTEST.value and primary_res:
                failing_tests = re.findall(r"FAILED\s+[^\s:]+::([a-zA-Z0-9_]+)", primary_res.stdout or primary_res.stderr)

            guidance_map = {
                "SYNTAX_ERROR": "Fix syntax/indentation errors. Validate proper colon usage and matching parentheses.",
                "IMPORT_ERROR": "Ensure all imported symbols exist in module.py and are exported. Use standard library modules only.",
                "TYPE_ERROR": "Ensure return types and parameter annotations match mypy strict checking.",
                "SECURITY_ERROR": "Eliminate forbidden functions (eval/exec) and hardcoded secrets.",
                "ASSERTION_FAILURE": "Carefully trace logic and boundary conditions asserted in the failing unit tests.",
                "RUNTIME_ERROR": "Inspect traceback carefully and eliminate unexpected exceptions."
            }

            diagnostics = {
                "failed_check": primary_fail_name,
                "category": cat,
                "diagnostic": raw_diag,
                "failing_tests": failing_tests,
                "actionable_guidance": guidance_map.get(cat, "Resolve verification errors while preserving working code."),
                "failed_checks_list": failed_checks,
                "skipped_checks_list": skipped_checks
            }

        return VerificationReport(
            task_id=task_id,
            overall_status=overall_status,
            checks=checks,
            total_duration_ms=total_duration,
            summary=summary,
            failure_diagnostics=diagnostics,
            sandbox_config=self.runner.config.to_dict()
        )

