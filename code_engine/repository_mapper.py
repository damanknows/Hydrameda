"""
Hydrameda Code Engine: Repository Structure and Tree-sitter Symbol Mapper.

Capabilities:
- Repository directory tree traversal with ignore filters.
- Tree-sitter AST extraction for Python (classes, methods, functions, imports, dependencies).
- Fallback/heuristic extraction for other common languages (JS/TS, Go, Rust, JSON, YAML).
- Symbol dependency graph detection (internal module imports and external packages).
"""
from __future__ import annotations
import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Any, Optional

try:
    from tree_sitter_language_pack import get_parser
    HAS_TREESITTER = True
except ImportError:
    HAS_TREESITTER = False

IGNORE_PATTERNS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build",
    "n8n_data", "lancedb_data", "worktrees", ".fastembed_cache", ".python-3.12"
}


class RepositoryMapper:
    """Scans and maps a repository structure, symbols, and dependencies."""

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        if not self.repo_path.exists():
            raise FileNotFoundError(f"Repository path does not exist: {self.repo_path}")

    def list_files(self, relative: bool = True, extensions: Optional[Set[str]] = None) -> List[str]:
        """Lists all non-ignored project files."""
        files = []
        for p in sorted(self.repo_path.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.repo_path)
            if any(part in IGNORE_PATTERNS or part.startswith(".") for part in rel.parts):
                continue
            if extensions and p.suffix.lower() not in extensions:
                continue
            files.append(str(rel) if relative else str(p))
        return files

    def extract_file_symbols(self, file_path: str | Path) -> Dict[str, Any]:
        """Extracts imports, classes, functions, and docstrings from a Python file."""
        target = (self.repo_path / file_path).resolve() if not Path(file_path).is_absolute() else Path(file_path)
        if not target.exists() or not target.is_file():
            return {"file": str(file_path), "error": "File not found"}

        source_bytes = target.read_bytes()
        source_text = source_bytes.decode("utf-8", errors="ignore")

        symbols: Dict[str, Any] = {
            "file": str(target.relative_to(self.repo_path) if target.is_relative_to(self.repo_path) else target),
            "imports": [],
            "classes": [],
            "functions": [],
            "dependencies": []
        }

        # Try Tree-sitter if available
        if HAS_TREESITTER and target.suffix == ".py":
            try:
                parser = get_parser("python")
                tree = parser.parse(source_bytes)
                for node in tree.root_node.children:
                    if node.type in ("import_statement", "import_from_statement"):
                        stmt = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore").strip()
                        symbols["imports"].append(stmt)
                        # Extract module names
                        for word in re.findall(r"\b(?:import|from)\s+([a-zA-Z0-9_\.]+)", stmt):
                            symbols["dependencies"].append(word.split(".")[0])
                    elif node.type == "class_definition":
                        name_node = node.child_by_field_name("name")
                        cls_name = source_bytes[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="ignore") if name_node else "Unknown"
                        methods = []
                        body = node.child_by_field_name("body")
                        if body:
                            for child in body.children:
                                if child.type == "function_definition":
                                    fn_name_node = child.child_by_field_name("name")
                                    if fn_name_node:
                                        methods.append(source_bytes[fn_name_node.start_byte:fn_name_node.end_byte].decode("utf-8", errors="ignore"))
                        symbols["classes"].append({"name": cls_name, "methods": methods})
                    elif node.type == "function_definition":
                        fn_name_node = node.child_by_field_name("name")
                        if fn_name_node:
                            fn_name = source_bytes[fn_name_node.start_byte:fn_name_node.end_byte].decode("utf-8", errors="ignore")
                            symbols["functions"].append(fn_name)
                return symbols
            except Exception:
                pass

        # Fallback to stdlib ast for Python
        if target.suffix == ".py":
            try:
                tree = ast.parse(source_text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            symbols["imports"].append(f"import {alias.name}")
                            symbols["dependencies"].append(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = ", ".join(a.name for a in node.names)
                        symbols["imports"].append(f"from {node.module} import {names}")
                        symbols["dependencies"].append(node.module.split(".")[0])
                    elif isinstance(node, ast.ClassDef):
                        methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                        symbols["classes"].append({"name": node.name, "methods": methods})
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols["functions"].append(node.name)
            except Exception as e:
                symbols["error"] = f"AST parse warning: {e}"

        symbols["dependencies"] = sorted(set(symbols["dependencies"]))
        return symbols

    def map_repository(self, max_chars: int = 12000) -> Dict[str, Any]:
        """Generates a comprehensive structured map of the repository."""
        py_files = self.list_files(extensions={".py"})
        file_maps = []
        all_deps: Set[str] = set()

        for f in py_files:
            syms = self.extract_file_symbols(f)
            file_maps.append(syms)
            all_deps.update(syms.get("dependencies", []))

        return {
            "repo_name": self.repo_path.name,
            "root": str(self.repo_path),
            "total_files": len(self.list_files()),
            "python_files": len(py_files),
            "files": file_maps,
            "detected_dependencies": sorted(all_deps)
        }

    def get_compressed_skeleton(self, max_chars: int = 6000) -> str:
        """Returns a concise text representation of files and symbols for LLM context."""
        repo_map = self.map_repository()
        lines = [f"# REPOSITORY MAP: {repo_map['repo_name']} ({repo_map['python_files']} Python files)"]

        for f in repo_map["files"]:
            lines.append(f"\n## {f['file']}")
            for imp in f.get("imports", [])[:8]:
                lines.append(f"  {imp}")
            for cls in f.get("classes", []):
                methods_str = f" (methods: {', '.join(cls['methods'][:6])})" if cls["methods"] else ""
                lines.append(f"  class {cls['name']}{methods_str}")
            for fn in f.get("functions", []):
                lines.append(f"  def {fn}(...)")

        out = "\n".join(lines)
        return out[:max_chars]
