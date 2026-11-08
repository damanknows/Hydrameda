"""Secure isolated sandbox execution runner."""
import os
import re
import time
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable

from verification_engine.models import CheckResult, CheckStatus, CheckName


@dataclass
class SandboxConfig:
    """Configuration and security limits for Docker sandbox execution."""
    image: str = "hydrameda-verifier:latest"
    user: str = "10001:10001"
    network: str = "none"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    pids_limit: int = 100
    read_only_root: bool = True
    tmpfs_mount: str = "/tmp:size=256m,exec"
    timeout_seconds: float = 60.0
    allowed_mounts: List[str] = field(default_factory=list)

    def validate_mounts(self, target_mount: Path) -> None:
        """Enforces mount safety: rejects docker socket and unnecessary host trees."""
        mount_str = str(target_mount.resolve()).lower()
        if "docker.sock" in mount_str:
            raise SecurityError("Forbidden mount: host Docker socket access is strictly prohibited.")
        if mount_str in ("/", "/etc", "/var", "/usr", "/bin", "/root", "/proc", "/sys"):
            raise SecurityError(f"Forbidden mount: root or critical host directory '{mount_str}' cannot be mounted.")


class SecurityError(Exception):
    """Raised when a sandbox security constraint is violated."""
    pass


class SandboxRunner:
    """Executes commands inside a secure, rootless, network-isolated container."""

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._mock_executor: Optional[Callable[..., Tuple[int, str, str]]] = None

    def set_mock_executor(self, executor: Optional[Callable[..., Any]]) -> None:
        """Sets a mock executor hook for isolated testing."""
        self._mock_executor = executor

    def run_tool_check(
        self,
        worktree_path: Path,
        command: str,
        check_name: CheckName | str,
        custom_timeout: Optional[float] = None
    ) -> CheckResult:
        """Runs a tool command (Ruff, Mypy, Bandit, Pytest) inside the secure sandbox."""
        name_str = check_name.value if isinstance(check_name, CheckName) else str(check_name)
        wt = worktree_path.resolve()

        if not wt.exists():
            return CheckResult(
                name=name_str,
                status=CheckStatus.FAILED,
                exit_code=1,
                stderr=f"Worktree path does not exist: {wt}",
                diagnostic=f"Worktree path not found: {wt}"
            )

        # Enforce mount security validation
        try:
            self.config.validate_mounts(wt)
        except SecurityError as sec_err:
            return CheckResult(
                name=name_str,
                status=CheckStatus.FAILED,
                exit_code=126,
                stderr=str(sec_err),
                diagnostic=f"Sandbox security violation: {sec_err}"
            )

        timeout = custom_timeout or self.config.timeout_seconds
        start_t = time.perf_counter()

        # Handle mock executor hook if active
        if self._mock_executor is not None:
            try:
                exit_code, stdout, stderr = self._mock_executor(wt, command, name_str)
                duration_ms = (time.perf_counter() - start_t) * 1000.0
                status = CheckStatus.PASSED if exit_code == 0 else CheckStatus.FAILED
                diag = None if exit_code == 0 else (stderr or stdout)
                return CheckResult(
                    name=name_str,
                    status=status,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=duration_ms,
                    diagnostic=diag
                )
            except Exception as e:
                duration_ms = (time.perf_counter() - start_t) * 1000.0
                return CheckResult(
                    name=name_str,
                    status=CheckStatus.FAILED,
                    exit_code=1,
                    stderr=str(e),
                    duration_ms=duration_ms,
                    diagnostic=f"Mock execution error: {e}"
                )

        # Construct ephemeral container script
        # Copies worktree to isolated tmpfs /tmp/work and executes with cache dirs in tmpfs
        full_script = (
            "cp -r /app /tmp/work && "
            "export PYTHONPATH=/tmp/work && "
            "cd /tmp/work && "
            f"{command}"
        )

        container_name = f"hydrameda-verify-{int(time.time()*1000)%1000000}"
        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", self.config.network,
            "--cap-drop", "ALL",
            "--read-only" if self.config.read_only_root else "--read-only=false",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self.config.pids_limit),
            "--user", self.config.user,
            "--memory", self.config.memory_limit,
            "--cpus", self.config.cpu_limit,
            "--tmpfs", self.config.tmpfs_mount,
            "-v", f"{wt}:/app:ro",
            self.config.image,
            full_script
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            status = CheckStatus.PASSED if proc.returncode == 0 else CheckStatus.FAILED

            diag = None
            if proc.returncode != 0:
                combined = (proc.stdout + "\n" + proc.stderr).strip()
                diag = "\n".join(combined.splitlines()[-40:]) if combined else f"Exit code {proc.returncode}"

            return CheckResult(
                name=name_str,
                status=status,
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
                diagnostic=diag
            )

        except subprocess.TimeoutExpired:
            # Enforce clean container termination on timeout
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, check=False)
            except Exception:
                pass
            return CheckResult(
                name=name_str,
                status=CheckStatus.FAILED,
                exit_code=124,
                stderr=f"Execution timed out after {timeout} seconds",
                duration_ms=duration_ms,
                diagnostic=f"Sandbox execution timed out ({timeout}s limit exceeded)"
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            return CheckResult(
                name=name_str,
                status=CheckStatus.FAILED,
                exit_code=1,
                stderr=str(e),
                duration_ms=duration_ms,
                diagnostic=f"Sandbox execution error: {e}"
            )
