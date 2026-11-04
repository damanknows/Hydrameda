#!/usr/bin/env python3
"""
Hydrameda Stage 4: Unified IDE Bridge (MCP Server)
Exposes Obsidian Vault RAG, Tree-sitter AST Maps, and Verified Git Worktrees to Cursor & Antigravity.
"""
import os
import re
import sys
import time
import shutil
import subprocess
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from fastmcp import FastMCP
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError:
            class FastMCP:
                def __init__(self, name: str): self.name = name
                def tool(self): return lambda fn: fn
                def run(self, **kwargs): pass

ENGINE_DIR = Path.home() / "hydrameda" / "langgraph_engine"
sys.path.insert(0, str(ENGINE_DIR))
from preprocessor import extract_ast_skeleton, ObsidianRAG

HYDRAMEDA_ROOT = Path.home() / "hydrameda"
OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault"))
WORKTREES_DIR = HYDRAMEDA_ROOT / "worktrees"
PYTHON_BIN = HYDRAMEDA_ROOT / ".venv" / "bin" / "python"
GRAPH_SCRIPT = ENGINE_DIR / "graph.py"

mcp = FastMCP("Hydrameda-Brain")

@mcp.tool()
def get_active_specs(limit: int = 10) -> str:
    """Lists architectural specifications in Obsidian /Specs/ with previews."""
    specs_dir = OBSIDIAN_VAULT / "Specs"
    if not specs_dir.exists():
        return "No /Specs directory found in Obsidian Vault."
    spec_files = sorted(specs_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not spec_files:
        return "No active specs found in /Specs."
    out = ["# Active Hydrameda Specifications"]
    for f in spec_files[:limit]:
        preview = f.read_text(encoding="utf-8", errors="ignore")[:260].replace("\n", " ")
        out.append(f"- **{f.name}** (`{f}`): {preview}...")
    return "\n".join(out)

@mcp.tool()
def query_obsidian_memory(query: str, top_k: int = 3) -> str:
    """Runs LanceDB + FastEmbed hybrid search over Obsidian /Specs and /Lessons-Learned."""
    rag = ObsidianRAG(vault_path=OBSIDIAN_VAULT)
    return rag.query(query, top_k=top_k)

@mcp.tool()
def get_repo_ast_map(repo_path: str) -> str:
    """Returns the compressed Tree-sitter AST skeleton (imports, classes, typed function signatures)."""
    return extract_ast_skeleton(repo_path, max_chars=8000)

@mcp.tool()
def list_verified_worktrees() -> str:
    """Shows isolated Git worktree branches verified by the LangGraph loop ready for surgical editing."""
    if not WORKTREES_DIR.exists():
        return "No worktrees directory found."
    worktrees = sorted([d for d in WORKTREES_DIR.iterdir() if d.is_dir()])
    if not worktrees:
        return "No active worktrees found."
    out = ["# Hydrameda Isolated Worktrees"]
    for wt in worktrees:
        branch_res = subprocess.run(
            ["git", "-C", str(wt), "branch", "--show-current"],
            capture_output=True, text=True, check=False
        )
        commit_res = subprocess.run(
            ["git", "-C", str(wt), "log", "-1", "--oneline"],
            capture_output=True, text=True, check=False
        )
        branch = branch_res.stdout.strip() or "detached"
        last_commit = commit_res.stdout.strip() or "uncommitted"
        files = [p.name for p in sorted(wt.glob("*.py"))]
        out.append(f"- **{wt.name}** | Branch: `{branch}` | Latest: `{last_commit}` | Path: `{wt}` | Files: {files}")
    return "\n".join(out)

@mcp.tool()
def inspect_worktree_diff(task_id: str) -> str:
    """Returns the git log, file list, and full source code of a verified worktree."""
    wt = WORKTREES_DIR / task_id
    if not wt.exists():
        return f"Worktree '{task_id}' not found at {wt}."
    log_res = subprocess.run(["git", "-C", str(wt), "log", "-2", "--stat"], capture_output=True, text=True, check=False)
    out = [f"# Worktree Inspection: {task_id}", f"Path: `{wt}`", "## Git Log", f"```\n{log_res.stdout.strip()}\n```"]
    for py_file in sorted(wt.glob("*.py")):
        out.append(f"## File: `{py_file.name}`")
        out.append(f"```python\n{py_file.read_text(encoding='utf-8', errors='ignore')}\n```")
    return "\n\n".join(out)

@mcp.tool()
def trigger_autonomous_build(prompt: str, task_id: str = "") -> str:
    """Triggers an asynchronous Hydrameda LangGraph build in the background from the IDE."""
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:24].rstrip("-")
    tid = task_id.strip() or f"task-{slug}-{int(time.time()) % 10000}"
    log_file = HYDRAMEDA_ROOT / "worktrees" / f"{tid}.build.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "w") as lf:
        subprocess.Popen(
            [str(PYTHON_BIN), str(GRAPH_SCRIPT), "--prompt", prompt, "--task-id", tid],
            stdout=lf, stderr=subprocess.STDOUT, start_new_session=True
        )
    return f"🚀 Background build started for `{tid}`.\n- Log: `{log_file}`\n- Worktree: `{WORKTREES_DIR / tid}`"

@mcp.tool()
def merge_verified_worktree(task_id: str, target_dir: str, module_rename: str = "") -> str:
    """Copies verified worktree Python files into target_dir (optionally renaming module.py)."""
    wt = WORKTREES_DIR / task_id
    if not wt.exists():
        return f"Worktree '{task_id}' not found."
    dest = Path(target_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for py_file in sorted(wt.glob("*.py")):
        out_name = py_file.name
        if module_rename:
            stem = module_rename.removesuffix(".py")
            if py_file.name == "module.py":
                out_name = f"{stem}.py"
            elif py_file.name == "test_module.py":
                out_name = f"test_{stem}.py"
        shutil.copy2(py_file, dest / out_name)
        if module_rename and py_file.name == "test_module.py":
            # Update `from module import` -> `from <stem> import` in the exported test file
            stem = module_rename.removesuffix(".py")
            test_dest = dest / out_name
            t_code = test_dest.read_text(encoding="utf-8", errors="ignore")
            t_code = re.sub(r"\bfrom\s+module\s+import\b", f"from {stem} import", t_code)
            t_code = re.sub(r"\bimport\s+module\b", f"import {stem} as module", t_code)
            test_dest.write_text(t_code, encoding="utf-8")
        copied.append(str(dest / out_name))
    return f"✅ Exported {len(copied)} verified files from `{task_id}` to `{dest}`: {copied}"

if __name__ == "__main__":
    if "--List" in sys.argv or "--list" in sys.argv:
        print(list_verified_worktrees())
    elif "--inspect" in sys.argv and len(sys.argv) >= 3:
        print(inspect_worktree_diff(sys.argv[2]))
    elif "--merge" in sys.argv and len(sys.argv) >= 4:
        rename = sys.argv[4] if len(sys.argv) >= 5 else ""
        print(merge_verified_worktree(sys.argv[2], sys.argv[3], rename))
    elif "--sse" in sys.argv:
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
