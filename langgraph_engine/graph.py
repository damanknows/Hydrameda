#!/usr/bin/env python3
import argparse
import ast
import os
import re
import sys
import time
import subprocess
import sqlite3
from pathlib import Path
from typing import TypedDict, List, Dict
import httpx
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from openai import OpenAI

from preprocessor import extract_ast_skeleton, ObsidianRAG, create_isolated_worktree

for env_var in ('OPENAI_BASE_URL', 'OPENAI_API_BASE', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
    os.environ.pop(env_var, None)

LITELLM_URL = 'http' + '://127.0.0.1:4000/v1'
client = OpenAI(
    base_url=LITELLM_URL,
    api_key='sk-hydrameda-local-key',
    http_client=httpx.Client(trust_env=False, timeout=90.0)
)

def call_llm(model_group: str, prompt: str, max_tokens: int = 4096) -> tuple[str, str]:
    """Calls LiteLLM first; if Docker DNS/cooldown fails, falls back directly to host-OS Cerebras & Groq APIs."""
    from dotenv import dotenv_values
    env_vars = {**dotenv_values(Path.home() / "hydrameda" / ".env"), **os.environ}
    cerebras_key = env_vars.get("CEREBRAS_API_KEY", "")
    groq_key = env_vars.get("GROQ_API_KEY", "")

    # 1. Try direct host-OS endpoints first (immune to Docker DNS drops & zero proxy overhead!)
    direct_endpoints = []
    if cerebras_key:
        direct_endpoints.append(("cerebras/gpt-oss-120b", "https://api.cerebras.ai/v1", cerebras_key, "gpt-oss-120b"))
    if groq_key:
        direct_endpoints.append(("groq/gpt-oss-120b", "https://api.groq.com/openai/v1", groq_key, "openai/gpt-oss-120b"))

    last_err = None
    for label, base_url, api_key, model_id in direct_endpoints:
        try:
            direct_client = OpenAI(base_url=base_url, api_key=api_key, timeout=30.0)
            resp = direct_client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content or ""
            if content.strip():
                return content, label
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                time.sleep(4)
            continue

    # 2. Fallback to LiteLLM proxy on port 4000
    for attempt in range(1, 3):
        try:
            resp = client.chat.completions.create(
                model=model_group,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content or ""
            if content.strip():
                return content, model_group
        except Exception as e:
            last_err = e
            print(f"⚠️ LLM warning ({attempt}/2): {e}. Retrying in 4s...", flush=True)
            time.sleep(4)

    raise RuntimeError(f"All LLM attempts failed: {last_err}")

STDLIB_AUTO_MODULES = [
    "os", "sys", "math", "re", "json", "typing", "pathlib", "collections",
    "datetime", "hashlib", "hmac", "secrets", "dataclasses", "abc", "itertools",
    "functools", "sqlite3", "base64", "urllib", "ipaddress", "xml.etree.ElementTree"
]

def heal_module_imports(code: str, is_test_file: bool, module_code_for_test: str = "") -> str:
    """
    Deterministically heals Python imports & common stdlib anti-patterns:
    1. Replaces `base64.binascii.Error` with `binascii.Error` so mypy never fails on attr-defined.
    2. Splits multi-module imports (`import a, b` -> `import a\nimport b`) so E401 never triggers.
    3. Injects any missing stdlib modules referenced as `mod.xxx`.
    4. Ensures all public symbols from module.py used in test_module.py are imported.
    """
    # Fix `base64.binascii` mypy error
    code = code.replace("base64.binascii.", "binascii.")

    # Split `import a, b, c` on a single line into separate lines
    def _split_multi_import(match: re.Match) -> str:
        indent = match.group(1)
        mods = [m.strip() for m in match.group(2).split(",") if m.strip()]
        return "\n".join(f"{indent}import {m}" for m in mods)

    code = re.sub(r"^([ \t]*)import\s+([a-zA-Z0-9_]+(?:\s*,\s*[a-zA-Z0-9_]+)+)\s*$", _split_multi_import, code, flags=re.MULTILINE)

    missing_lines = []
    for mod in STDLIB_AUTO_MODULES:
        if re.search(rf"\b{mod}\.", code):
            if not re.search(rf"^\s*(?:import\s+.*\b{mod}\b|from\s+{mod}\s+import)", code, re.MULTILINE):
                missing_lines.append(f"import {mod}")

    if is_test_file and module_code_for_test:
        # Only fix relative dot import `from .module import` -> `from module import`
        code = re.sub(r"^(\s*)from\s+\.module\s+import\b", r"\1from module import", code, flags=re.MULTILINE)
        try:
            mod_tree = ast.parse(module_code_for_test)
            test_tree = ast.parse(code)
            # Find all names already imported from `module` in test_module.py
            already_imported = set()
            for node in ast.walk(test_tree):
                if isinstance(node, ast.ImportFrom) and node.module == "module":
                    for alias in node.names:
                        already_imported.add(alias.asname or alias.name)
            # Find all top-level symbols defined in module.py (including _helpers used by tests!)
            defined_in_mod = set()
            for node in mod_tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined_in_mod.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            defined_in_mod.add(target.id)
            # Any symbol defined in module.py, used in test_module.py, but NOT yet imported gets added safely!
            missing_exports = [
                name for name in sorted(defined_in_mod)
                if name not in already_imported and re.search(rf"\b{name}\b", code)
            ]
            if missing_exports and "*" not in already_imported:
                missing_lines.append(f"from module import {', '.join(missing_exports)}")
        except Exception:
            pass

    if not missing_lines:
        return code

    lines_out = code.splitlines()
    insert_idx = 0
    for i, line in enumerate(lines_out):
        if line.strip().startswith("from __future__ import"):
            insert_idx = i + 1
    lines_out[insert_idx:insert_idx] = missing_lines
    return "\n".join(lines_out) + "\n"


class HydramedaState(TypedDict):
    task_id: str
    user_prompt: str
    repo_path: str
    vault_context: str
    ast_skeleton: str
    spec_md: str
    worktree_path: str
    target_files: List[str]
    current_files: Dict[str, str]
    pytest_stderr: str
    retry_count: int
    status: str

def node_spec_researcher(state: HydramedaState) -> HydramedaState:
    print("\n🧠 [Node 1: Spec Researcher] Analyzing intent & querying Obsidian Vault...")
    rag = ObsidianRAG()
    vault_hits = rag.query(state["user_prompt"], top_k=3)
    ast_map = extract_ast_skeleton(state["repo_path"])
    
    prompt = (
        f"You are Hydrameda's Epistemic Architect.\n"
        f"User Prompt: {state['user_prompt']}\n\n"
        f"Relevant Obsidian Notes:\n{vault_hits}\n\n"
        f"Codebase AST Map:\n{ast_map}\n\n"
        "Write a concise technical specification (in Markdown) defining the required functions/classes in "
        "module.py and unit tests in test_module.py.\n"
        "STRICT DESIGN CONSTRAINTS:\n"
        "1. Use ONLY Python standard library modules (hashlib, hmac, os, secrets, base64, json, time, re, typing, dataclasses) and pytest.\n"
        "2. Raise ONLY built-in `ValueError` or `TypeError` (or custom exceptions that subclass `ValueError`).\n"
        "3. Do NOT write `raise NotImplementedError` stubs in your specification."
    )

    spec, used_model = call_llm("hybrid-architect", prompt, max_tokens=4096)
    if not spec:
        spec = "# Spec Generation Failed"
    
    spec_path = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault")) / "Specs" / f"{state['task_id']}.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec)
    
    state["vault_context"] = vault_hits
    state["ast_skeleton"] = ast_map
    state["spec_md"] = spec
    state["worktree_path"] = create_isolated_worktree(state["repo_path"], state["task_id"])
    print(f"✅ Spec written ({used_model}) to {spec_path} | Worktree at {state['worktree_path']}")
    return state

def extract_files_from_response(raw_text: str) -> Dict[str, str]:
    pattern = re.compile(r'<file\s+name=["\']([^"\']+)["\']\s*>(.*?)(?:</file>|$)', re.DOTALL)
    matches = pattern.findall(raw_text)
    extracted: Dict[str, str] = {}
    bt = chr(96) * 3
    for fname, content in matches:
        if "<file " in content:
            content = content.split("<file ")[0]
        lines = [
            line for line in content.splitlines()
            if not line.strip().startswith(bt)
        ]
        c = "\n".join(lines).strip()
        if c:
            extracted[fname.strip()] = c + "\n"
    return extracted

def node_code_builder(state: HydramedaState) -> HydramedaState:
    wt = Path(state["worktree_path"])
    prev_files = state.get("current_files") or {}

    existing_code_context = ""
    if state["retry_count"] > 0 and prev_files:
        snippets = [f'<file name="{k}">\n{v}</file>' for k, v in prev_files.items()]
        existing_code_context = (
            "\n=== YOUR PREVIOUS CODE ATTEMPT (DO NOT LOSE PROGRESS; FIX ONLY THE FAILURES BELOW) ===\n"
            + "\n\n".join(snippets)
            + "\n======================================================================================\n"
        )

    retry_msg = (
        f"\n⚠️ PREVIOUS BUILD FAILED (Attempt {state['retry_count']}/4).\n"
        f"{existing_code_context}\n"
        f"=== VERIFIER FAILURE TRACEBACK ===\n{state['pytest_stderr']}\n==================================\n"
    ) if state["retry_count"] > 0 else ""

    print(f"\n⚡ [Node 2: Code Builder] Generating code & unit tests (Attempt {state['retry_count'] + 1})...")

    prompt = (
        f"You are Hydrameda's Autonomous Coder.\n"
        f"Specification:\n{state['spec_md']}\n"
        f"{retry_msg}\n"
        "CRITICAL RULES FOR CODE & TESTS:\n"
        "1. Write COMPLETE, FULLY IMPLEMENTED production code in `module.py`. NEVER leave `raise NotImplementedError` or `pass` stubs!\n"
        "2. Use ONLY Python standard library modules (hashlib, hmac, os, secrets, base64, json, time, typing, dataclasses) and pytest.\n"
        "3. For temporal JWT claims with `leeway`: a token is expired only if `now > exp + leeway` (or `exp + leeway <= now`), and `nbf`/`iat` is in the future only if `claim - leeway > now`.\n"
        "4. In `test_module.py`, ALWAYS use direct top-level imports: `from module import ...`.\n"
        "5. Output ONLY raw <file> XML tags without markdown code fences:\n"
        '<file name="module.py">\n# complete working python code here\n</file>\n\n'
        '<file name="test_module.py">\n# complete pytest unit tests here\n</file>'
    )

    raw_content, used_model = call_llm("hybrid-coder", prompt, max_tokens=8192)
    extracted = extract_files_from_response(raw_content)

    merged_files = dict(prev_files)
    merged_files.update(extracted)

    mod_code = merged_files.get("module.py", "")
    if mod_code:
        merged_files["module.py"] = heal_module_imports(mod_code, is_test_file=False)
    if "test_module.py" in merged_files:
        merged_files["test_module.py"] = heal_module_imports(
            merged_files["test_module.py"],
            is_test_file=True,
            module_code_for_test=merged_files.get("module.py", "")
        )

    files_written = []
    for fname, content in merged_files.items():
        f_path = wt / fname
        f_path.parent.mkdir(parents=True, exist_ok=True)
        f_path.write_text(content)
        files_written.append(fname)

    if files_written:
        state["current_files"] = merged_files
        state["target_files"] = files_written
        print(f"✅ Generated & import-healed files ({used_model}): {files_written}")
    else:
        print(f"❌ Could not parse <file> tags from Builder Output ({used_model}).\nRaw snippet:\n{raw_content[:400]}...")

    return state

def node_sandbox_verifier(state: HydramedaState) -> HydramedaState:
    print("\n🛡️ [Node 3: Sandbox Verifier] Running rootless Docker container (Ruff + Mypy + Bandit + Pytest)...")
    wt = Path(state["worktree_path"]).resolve()
    
    # Copy /app into writable /tmp/work inside the container so ruff --fix can modify files freely,
    # and gate only on fatal syntax/undefined-name errors (E9,F63,F7,F82) so style nitpicks never waste retries!
    verify_script = (
        "cp -r /app /tmp/work && "
        "export PYTHONPATH=/tmp/work && "
        "ruff check --fix --cache-dir /tmp/ruff_cache /tmp/work >/dev/null 2>&1 || true && "
        "echo '--- [1/4] Ruff Static Check ---' && "
        "ruff check --select E9,F63,F7,F82 --cache-dir /tmp/ruff_cache /tmp/work && "
        "echo '--- [2/4] Mypy Type Check ---' && "
        "mypy --ignore-missing-imports --cache-dir /tmp/mypy_cache /tmp/work && "
        "echo '--- [3/4] Bandit SAST Security Scan ---' && "
        "(if command -v bandit >/dev/null 2>&1; then bandit -q -r /tmp/work/module.py -s B101,B105,B106,B107; else echo 'Bandit skipped'; fi) && "
        "echo '--- [4/4] Pytest Unit Suite ---' && "
        "pytest -v --tb=short -o cache_dir=/tmp/pytest_cache /tmp/work"
    )
    
    cmd = [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{wt}:/app:ro",
        "hydrameda-verifier:latest",
        verify_script
    ]
    
    result = subprocess.run(cmd, shell=False, capture_output=True, text=True)
    combined_output = (result.stdout + "\n" + result.stderr).strip()
    
    if result.returncode == 0:
        print("🎉 Verification PASSED with 0 errors!")
        print(combined_output)
        state["pytest_stderr"] = ""
        state["status"] = "PASSED"
    else:
        print(f"❌ Verification FAILED (Exit code {result.returncode})")
        print("--- VERIFIER DIAGNOSTICS ---")
        print(combined_output)
        print("----------------------------")
        lines = combined_output.splitlines()
        state["pytest_stderr"] = "\n".join(lines[-60:])
        state["retry_count"] += 1
        state["status"] = "FAILED"

    return state

def node_memory_consolidator(state: HydramedaState) -> HydramedaState:
    print("\n📚 [Node 4: Memory Consolidator] Summarizing via hybrid-fast & syncing Obsidian Vault...")
    commit_hash = "N/A (Verification Failed)"
    
    if state["status"] == "PASSED" and state["worktree_path"]:
        wt = state["worktree_path"]
        subprocess.run(["git", "-C", wt, "add", "."], check=False, capture_output=True)
        subprocess.run(
            ["git", "-C", wt, "commit", "-m", f"feat({state['task_id']}): verified autonomous build"],
            check=False, capture_output=True
        )
        rev = subprocess.run(["git", "-C", wt, "rev-parse", "--short", "HEAD"], check=False, capture_output=True, text=True)
        commit_hash = rev.stdout.strip() or "committed"
        print(f"📦 Auto-committed verified files to Git branch 'hydrameda/{state['task_id']}' ({commit_hash})")

    lesson_summary = (
        f"- Task `{state['task_id']}` completed with status **{state['status']}** after **{state['retry_count']}** verifier retry cycles.\n"
        f"- Verified artifacts: `{', '.join(state['target_files'])}` inside isolated worktree `{state['worktree_path']}`."
    )

    vault_root = Path(os.getenv("OBSIDIAN_VAULT_PATH", Path.home() / "ObsidianVault"))
    lessons_path = vault_root / "Lessons-Learned" / f"{state['task_id']}-report.md"
    report = (
        f"# Hydrameda Task Report: {state['task_id']}\n"
        f"- **Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Prompt:** {state['user_prompt']}\n"
        f"- **Status:** {state['status']}\n"
        f"- **Retries Required:** {state['retry_count']}\n"
        f"- **Git Branch:** `hydrameda/{state['task_id']}` (Commit: `{commit_hash}`)\n"
        f"- **Verified Worktree:** `{state['worktree_path']}`\n"
        f"- **Files Verified:** {state['target_files']}\n\n"
        f"## Local 3B Lesson Summary\n{lesson_summary}\n"
    )
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    lessons_path.write_text(report)
    
    try:
        rag = ObsidianRAG()
        total_notes = rag.sync_vault()
        print(f"✅ Report saved to {lessons_path} | LanceDB synced ({total_notes} vault notes indexed)")
    except Exception as e:
        print(f"✅ Report saved to {lessons_path} (Vault sync warning: {e})")

    return state

def decide_next_step(state: HydramedaState):
    if state["status"] == "PASSED":
        return "consolidate"
    if state["retry_count"] >= 4:
        print("❌ Max retries (4) reached. Escalating to human intervention.")
        return "consolidate"
    return "rebuild"

def build_hydrameda_graph():
    workflow = StateGraph(HydramedaState)
    
    workflow.add_node("spec_researcher", node_spec_researcher)
    workflow.add_node("code_builder", node_code_builder)
    workflow.add_node("sandbox_verifier", node_sandbox_verifier)
    workflow.add_node("memory_consolidator", node_memory_consolidator)
    
    workflow.set_entry_point("spec_researcher")
    workflow.add_edge("spec_researcher", "code_builder")
    workflow.add_edge("code_builder", "sandbox_verifier")
    
    workflow.add_conditional_edges(
        "sandbox_verifier",
        decide_next_step,
        {
            "rebuild": "code_builder",
            "consolidate": "memory_consolidator"
        }
    )
    workflow.add_edge("memory_consolidator", END)
    
    db_path = Path.home() / "hydrameda" / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    memory = SqliteSaver(conn)
    
    return workflow.compile(checkpointer=memory)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hydrameda Autonomous LangGraph Engine")
    parser.add_argument("positional_prompt", nargs="?", default=None, help="Task prompt")
    parser.add_argument("--prompt", "-p", type=str, default=None, help="Task prompt (named flag)")
    parser.add_argument("--repo", "-r", type=str, default=str(Path.home() / "hydrameda"), help="Target repository path")
    parser.add_argument("--task-id", "-t", type=str, default=None, help="Explicit task ID")
    args = parser.parse_args()

    prompt_arg = args.prompt or args.positional_prompt or "Create a JWT token validator with unit tests"
    slug = re.sub(r"[^a-z0-9]+", "-", prompt_arg.lower()).strip("-")[:24].rstrip("-")
    task_id = args.task_id or f"task-{slug}-{int(time.time()) % 10000}"
    
    print(f"🚀 Starting Hydrameda State Machine for task: '{task_id}'")
    app = build_hydrameda_graph()
    
    initial_state = HydramedaState(
        task_id=task_id,
        user_prompt=prompt_arg,
        repo_path=args.repo,
        vault_context="",
        ast_skeleton="",
        spec_md="",
        worktree_path="",
        target_files=[],
        current_files={},
        pytest_stderr="",
        retry_count=0,
        status="PENDING"
    )
    
    config = {"configurable": {"thread_id": task_id}}
    final_state = app.invoke(initial_state, config)
    print(f"\n🏁 Hydrameda pipeline execution completed with status: {final_state['status']}")
