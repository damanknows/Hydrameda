"""Deterministic static, AST, import, and dependency validation checks."""
from __future__ import annotations

import ast
import logging
import re
import sys
import time
from pathlib import Path

from verification_engine.models import CheckName, CheckResult, CheckStatus

logger = logging.getLogger(__name__)

# Standard library modules in Python 3.10+
STDLIB_MODULES = getattr(sys, "stdlib_module_names", frozenset([
    "os", "sys", "re", "json", "time", "datetime", "math", "random", "hashlib",
    "hmac", "secrets", "typing", "collections", "itertools", "functools", "pathlib",
    "dataclasses", "abc", "copy", "uuid", "string", "struct", "io", "base64",
    "urllib", "sqlite3", "xml", "html", "unittest", "logging", "asyncio"
]))

# Allowed external framework packages baseline
ALLOWED_PACKAGES = {
    "pytest", "ruff", "mypy", "bandit", "pydantic", "fastapi", "uvicorn",
    "yaml", "pyyaml", "cryptography", "jwt", "requests", "httpx", "dotenv"
}

# Directories to exclude from indexing or baseline scanning to prevent cross-talk
IGNORED_DIRS = frozenset({
    ".git", ".venv", "venv", "env", "worktrees", "exported_modules",
    "benchmarks", "n8n_data", "__pycache__", ".fastembed_cache",
    "lancedb_data", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".cache", "node_modules", "dist", "build"
})

# Dangerous call nodes that are forbidden in generated code
FORBIDDEN_CALLS = {"eval", "exec", "__import__", "compile"}


def get_worktree_py_files(worktree_path: Path) -> list[Path]:
    """Discovers all Python files in the worktree, strictly excluding ignored and cache directories."""
    py_files = []
    for p in sorted(worktree_path.rglob("*.py")):
        rel_parts = p.relative_to(worktree_path).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        py_files.append(p)
    return py_files


def get_configured_dependencies(worktree_path: Path) -> set[str]:
    """Dynamically resolves configured and installed dependencies for the target workspace."""
    deps = set(ALLOWED_PACKAGES)

    # 1. Inspect requirements.txt candidates in worktree and parent repository
    candidates = [
        worktree_path / "requirements.txt",
        worktree_path.parent / "requirements.txt",
        worktree_path.parent.parent / "requirements.txt"
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                    clean_line = line.strip().split("#")[0].split(";")[0].strip()
                    if clean_line and not clean_line.startswith("-"):
                        pkg = re.split(r"[=<>~!]", clean_line)[0].strip().lower().replace("-", "_")
                        if pkg:
                            deps.add(pkg)
            except (OSError, ValueError) as e:
                logger.debug("Failed parsing requirements file %s: %s", candidate, e)

    # 2. Query environment-installed distributions
    try:
        from importlib.metadata import packages_distributions
        dists = packages_distributions()
        deps.update(dists.keys())
    except (ImportError, OSError, TypeError) as e:
        logger.debug("packages_distributions discovery failed: %s", e)

    return deps


def _filter_by_targets(
    all_py: list[Path],
    worktree_path: Path,
    target_files: list[str] | None = None,
    exclude_tests: bool = False
) -> list[Path]:
    """Filters Python files based on target_files scope and test inclusion rules."""
    if target_files:
        norm_targets = set()
        for t in target_files:
            tp = Path(t)
            norm_targets.add(tp.name)
            try:
                if tp.is_absolute():
                    norm_targets.add(str(tp.relative_to(worktree_path)))
                else:
                    norm_targets.add(str(tp))
            except ValueError:
                pass

        files = [
            f for f in all_py
            if f.name in norm_targets or str(f.relative_to(worktree_path)) in norm_targets
        ]
    else:
        files = all_py

    if exclude_tests:
        files = [f for f in files if not f.name.startswith("test_") and not f.name.endswith("_test.py")]

    return files


def run_syntax_check(worktree_path: Path, target_files: list[str] | None = None) -> CheckResult:
    """Check 1: Independent syntax and indentation validation via AST parsing."""
    start = time.perf_counter()
    all_py = get_worktree_py_files(worktree_path)
    py_files = _filter_by_targets(all_py, worktree_path, target_files=target_files)

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


def run_ast_structural_check(worktree_path: Path, target_files: list[str] | None = None) -> CheckResult:
    """Check 2: AST structural inspection (disallows eval/exec, checks for forbidden constructs)."""
    start = time.perf_counter()
    all_py = get_worktree_py_files(worktree_path)
    py_files = _filter_by_targets(all_py, worktree_path, target_files=target_files, exclude_tests=True)

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
                # Detect forbidden function calls: eval, exec, __import__, compile
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in FORBIDDEN_CALLS
                ):
                    violations.append(f"{rel}:{node.lineno}: Disallowed dynamic execution function '{node.func.id}'")
        except (SyntaxError, ValueError) as e:
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


