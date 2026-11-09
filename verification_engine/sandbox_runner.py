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
    """Configuration and security limits for Docker sandbox execution.
    
    Security Architecture Notes:
    1. Container UID:GID: Executing as non-root (UID 10001, GID 10001) prevents in-container
       privilege escalation.
    2. Host Daemon Distinction: Non-root container execution does NOT by itself prove or establish
       that the host Docker daemon is rootless. If dockerd runs as host root, defense-in-depth relies
       on dropped capabilities (--cap-drop ALL), no-new-privileges, network isolation, and read-only rootfs.
       For strict multi-tenant isolation, rootless dockerd or gVisor (runsc) is required.
    3. Executable tmpfs: /tmp is mounted with 'exec' because the container rootfs is read-only (/app:ro),
       and Python bytecode execution (.pyc) and test execution require an isolated writable scratchpad.
    """
    image: str = "hydrameda-verifier:latest"
    user: str = "10001:10001"
    network: str = "none"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    pids_limit: int = 100
    read_only_root: bool = True
    tmpfs_mount: str = "/tmp:size=256m,mode=1777,exec"
    timeout_seconds: float = 60.0
    seccomp_profile: Optional[str] = None
    allowed_mounts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes sandbox configuration for audit logs and verification records."""
        return {
            "image": self.image,
            "user": self.user,
            "network": self.network,
            "memory_limit": self.memory_limit,
            "cpu_limit": self.cpu_limit,
            "pids_limit": self.pids_limit,
            "read_only_root": self.read_only_root,
            "tmpfs_mount": self.tmpfs_mount,
            "timeout_seconds": self.timeout_seconds,
            "seccomp_profile": self.seccomp_profile,
            "security_options": ["no-new-privileges", "cap-drop:ALL"]
        }

    def validate_mounts(self, target_mount: Path) -> None:
        """Enforces mount safety: rejects docker socket, critical host trees, and sensitive credentials."""
        mount_str = str(target_mount.resolve()).lower()
        if "docker.sock" in mount_str:
            raise SecurityError("Forbidden mount: host Docker socket access is strictly prohibited.")
        if mount_str in ("/", "/etc", "/var", "/usr", "/bin", "/root", "/proc", "/sys", "/run", "/dev"):
            raise SecurityError(f"Forbidden mount: root or critical host directory '{mount_str}' cannot be mounted.")
        
        # Requirement A6.5: Prevent host credential exposure (.env, .git/credentials, .ssh, .aws, .netrc)
        cred_names = [".env", ".git/credentials", ".ssh", ".aws", ".netrc", "id_rsa", "id_ed25519"]
        for cred in cred_names:
            cred_path = target_mount / cred
            if cred_path.exists():
                raise SecurityError(f"Forbidden mount: worktree exposes sensitive credential path '{cred}'.")



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
        ]

        if self.config.seccomp_profile:
            cmd.extend(["--security-opt", f"seccomp={self.config.seccomp_profile}"])

        cmd.extend([
            "-v", f"{wt}:/app:ro",
            self.config.image,
            full_script
        ])

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
            # Enforce clean container termination and complete removal on timeout
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, check=False)
            except Exception:
                pass
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
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
            # Enforce container cleanup on unexpected exceptions as well
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            try:
                subprocess.run(["docker", "kill", container_name], capture_output=True, check=False)
            except Exception:
                pass
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
            except Exception:
                pass
            return CheckResult(
                name=name_str,
                status=CheckStatus.FAILED,
                exit_code=1,
                stderr=str(e),
                duration_ms=duration_ms,
                diagnostic=f"Sandbox execution error: {e}"
            )

    def get_host_isolation_metadata(self) -> Dict[str, Any]:
        """Clearly distinguishes container non-root execution from rootless Docker host execution.
        
        Requirement A6.9:
        - Container non-root: execution inside container is mapped to non-root UID 10001:10001.
        - Host rootless: whether the host Docker daemon itself is rootless or standard dockerd.
        """
        is_rootless_daemon = False
        daemon_security = []
        try:
            info_proc = subprocess.run(
                ["docker", "info", "--format", "{{json .SecurityOptions}}"],
                capture_output=True, text=True, timeout=5
            )
            if info_proc.returncode == 0 and info_proc.stdout.strip():
                daemon_security = json.loads(info_proc.stdout.strip())
                is_rootless_daemon = any("rootless" in s.lower() for s in daemon_security)
        except Exception:
            pass

        return {
            "container_user": self.config.user,
            "container_non_root": self.config.user != "0:0" and "0:" not in self.config.user,
            "host_daemon_rootless": is_rootless_daemon,
            "daemon_security_options": daemon_security,
            "capabilities_dropped": "ALL",
            "network_isolation": self.config.network == "none",
            "read_only_rootfs": self.config.read_only_root
        }

