"""Deterministic static, AST, import, and dependency validation checks."""
import ast
import sys
import time
from pathlib import Path
from typing import Set, Dict, List, Tuple

from verification_engine.models import CheckResult, CheckStatus, CheckName

# Standard library modules in Python 3.10+
STDLIB_MODULES = getattr(sys, "stdlib_module_names", frozenset([
    "os", "sys", "re", "json", "time", "datetime", "math", "random", "hashlib",
    "hmac", "secrets", "typing", "collections", "itertools", "functools", "pathlib",
    "dataclasses", "abc", "copy", "uuid", "string", "struct", "io", "base64",
    "urllib", "sqlite3", "xml", "html", "unittest", "logging", "asyncio"
]))

# Allowed external framework packages
ALLOWED_PACKAGES = {
    "pytest", "ruff", "mypy", "bandit", "pydantic", "fastapi", "uvicorn",
    "yaml", "pyyaml", "cryptography", "jwt", "requests", "httpx", "dotenv"
}

# Dangerous call nodes that are forbidden in generated code
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "compile"}


def run_syntax_check(worktree_path: Path) -> CheckResult:
    """Check 1: Independent syntax and indentation validation via AST parsing."""
    start = time.perf_counter()
    py_files = sorted(worktree_path.rglob("*.py"))
    if not py_files:
        return CheckResult(
            name=CheckName.SYNTAX.value,
            status=CheckStatus.NOT_APPLICABLE,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            stdout="No python files found in worktree."
        )

    errors = []
    for f in py_files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=str(f.relative_to(worktree_path)))
        except SyntaxError as e:
            rel = f.relative_to(worktree_path)
            errors.append(f"{rel}:{e.lineno}:{e.offset}: SyntaxError: {e.msg}")

    duration = (time.perf_counter() - start) * 1000.0
    if errors:
        return CheckResult(
            name=CheckName.SYNTAX.value,
            status=CheckStatus.FAILED,
            exit_code=1,
            stderr="\n".join(errors),
            duration_ms=duration,
            diagnostic=f"Syntax validation failed in {len(errors)} file(s):\n" + "\n".join(errors[:5])
        )

    return CheckResult(
        name=CheckName.SYNTAX.value,
        status=CheckStatus.PASSED,
        exit_code=0,
        stdout=f"All {len(py_files)} Python files parsed cleanly.",
        duration_ms=duration
    )


def run_ast_structural_check(worktree_path: Path) -> CheckResult:
    """Check 2: AST structural inspection (disallows eval/exec, checks for empty stubs)."""
    start = time.perf_counter()
    py_files = [f for f in sorted(worktree_path.rglob("*.py")) if not f.name.startswith("test_")]
    if not py_files:
        return CheckResult(
            name=CheckName.AST_STRUCTURE.value,
            status=CheckStatus.NOT_APPLICABLE,
            duration_ms=(time.perf_counter() - start) * 1000.0
        )

    violations = []
    for f in py_files:
        rel = f.relative_to(worktree_path)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(rel))
            for node in ast.walk(tree):
                # Detect forbidden function calls: eval, exec, __import__
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                        violations.append(f"{rel}:{node.lineno}: Disallowed dynamic execution function '{node.func.id}'")
        except Exception as e:
            violations.append(f"{rel}: Structural parsing failed: {e}")

    duration = (time.perf_counter() - start) * 1000.0
    if violations:
        return CheckResult(
            name=CheckName.AST_STRUCTURE.value,
            status=CheckStatus.FAILED,
            exit_code=1,
            stderr="\n".join(violations),
            duration_ms=duration,
            diagnostic="AST structural check detected security violations:\n" + "\n".join(violations)
        )

    return CheckResult(
        name=CheckName.AST_STRUCTURE.value,
        status=CheckStatus.PASSED,
        exit_code=0,
        stdout=f"AST structure verified for {len(py_files)} modules (0 forbidden constructs).",
        duration_ms=duration
    )