def run_import_resolution_check(worktree_path: Path, target_files: list[str] | None = None) -> CheckResult:
    """Check 3: Deterministic import resolution for internal and external modules with package indexing."""
    start = time.perf_counter()
    all_py = get_worktree_py_files(worktree_path)
    if not all_py:
        return CheckResult(
            name=CheckName.IMPORT_RESOLUTION.value,
            status=CheckStatus.NOT_APPLICABLE,
            duration_ms=(time.perf_counter() - start) * 1000.0
        )

    configured_deps = get_configured_dependencies(worktree_path)

    # Index available module stems, dotted package paths, and declared top-level symbols
    available_modules: dict[str, set[str]] = {}
    for f in all_py:
        rel = f.relative_to(worktree_path)
        stem = f.stem
        dotted_parts = list(rel.parent.parts) + [stem]
        dotted_name = ".".join(p for p in dotted_parts if p != ".")

        symbols: set[str] = set()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            symbols.add(target.id)
                            if target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                                for elt in node.value.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        symbols.add(elt.value)
                        elif isinstance(target, (ast.Tuple, ast.List)):
                            for elt in target.elts:
                                if isinstance(elt, ast.Name):
                                    symbols.add(elt.id)
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name):
                        symbols.add(node.target.id)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        symbols.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        symbols.add(alias.asname or alias.name)
        except (SyntaxError, ValueError) as exc:
            logger.debug("AST parse failed for %s: %s", f, exc)

        available_modules[stem] = symbols
        available_modules[dotted_name] = symbols

        # If __init__.py, also register the package name and its submodule children
        if f.name == "__init__.py":
            pkg_rel = rel.parent
            pkg_dotted = ".".join(pkg_rel.parts)
            if pkg_dotted:
                try:
                    for child in f.parent.iterdir():
                        if child.is_file() and child.suffix == ".py" and child.stem != "__init__":
                            symbols.add(child.stem)
                        elif child.is_dir() and (child / "__init__.py").exists():
                            symbols.add(child.name)
                except OSError:
                    pass
                available_modules[pkg_dotted] = symbols
                if len(pkg_rel.parts) == 1:
                    available_modules[pkg_rel.parts[0]] = symbols

    files_to_check = _filter_by_targets(all_py, worktree_path, target_files=target_files)
    if not files_to_check:
        return CheckResult(
            name=CheckName.IMPORT_RESOLUTION.value,
            status=CheckStatus.NOT_APPLICABLE,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            stdout="No scoped target files to check for imports."
        )

    unresolved = []
    for f in files_to_check:
        rel = f.relative_to(worktree_path)
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(rel))
            for item_node in ast.walk(tree):
                if isinstance(item_node, ast.Import):
                    for alias in item_node.names:
                        root_mod = alias.name.split(".")[0]
                        if (
                            root_mod not in STDLIB_MODULES
                            and root_mod not in configured_deps
                            and root_mod not in available_modules
                            and alias.name not in available_modules
                        ):
                            unresolved.append(f"{rel}:{item_node.lineno}: Cannot resolve imported module '{alias.name}'")
                elif isinstance(item_node, ast.ImportFrom):
                    mod_name = item_node.module or ""
                    level = getattr(item_node, "level", 0)
                    if level > 0:
                        # Resolve relative import relative to rel.parent
                        pkg_parts = list(rel.parent.parts)
                        slice_end = len(pkg_parts) - (level - 1)
                        base_parts = pkg_parts[:max(0, slice_end)]
                        if mod_name:
                            resolved_parts = base_parts + mod_name.split(".")
                        else:
                            resolved_parts = base_parts
                        mod_name = ".".join(p for p in resolved_parts if p)

                    root_mod = mod_name.split(".")[0] if mod_name else ""

                    # If importing from a local module/package in the worktree
                    if mod_name in available_modules or root_mod in available_modules:
                        target_symbols = available_modules.get(mod_name) or available_modules.get(root_mod, set())
                        for alias in item_node.names:
                            if alias.name != "*" and target_symbols and alias.name not in target_symbols:
                                unresolved.append(
                                    f"{rel}:{item_node.lineno}: Symbol '{alias.name}' not found in module '{mod_name}' (available: {sorted(target_symbols)[:5]})"
                                )
                    elif root_mod and root_mod not in STDLIB_MODULES and root_mod not in configured_deps:
                        unresolved.append(f"{rel}:{item_node.lineno}: Cannot resolve module '{mod_name}'")
        except (SyntaxError, ValueError) as e:
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


def run_dependency_security_check(worktree_path: Path, target_files: list[str] | None = None) -> CheckResult:
    """Check 8: Dependency security validation (guards against unauthorized or risky packages)."""
    start = time.perf_counter()
    all_py = get_worktree_py_files(worktree_path)
    py_files = _filter_by_targets(all_py, worktree_path, target_files=target_files)

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
        except (SyntaxError, ValueError) as exc:
            logger.debug("AST parse failed for %s: %s", f, exc)

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
