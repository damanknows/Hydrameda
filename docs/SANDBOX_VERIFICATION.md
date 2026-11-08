# Hydrameda Independent Verification and Secure Sandbox

The **Verification Engine** (`verification_engine/`) provides deterministic, multi-phase verification of all generated code within hardened, unprivileged container sandboxes. It prevents hallucinated passes, enforces zero network access, isolates filesystems, and emits structured diagnostic reports to the self-healing repair loop.

---

## 1. Verification Architecture

```mermaid
flowchart TD
    WT[Isolated Git Worktree: /worktrees/{task_id}] --> P1
    
    subgraph Phase1["Phase 1: Deterministic Static Checks"]
        P1[1. Syntax Validation: ast.parse] --> P2[2. AST Structural: disallow eval/exec]
        P2 --> P3[3. Import Resolution: symbol & module mapping]
    end

    subgraph Phase2["Phase 2: Hardened Sandbox Tools (Docker / Rootless)"]
        P3 --> S1[4. Ruff Check: fatal syntax & lint]
        S1 --> S2[5. Mypy: strict type consistency]
        S2 --> S3[6. Bandit: SAST security scan]
        S3 --> S4[7. Pytest: unit test execution]
    end

    subgraph Phase3["Phase 3: Dependency Security"]
        S4 --> D1[8. Dependency & Network Security Check]
    end

    D1 --> Report[VerificationReport]
    Report -->|All 8 Passed| Pass[PASSED: Commit & Consolidate]
    Report -->|Any Failed or Skipped| Fail[FAILED: Diagnostics to Error Analyzer & Repair Agent]
```

---

## 2. The 8 Required Deterministic Checks

Every verification run executes 8 checks in order. Results are represented using explicit `CheckStatus` enums:
`passed` | `failed` | `skipped` | `not_applicable`

> [!IMPORTANT]
> **Strict Verification Invariant**: Skipped checks are **NEVER** treated as successful. If a check is skipped, the overall status is marked `failed`. A pipeline run passes if and only if all applicable checks pass with 0 failures and 0 skipped checks.

| # | Check Name | Implementation | Enforced Criteria |
|---|---|---|---|
| 1 | `syntax_validation` | `run_syntax_check` | Full AST compilation; rejects any `SyntaxError` or `IndentationError` with line and offset. |
| 2 | `ast_structural_validation` | `run_ast_structural_check` | AST tree walk; strictly disallows dynamic code execution (`eval`, `exec`, `compile`, `__import__`). |
| 3 | `import_resolution` | `run_import_resolution_check` | Extracts all `import` / `from ... import` statements; verifies target module and declared symbol exist in worktree or standard library. |
| 4 | `ruff` | Sandbox Runner (`ruff check`) | Fatal lint and syntax error check (`--select E9,F63,F7,F82`). |
| 5 | `mypy` | Sandbox Runner (`mypy`) | Static type checking (`--ignore-missing-imports`). |
| 6 | `bandit` | Sandbox Runner (`bandit`) | SAST vulnerability scan across all project files (`-s B101,B105,B106,B107` excluding test files). |
| 7 | `pytest` | Sandbox Runner (`pytest`) | Unit test suite execution (`-v --tb=short`). |
| 8 | `dependency_security` | `run_dependency_security_check` | Prohibits unauthorized high-risk network/system packages (`socket`, `paramiko`, `telnetlib`, `pty`). |

---

## 3. Sandbox Security Specifications

The container sandbox (`SandboxRunner`) enforces the following security boundaries:

```bash
docker run --rm \
    --network none \
    --cap-drop ALL \
    --read-only \
    --security-opt no-new-privileges \
    --pids-limit 100 \
    --user 10001:10001 \
    --memory 512m \
    --cpus 1.0 \
    --tmpfs /tmp:size=256m,exec \
    -v /path/to/worktree:/app:ro \
    hydrameda-verifier:latest
```

1. **Non-Root Execution**: Runs strictly as unprivileged `sandboxuser` (`UID 10001:10001`).
2. **Network Isolation**: `--network none` prohibits all inbound and outbound network access.
3. **Dropped Capabilities**: `--cap-drop ALL` strips all Linux capabilities.
4. **No-New-Privileges**: `--security-opt no-new-privileges` blocks `setuid` privilege escalation.
5. **Read-Only Root Filesystem**: Root filesystem is mounted `--read-only`.
6. **Restricted Ephemeral Workspace**: Ephemeral memory filesystem (`--tmpfs /tmp:size=256m,exec`) holds temporary execution artifacts; the host worktree is mounted strictly read-only (`-v <worktree>:/app:ro`).
7. **Mount Security Protection**: Any mount referencing `/var/run/docker.sock` or host root directories (`/`, `/etc`, `/var`, `/usr`, `/root`) is rejected immediately with a `SecurityError` (exit code 126).
8. **Resource Caps**: Enforces `--memory 512m`, `--cpus 1.0`, and `--pids-limit 100` (mitigating fork bombs).
9. **Execution Timeout**: Subprocess runs with an execution deadline (default 60s); timeouts issue an explicit `docker kill` and record exit code 124.
10. **Automated Cleanup**: `--rm` guarantees immediate container destruction upon termination.

---

## 4. Structured Verification Reports & Diagnostic Feedback

When verification fails, a structured `VerificationReport` is generated and returned to `node_error_analyzer` and `node_repair_agent`:

```json
{
  "task_id": "task-math-utils",
  "overall_status": "failed",
  "total_duration_ms": 142.5,
  "checks": {
    "syntax_validation": { "status": "passed", "duration_ms": 8.2 },
    "ast_structural_validation": { "status": "passed", "duration_ms": 6.1 },
    "import_resolution": { "status": "passed", "duration_ms": 10.4 },
    "ruff": { "status": "passed", "duration_ms": 35.0 },
    "mypy": { "status": "passed", "duration_ms": 42.1 },
    "bandit": { "status": "passed", "duration_ms": 18.2 },
    "pytest": {
      "status": "failed",
      "exit_code": 1,
      "duration_ms": 22.5,
      "diagnostic": "FAILED test_module.py::test_edge_case - AssertionError: assert -1 == 0"
    },
    "dependency_security": { "status": "passed", "duration_ms": 5.0 }
  },
  "failure_diagnostics": {
    "failed_check": "pytest",
    "category": "ASSERTION_FAILURE",
    "diagnostic": "FAILED test_module.py::test_edge_case - AssertionError: assert -1 == 0",
    "failing_tests": ["test_edge_case"],
    "actionable_guidance": "Carefully trace logic and boundary conditions asserted in the failing unit tests."
  }
}
```

The repair loop is hard-capped at **three repair attempts** (`repair_count <= 3`), preventing infinite repair cycles.