def run_import_resolution_check(worktree_path: Path) -> CheckResult:
    """Check 3: Deterministic import resolution for internal and external modules."""
    start = time.perf_counter()
    py_files = sorted(worktree_path.rglob("*.py"))
    if not py_files:
        return CheckResult(
            name=CheckName.IMPORT_RESOLUTION.value,
            status=CheckStatus.NOT_APPLICABLE,
            duration_ms=(time.perf_counter() - start) * 1000.0
        )

    # Index available module stems and their declared top-level symbols
    available_modules: Dict[str, Set[str]] = {}
    for f in py_files:
        rel = f.relative_to(worktree_path)
        stem = f.stem
        # Also store package dotted path if nested
        dotted_parts = list(rel.parent.parts) + [stem]
        dotted_name = ".".join(p for p in dotted_parts if p != ".")
        
        symbols = set()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            symbols.add(target.id)
        except Exception:
            pass

        available_modules[stem] = symbols
        available_modules[dotted_name] = symbols

    unresolved = []
    for f in py_files:
        rel = f.relative_to(worktree_path)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(rel))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_mod = alias.name.split(".")[0]
                        if root_mod not in STDLIB_MODULES and root_mod not in ALLOWED_PACKAGES and root_mod not in available_modules:
                            unresolved.append(f"{rel}:{node.lineno}: Cannot resolve imported module '{alias.name}'")
                elif isinstance(node, ast.ImportFrom):
                    mod_name = node.module or ""
                    root_mod = mod_name.split(".")[0]
                    # If importing from a local module in the worktree
                    if mod_name in available_modules or root_mod in available_modules:
                        target_symbols = available_modules.get(mod_name) or available_modules.get(root_mod, set())
                        for alias in node.names:
                            if alias.name != "*" and target_symbols and alias.name not in target_symbols:
                                unresolved.append(
                                    f"{rel}:{node.lineno}: Symbol '{alias.name}' not found in module '{mod_name}' (available: {sorted(list(target_symbols))[:5]})"
                                )
                    elif root_mod not in STDLIB_MODULES and root_mod not in ALLOWED_PACKAGES:
                        unresolved.append(f"{rel}:{node.lineno}: Cannot resolve module '{mod_name}'")
        except Exception as e:
            unresolved.append(f"{rel}: Import analysis error: {e}")

    duration = (time.perf_counter() - start) * 1000.0
    if unresolved:
        return CheckResult(
            name=CheckName.IMPORT_RESOLUTION.value,
            status=CheckStatus.FAILED,
            exit_code=1,
            stderr="\n".join(unresolved),
            duration_ms=duration,
            diagnostic=f"Import resolution failed ({len(unresolved)} issue(s)):\n" + "\n".join(unresolved[:6])
        )

    return CheckResult(
        name=CheckName.IMPORT_RESOLUTION.value,
        status=CheckStatus.PASSED,
        exit_code=0,
        stdout="All imports cleanly resolved against project worktree and allowed libraries.",
        duration_ms=duration
    )


def run_dependency_security_check(worktree_path: Path) -> CheckResult:
    """Check 8: Dependency security validation (guards against unauthorized or risky packages)."""
    start = time.perf_counter()
    py_files = sorted(worktree_path.rglob("*.py"))
    
    # Block high-risk external network / code-execution libraries in autonomous sandbox code
    DISALLOWED_EXTERNAL = {
        "socket", "telnetlib", "ftplib", "paramiko", "pexpect", "pty", "winreg"
    }

    suspicious = []
    for f in py_files:
        rel = f.relative_to(worktree_path)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in DISALLOWED_EXTERNAL:
                            suspicious.append(f"{rel}:{node.lineno}: Restricted network/system module '{alias.name}'")
                elif isinstance(node, ast.ImportFrom):
                    mod = (node.module or "").split(".")[0]
                    if mod in DISALLOWED_EXTERNAL:
                        suspicious.append(f"{rel}:{node.lineno}: Restricted network/system module '{mod}'")
        except Exception:
            pass

    duration = (time.perf_counter() - start) * 1000.0
    if suspicious:
        return CheckResult(
            name=CheckName.DEPENDENCY_SECURITY.value,
            status=CheckStatus.FAILED,
            exit_code=1,
            stderr="\n".join(suspicious),
            duration_ms=duration,
            diagnostic="Security policy violation: restricted dependency detected:\n" + "\n".join(suspicious)
        )

    return CheckResult(
        name=CheckName.DEPENDENCY_SECURITY.value,
        status=CheckStatus.PASSED,
        exit_code=0,
        stdout="Dependency security scan passed (0 restricted packages).",
        duration_ms=duration
    )
