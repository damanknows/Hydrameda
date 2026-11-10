#!/usr/bin/env python3
"""
Hydrameda Stage 4: Unified IDE Bridge (MCP Server)
Exposes Obsidian Vault RAG, Tree-sitter AST Maps, and Verified Git Worktrees to Cursor & Antigravity.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP  # type: ignore[attr-defined]
except ImportError:
    try:
        from fastmcp import FastMCP  # type: ignore[no-redef]
    except ImportError:
        try:
            from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore[no-redef]
        except ImportError:
            sys.stderr.write("FATAL: MCP library not found. Install with `pip install mcp` or `pip install fastmcp`\n")
            sys.exit(1)

HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda")).resolve()
ENGINE_DIR = HYDRAMEDA_ROOT / "langgraph_engine"
sys.path.insert(0, str(ENGINE_DIR))
sys.path.insert(0, str(HYDRAMEDA_ROOT))
from preprocessor import ObsidianRAG, extract_ast_skeleton, validate_task_id

from code_engine import PatchValidator, ValidationError

OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault"))
WORKTREES_DIR = HYDRAMEDA_ROOT / "worktrees"
LOGS_DIR = (HYDRAMEDA_ROOT / "logs").resolve()
LOGS_DIR.mkdir(parents=True, exist_ok=True)
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
    """Runs LanceDB + FastEmbed dense vector search over Obsidian /Specs and /Lessons-Learned."""
    rag = ObsidianRAG(vault_path=OBSIDIAN_VAULT)
    return rag.query(query, top_k=top_k)

@mcp.tool()
def get_repo_ast_map(repo_path: str) -> str:
    """Returns the compressed Tree-sitter AST skeleton (imports, classes, typed function signatures)."""
    return extract_ast_skeleton(repo_path, max_chars=8000)

@mcp.tool()
def list_verified_worktrees(verified_only: bool = False) -> str:
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

        # Requirement A5.7: Verify via machine-readable json
        ver_file = wt / ".hydrameda_verification.json"
        is_verified = False
        if ver_file.exists():
            try:
                import json
                v_data = json.loads(ver_file.read_text(encoding="utf-8"))
                is_verified = (v_data.get("overall_status") == "passed")
            except (json.JSONDecodeError, OSError):
                is_verified = False
        else:
            is_verified = "verified autonomous build" in last_commit.lower()

        if verified_only and not is_verified:
            continue
        status_tag = "✅ VERIFIED" if is_verified else "⏳ UNVERIFIED"
        files = [p.name for p in sorted(wt.glob("*.py"))]
        out.append(f"- **{wt.name}** [{status_tag}] | Branch: `{branch}` | Latest: `{last_commit}` | Path: `{wt}` | Files: {files}")
    return "\n".join(out)

@mcp.tool()
def inspect_worktree_diff(task_id: str) -> str:
    """Returns the git log, file list, and full source code of a verified worktree."""
    tid = validate_task_id(task_id)
    wt = (WORKTREES_DIR / tid).resolve()
    if not wt.is_relative_to(WORKTREES_DIR.resolve()) or not wt.exists():
        return f"Worktree '{tid}' not found at {wt}."
    log_res = subprocess.run(["git", "-C", str(wt), "log", "-2", "--stat"], capture_output=True, text=True, check=False)
    out = [f"# Worktree Inspection: {tid}", f"Path: `{wt}`", "## Git Log", f"```\n{log_res.stdout.strip()}\n```"]
    for py_file in sorted(wt.glob("*.py")):
        out.append(f"## File: `{py_file.name}`")
        out.append(f"```python\n{py_file.read_text(encoding='utf-8', errors='ignore')}\n```")
    return "\n\n".join(out)

@mcp.tool()
def trigger_autonomous_build(prompt: str, task_id: str = "") -> str:
    """Triggers an asynchronous Hydrameda LangGraph build in the background without auto-approving."""
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:24].rstrip("-")
    raw_tid = task_id.strip() if task_id else f"task-{slug}-{int(time.time()) % 10000}"
    tid = validate_task_id(raw_tid)
    log_file = (LOGS_DIR / f"{tid}.build.log").resolve()
    if not log_file.is_relative_to(LOGS_DIR):
        raise ValueError(f"Prohibited log path escape: {log_file}")

    with open(log_file, "w") as lf:
        subprocess.Popen(
            [str(PYTHON_BIN), str(GRAPH_SCRIPT), "--prompt", prompt, "--task-id", tid, "--non-interactive"],
            stdout=lf, stderr=subprocess.STDOUT, start_new_session=True
        )
    return f"🚀 Background build started for `{tid}`.\n- Log: `{log_file}`\n- Worktree: `{WORKTREES_DIR / tid}`\n- Status: Non-interactive execution (awaits explicit approval)"

@mcp.tool()
def get_task_status(task_id: str) -> str:
    """Checks the current lifecycle status and pending approval interrupt of a task."""
    tid = validate_task_id(task_id)
    from graph import build_hydrameda_graph
    app = build_hydrameda_graph()
    config = {"configurable": {"thread_id": tid}}
    snapshot = app.get_state(config)
    if not snapshot.values:
        log_file = LOGS_DIR / f"{tid}.build.log"
        if log_file.exists():
            snippet = log_file.read_text(encoding="utf-8", errors="ignore")[-800:]
            return f"Task '{tid}' state pending initialization. Recent log snippet:\n{snippet}"
        return f"Task '{tid}' not found in state store or logs."

    task_state = snapshot.values.get("task_state", "UNKNOWN")
    status = snapshot.values.get("status", "UNKNOWN")
    next_nodes = snapshot.next
    out = [
        f"# Hydrameda Task Status: `{tid}`",
        f"- Task State: **{task_state}**",
        f"- Status: **{status}**",
        f"- Next Graph Node(s): `{next_nodes}`",
    ]
    if snapshot.tasks:
        for t in snapshot.tasks:
            for intr in getattr(t, "interrupts", []):
                val = intr.value if isinstance(intr.value, dict) else {}
                out.append("⚠️ Awaiting Human Approval:")
                out.append(f"  - Action: `{val.get('action')}`")
                out.append(f"  - Manifest Hash: `{val.get('manifest_hash')}`")
                out.append(f"  - Question: {val.get('question')}")
    return "\n".join(out)

@mcp.tool()
def approve_task(
    task_id: str,
    approved_manifest_hash: str,
    approved: bool = True,
    destructive_approved: bool = False,
    merge_approved: bool = False
) -> str:
    """Explicitly approves or rejects a paused Hydrameda task and resumes autonomous execution."""
    tid = validate_task_id(task_id)
    from graph import build_hydrameda_graph
    from langgraph.types import Command
    app = build_hydrameda_graph()
    config = {"configurable": {"thread_id": tid}}
    snapshot = app.get_state(config)
    if not snapshot.next:
        return f"Task '{tid}' is not currently awaiting resumption (next nodes: {snapshot.next})."

    resume_payload = {
        "approved": bool(approved),
        "approved_manifest_hash": str(approved_manifest_hash).strip(),
        "task_id": tid,
        "approver": "mcp_client",
        "approval_mode": "mcp_tool",
        "authorization_source": "mcp_approve_task",
        "destructive_approved": bool(destructive_approved),
        "merge_approved": bool(merge_approved)
    }
    resumed_state = app.invoke(Command(resume=resume_payload), config)
    final_state = resumed_state.get("task_state", resumed_state.get("status", "UNKNOWN"))
    return f"✅ Task '{tid}' resumed with approval: approved={approved}. Current state: {final_state}"

@mcp.tool()
def merge_verified_worktree(task_id: str, target_dir: str, module_rename: str = "", force: bool = False) -> str:
    """Copies verified worktree Python files into target_dir (optionally renaming module.py).
    
    Refuses to merge unverified worktrees unless force=True.
    Validates target destination path safety and boundaries.
    """
    tid = validate_task_id(task_id)
    wt = (WORKTREES_DIR / tid).resolve()
    if not wt.is_relative_to(WORKTREES_DIR.resolve()) or not wt.exists():
        return f"Worktree '{tid}' not found."

    commit_res = subprocess.run(
        ["git", "-C", str(wt), "log", "-1", "--oneline"],
        capture_output=True, text=True, check=False
    )
    last_commit = commit_res.stdout.strip()

    # Requirement A5.7: Verify via machine-readable verification record
    ver_file = wt / ".hydrameda_verification.json"
    is_verified = False
    if ver_file.exists():
        try:
            import json
            v_data = json.loads(ver_file.read_text(encoding="utf-8"))
            is_verified = (v_data.get("overall_status") == "passed")
        except (json.JSONDecodeError, OSError):
            is_verified = False
    else:
        is_verified = "verified autonomous build" in last_commit.lower()

    if not force and not is_verified:
        return f"❌ Refusing to merge unverified worktree '{tid}' (latest commit: '{last_commit}'). Set force=True to override."


    dest = Path(target_dir).expanduser().resolve()
    sensitive_prefixes = (
        "/", "/etc", "/var", "/usr", "/root", "/bin", "/sbin",
        "/boot", "/sys", "/proc", "/run", "/dev"
    )
    if str(dest) in sensitive_prefixes or any(str(dest) == p or str(dest).startswith(p + "/") for p in sensitive_prefixes):
        return f"❌ Security Error: Target directory '{dest}' is restricted."

    dest.mkdir(parents=True, exist_ok=True)
    validator = PatchValidator(dest, allow_delete=False)
    copied = []
    for py_file in sorted(wt.glob("*.py")):
        out_name = py_file.name
        if module_rename:
            stem = module_rename.removesuffix(".py")
            if py_file.name == "module.py":
                out_name = f"{stem}.py"
            elif py_file.name == "test_module.py":
                out_name = f"test_{stem}.py"

        try:
            target_path = validator.validate_path_safety(out_name)
        except ValidationError as ve:
            return f"❌ Security Error: Unsafe target file path '{out_name}': {ve}"

        shutil.copy2(py_file, target_path)
        if module_rename and py_file.name == "test_module.py":
            # Update `from module import` -> `from <stem> import` in the exported test file
            stem = module_rename.removesuffix(".py")
            test_dest = target_path
            t_code = test_dest.read_text(encoding="utf-8", errors="ignore")
            t_code = re.sub(r"\bfrom\s+module\s+import\b", f"from {stem} import", t_code)
            t_code = re.sub(r"\bimport\s+module\b", f"import {stem} as module", t_code)
            test_dest.write_text(t_code, encoding="utf-8")
        copied.append(str(target_path))
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
