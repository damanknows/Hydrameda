#!/usr/bin/env python3
"""
Hydrameda Neuro-Symbolic Closed-Loop LangGraph Engine.

Decomposed 12-Node Architecture:
 1. spec_analyzer      - Analyzes intent, requirements, and AST repository map
 2. rag_retriever      - Queries Obsidian Vault / LanceDB for architectural invariants
 3. architect          - Synthesizes formal technical specification (Markdown)
 4. prompt_optimizer   - Injects epistemic guards and stdlib constraints
 5. approval_gate      - Human-in-the-loop governance (supports interrupt/resume)
 6. task_router        - Routes between isolated execution and cancellation
 7. worktree_builder   - Provisions isolated Git worktree branch
 8. code_builder       - Generates module & test code with deterministic import healing
 9. sandbox_verifier   - Rootless Docker verification (Ruff + Mypy + Bandit + Pytest)
10. error_analyzer     - Neuro-symbolic failure diagnosis & test traceback extraction
11. repair_agent       - Targeted self-healing repair loop (maximum 3 attempts)
12. consolidator       - Memory persistence, Git commit, Obsidian report & LanceDB sync
"""
import argparse
import ast
import os
import re
import sys
import time
import subprocess
import sqlite3
from pathlib import Path
from typing import TypedDict, List, Dict, Any, Optional
import httpx
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command
from openai import OpenAI

from preprocessor import extract_ast_skeleton, ObsidianRAG, create_isolated_worktree

# Code Engine, HydraRouter, and Verification Engine integrations
try:
    from code_engine import (
        PatchEngine, ChangeManifest, FileChange, FileOperation, ValidationError,
        RepositoryMapper, FileSelector, DiffGenerator,
        RiskLevel, RiskAssessment, ApprovalDecision, ConsolidationReport,
        GovernanceEngine, SafeConsolidator
    )
    from hydra_router import get_router, ModelRole, OutputValidator, RouterResponse
    from verification_engine import VerificationPipeline, CheckStatus, CheckResult, VerificationReport
except ImportError:
    # Handle sys.path when running graph.py standalone
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from code_engine import (
        PatchEngine, ChangeManifest, FileChange, FileOperation, ValidationError,
        RepositoryMapper, FileSelector, DiffGenerator,
        RiskLevel, RiskAssessment, ApprovalDecision, ConsolidationReport,
        GovernanceEngine, SafeConsolidator
    )
    from hydra_router import get_router, ModelRole, OutputValidator, RouterResponse
    from verification_engine import VerificationPipeline, CheckStatus, CheckResult, VerificationReport

# Strip proxy variables that interfere with local endpoints
for env_var in (
    'OPENAI_BASE_URL', 'OPENAI_API_BASE',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy'
):
    os.environ.pop(env_var, None)

HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda")).resolve()
LITELLM_URL = os.getenv("LITELLM_URL", "http://127.0.0.1:4000/v1")

client = OpenAI(
    base_url=LITELLM_URL,
    api_key=os.getenv("LITELLM_MASTER_KEY", "sk-hydrameda-local-key"),
    http_client=httpx.Client(trust_env=False, timeout=90.0)
)


# Pluggable execution handlers (allows deterministic mocking in test suites)
_LLM_HANDLER = None
_VERIFIER_HANDLER = None

def set_llm_handler(handler):
    global _LLM_HANDLER
    _LLM_HANDLER = handler
    get_router().set_mock_handler(handler)

def set_verifier_handler(handler):
    global _VERIFIER_HANDLER
    _VERIFIER_HANDLER = handler


def call_llm(model_group: str, prompt: str, max_tokens: int = 4096) -> tuple[str, str]:
    """Routes LLM requests through the centralized HydraRouter via LiteLLM."""
    if _LLM_HANDLER is not None:
        return _LLM_HANDLER(model_group, prompt)
    router = get_router()
    role = ModelRole.normalize(model_group)
    resp = router.complete(role=role, prompt=prompt, max_tokens=max_tokens)
    return resp.content, resp.model_used


STDLIB_AUTO_MODULES = [
    "os", "sys", "math", "re", "json", "typing", "pathlib", "collections",
    "datetime", "hashlib", "hmac", "secrets", "dataclasses", "abc", "itertools",
    "functools", "sqlite3", "base64", "urllib", "ipaddress", "xml.etree.ElementTree"
]


def heal_module_imports(code: str, is_test_file: bool, module_code_for_test: str = "") -> str:
    """Deterministically heals Python imports & common stdlib anti-patterns."""
    code = code.replace("base64.binascii.", "binascii.")

    def _split_multi_import(match: re.Match) -> str:
        indent = match.group(1)
        mods = [m.strip() for m in match.group(2).split(",") if m.strip()]
        return "\n".join(f"{indent}import {m}" for m in mods)

    code = re.sub(
        r"^([ \t]*)import\s+([a-zA-Z0-9_]+(?:\s*,\s*[a-zA-Z0-9_]+)+)\s*$",
        _split_multi_import,
        code,
        flags=re.MULTILINE
    )

    missing_lines = []
    for mod in STDLIB_AUTO_MODULES:
        if re.search(rf"\b{mod}\.", code):
            if not re.search(rf"^\s*(?:import\s+.*\b{mod}\b|from\s+{mod}\s+import)", code, re.MULTILINE):
                missing_lines.append(f"import {mod}")

    if is_test_file and module_code_for_test:
        code = re.sub(r"^(\s*)from\s+\.module\s+import\b", r"\1from module import", code, flags=re.MULTILINE)
        try:
            mod_tree = ast.parse(module_code_for_test)
            test_tree = ast.parse(code)
            already_imported = set()
            for node in ast.walk(test_tree):
                if isinstance(node, ast.ImportFrom) and node.module == "module":
                    for alias in node.names:
                        already_imported.add(alias.asname or alias.name)
            defined_in_mod = set()
            for node in mod_tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined_in_mod.add(node.name)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            defined_in_mod.add(target.id)
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


def extract_files_from_response(raw_text: str) -> Dict[str, str]:
    """Extracts code blocks tagged with <file name="...">...</file> from LLM output."""
    pattern = re.compile(r'<file\s+name=["\']([^"\']+)["\']\s*>(.*?)(?:</file>|$)', re.DOTALL)
    matches = pattern.findall(raw_text)
    extracted: Dict[str, str] = {}
    bt = chr(96) * 3
    for fname, content in matches:
        if "<file " in content:
            content = content.split("<file ")[0]
        lines = [line for line in content.splitlines() if not line.strip().startswith(bt)]
        c = "\n".join(lines).strip()
        if c:
            extracted[fname.strip()] = c + "\n"
    return extracted


# ============================================================================
# Strongly-Structured Shared State
# ============================================================================

class HydramedaState(TypedDict, total=False):
    # Core identifiers & inputs
    task_id: str
    user_prompt: str
    repo_path: str
    auto_approve: bool

    # Node 1 & 2 outputs: analysis & context
    task_analysis: Dict[str, Any]
    vault_context: str
    ast_skeleton: str

    # Node 3 & 4 outputs: spec & optimized prompt
    spec_md: str
    optimized_prompt: str

    # Node 5 & 6 outputs: governance & routing
    approval_status: str       # "PENDING" | "APPROVED" | "REJECTED"
    route: str                 # "execute" | "cancel"

    # Node 7 & 8 outputs: worktree & files
    worktree_path: str
    target_files: List[str]
    current_files: Dict[str, str]

    # Node 9 output: verification results
    verifier_output: str
    pytest_stderr: str
    verifier_exit_code: int
    verification_report: Dict[str, Any]

    # Neuro-symbolic repair loop tracking (Max 3 repairs)
    repair_count: int
    retry_count: int           # Alias for backward compatibility
    error_analysis: Dict[str, Any]

    # Final outputs & telemetry
    status: str                # "PENDING" | "IN_PROGRESS" | "PASSED" | "FAILED" | "REJECTED" | "ERROR"
    commit_hash: str
    final_report: str
    structured_logs: List[Dict[str, Any]]
    error: str

    # Governance, Manifest, and Consolidation
    manifest: Dict[str, Any]
    manifest_hash: str
    diff: str
    risk_assessment: Dict[str, Any]
    approval_decision: Dict[str, Any]
    destructive_approved: bool
    merge_approved: bool
    require_consolidation_approval: bool
    consolidation_report: Dict[str, Any]


def log_event(state: HydramedaState, node: str, level: str, message: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Appends a structured log entry to state['structured_logs'] and prints it."""
    logs = state.setdefault("structured_logs", [])
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "task_id": state.get("task_id", "unknown"),
        "node": node,
        "level": level,
        "message": message,
        "metadata": metadata or {}
    }
    logs.append(entry)
    icon = {"INFO": "ℹ️", "SUCCESS": "✅", "WARN": "⚠️", "ERROR": "❌"}.get(level, "•")
    print(f"{icon} [{node}] {message}", flush=True)


# ============================================================================
# 12 Node Definitions
# ============================================================================

def node_spec_analyzer(state: HydramedaState) -> HydramedaState:
    """Node 1: Analyzes user prompt intent and extracts repository AST skeleton.

    Inputs:  user_prompt, repo_path, task_id
    Outputs: task_analysis, ast_skeleton
    """
    node = "spec_analyzer"
    log_event(state, node, "INFO", f"Analyzing task specification for '{state.get('task_id')}'...")
    try:
        prompt = state.get("user_prompt", "").strip()
        if not prompt:
            raise ValueError("user_prompt cannot be empty")

        repo_path = state.get("repo_path") or str(HYDRAMEDA_ROOT)
        ast_map = extract_ast_skeleton(repo_path)

        # Extract symbols and intent keywords
        keywords = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", prompt)
        analysis = {
            "intent": prompt[:120],
            "keywords": keywords[:15],
            "target_files": ["module.py", "test_module.py"],
            "constraints": [
                "pure python standard library only",
                "full type hints",
                "pytest test suite with top-level imports",
                "no NotImplementedError stubs"
            ]
        }

        state["task_analysis"] = analysis
        state["ast_skeleton"] = ast_map
        log_event(state, node, "SUCCESS", f"Task analyzed ({len(keywords)} keywords, AST map {len(ast_map)} chars)")
    except Exception as e:
        log_event(state, node, "ERROR", f"Failed to analyze spec: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
    return state


def node_rag_retriever(state: HydramedaState) -> HydramedaState:
    """Node 2: Queries Obsidian Vault via LanceDB for architectural invariants.

    Inputs:  user_prompt, task_analysis
    Outputs: vault_context
    """
    node = "rag_retriever"
    log_event(state, node, "INFO", "Querying Obsidian Vault memory & LanceDB embeddings...")
    try:
        rag = ObsidianRAG()
        query = state.get("user_prompt", "")
        vault_hits = rag.query(query, top_k=3)
        state["vault_context"] = vault_hits
        log_event(state, node, "SUCCESS", f"Retrieved Obsidian RAG context ({len(vault_hits)} chars)")
    except Exception as e:
        log_event(state, node, "WARN", f"Obsidian RAG retrieval warning (using empty context): {e}")
        state["vault_context"] = ""
    return state


def node_architect(state: HydramedaState) -> HydramedaState:
    """Node 3: Generates formal Markdown technical specification.

    Inputs:  user_prompt, task_analysis, vault_context, ast_skeleton, task_id
    Outputs: spec_md
    """
    node = "architect"
    log_event(state, node, "INFO", "Synthesizing architectural specification via hybrid-architect...")
    try:
        prompt = (
            f"You are Hydrameda's Epistemic Architect.\n"
            f"User Prompt: {state.get('user_prompt')}\n\n"
            f"Relevant Obsidian Notes:\n{state.get('vault_context')}\n\n"
            f"Codebase AST Map:\n{state.get('ast_skeleton')}\n\n"
            "Write a concise technical specification (in Markdown) defining required functions/classes in "
            "module.py and unit tests in test_module.py.\n"
            "STRICT DESIGN CONSTRAINTS:\n"
            "1. Use ONLY Python standard library modules (hashlib, hmac, os, secrets, base64, json, time, re, typing, dataclasses) and pytest.\n"
            "2. Raise ONLY built-in `ValueError` or `TypeError` (or custom exceptions that subclass `ValueError`).\n"
            "3. Do NOT write `raise NotImplementedError` stubs in your specification."
        )
        spec, used_model = call_llm(ModelRole.PLANNER.value, prompt, max_tokens=4096)

        if not spec:
            spec = f"# Spec Generation Failed for {state.get('task_id')}"

        vault_path_env = os.getenv("OBSIDIAN_VAULT_PATH", str(Path.home() / "ObsidianVault"))
        spec_path = Path(vault_path_env) / "Specs" / f"{state.get('task_id', 'task')}.md"
        try:
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(spec, encoding="utf-8")
        except Exception as write_err:
            log_event(state, node, "WARN", f"Could not write spec to vault: {write_err}")

        state["spec_md"] = spec
        log_event(state, node, "SUCCESS", f"Specification generated ({used_model}, {len(spec)} chars)")
    except Exception as e:
        log_event(state, node, "ERROR", f"Architect failure: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
        state["spec_md"] = "# Architectural Synthesis Failed"
    return state


def node_prompt_optimizer(state: HydramedaState) -> HydramedaState:
    """Node 4: Compiles and optimizes the code generation prompt with epistemic rules.

    Inputs:  spec_md, task_analysis, ast_skeleton
    Outputs: optimized_prompt
    """
    node = "prompt_optimizer"
    log_event(state, node, "INFO", "Optimizing code-generation prompt with epistemic rules...")
    try:
        spec = state.get("spec_md", "")
        prompt_lower = (state.get("user_prompt", "") + " " + spec).lower()
        if any(lib in prompt_lower for lib in ["flask", "yaml", "pyyaml", "cryptography", "jwt", "requests", "fastapi"]):
            lib_rule = "2. Rely on Python standard library and explicitly requested packages (e.g. Flask, PyYAML, cryptography, pytest). Avoid unrequested external dependencies.\n"
        else:
            lib_rule = "2. Prefer Python standard library modules (hashlib, hmac, os, secrets, base64, json, time, re, typing, dataclasses) and pytest.\n"

        prompt = (
            f"You are Hydrameda's Autonomous Repository Coder.\n"
            f"Specification:\n{spec}\n\n"
            "CRITICAL RULES FOR REPOSITORY CODE & TESTS:\n"
            "1. Write COMPLETE, FULLY IMPLEMENTED production code. NEVER leave `raise NotImplementedError` or `pass` stubs!\n"
            f"{lib_rule}"
            "3. Multi-file generation is fully supported. Specify each created or modified file with its clean relative path in <file name=\"...\"> tags.\n"
            "4. For tests, ensure all imports of local project modules use top-level or clean package imports.\n"
            "5. Output ONLY raw <file> XML tags without markdown code fences:\n"
            '<file name="relative/path/to/file.py">\n# complete working python code here\n</file>\n\n'
            '<file name="tests/test_file.py">\n# complete pytest unit tests here\n</file>'
        )
        state["optimized_prompt"] = prompt
        log_event(state, node, "SUCCESS", f"Prompt optimized ({len(prompt)} chars)")
    except Exception as e:
        log_event(state, node, "ERROR", f"Prompt optimizer failure: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
    return state


def node_approval_gate(state: HydramedaState) -> HydramedaState:
    """Node 5: Human-in-the-loop governance gate.

    Supports resumable human review via LangGraph interrupt/Command(resume=...).
    If auto_approve is True or approval_status is already APPROVED, passes without pausing.

    Inputs:  auto_approve, approval_status, spec_md, task_id
    Outputs: approval_status ("APPROVED" | "REJECTED")
    """
    node = "approval_gate"
    current_status = state.get("approval_status")
    auto_approve = state.get("auto_approve", True)

    if auto_approve or current_status == "APPROVED":
        state["approval_status"] = "APPROVED"
        log_event(state, node, "SUCCESS", "Specification auto-approved")
        return state

    if current_status == "REJECTED":
        log_event(state, node, "WARN", "Specification previously rejected")
        return state

    # Trigger LangGraph interrupt for human-in-the-loop review
    log_event(state, node, "INFO", "Interrupting for human review...")
    resume_val = interrupt({
        "action": "review_specification",
        "task_id": state.get("task_id"),
        "spec_preview": state.get("spec_md", "")[:500],
        "question": "Approve technical specification for autonomous build? [approved: True/False]"
    })

    approved = False
    if isinstance(resume_val, dict):
        approved = bool(resume_val.get("approved", False))
    elif isinstance(resume_val, str):
        approved = resume_val.strip().lower() in ("yes", "y", "approved", "true")
    elif isinstance(resume_val, bool):
        approved = resume_val

    state["approval_status"] = "APPROVED" if approved else "REJECTED"
    log_event(state, node, "SUCCESS" if approved else "WARN", f"Human approval decision: {state['approval_status']}")
    return state


def node_task_router(state: HydramedaState) -> HydramedaState:
    """Node 6: Determines execution path based on approval decision and error state.

    Inputs:  approval_status, error
    Outputs: route ("execute" | "cancel"), status
    """
    node = "task_router"
    if state.get("error") or state.get("status") == "ERROR":
        state["route"] = "cancel"
        state["status"] = "ERROR"
        log_event(state, node, "ERROR", f"Routing errored task to consolidator: {state.get('error')}")
    elif state.get("approval_status") == "APPROVED":
        state["route"] = "execute"
        state["status"] = "IN_PROGRESS"
        log_event(state, node, "INFO", "Routing to worktree provisioner")
    else:
        state["route"] = "cancel"
        state["status"] = "REJECTED"
        log_event(state, node, "WARN", "Routing rejected task to consolidator")
    return state


def node_worktree_builder(state: HydramedaState) -> HydramedaState:
    """Node 7: Provisions an isolated Git worktree branch for safe execution.

    Inputs:  repo_path, task_id
    Outputs: worktree_path
    """
    node = "worktree_builder"
    log_event(state, node, "INFO", "Provisioning isolated Git worktree...")
    try:
        repo_path = state.get("repo_path") or str(HYDRAMEDA_ROOT)
        task_id = state.get("task_id", f"task-{int(time.time())}")
        wt_path = create_isolated_worktree(repo_path, task_id)
        state["worktree_path"] = wt_path
        log_event(state, node, "SUCCESS", f"Isolated worktree ready at {wt_path}")
    except Exception as e:
        log_event(state, node, "ERROR", f"Worktree creation failed: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
    return state


def node_code_builder(state: HydramedaState) -> HydramedaState:
    """Node 8: Autonomous code and test suite builder with deterministic AST import healing.

    Inputs:  optimized_prompt, spec_md, worktree_path
    Outputs: current_files, target_files, repair_count (initialized to 0)
    """
    node = "code_builder"
    log_event(state, node, "INFO", "Generating production code & unit tests via hybrid-coder...")
    try:
        raw_content, used_model = call_llm(ModelRole.CODER.value, state.get("optimized_prompt", ""), max_tokens=8192)

        extracted = extract_files_from_response(raw_content)
        if not extracted:
            raise ValueError(f"Could not parse <file> tags from Builder Output. Raw snippet: {raw_content[:200]}")

        # Deterministic AST import healing
        if "module.py" in extracted:
            extracted["module.py"] = heal_module_imports(extracted["module.py"], is_test_file=False)
        if "test_module.py" in extracted:
            extracted["test_module.py"] = heal_module_imports(
                extracted["test_module.py"],
                is_test_file=True,
                module_code_for_test=extracted.get("module.py", "")
            )

        # Apply changes safely inside isolated worktree via PatchEngine
        wt_str = state.get("worktree_path", "")
        wt = Path(wt_str).resolve() if wt_str and wt_str.strip() else None
        files_written = []
        if wt and wt.exists() and wt != Path.cwd().resolve():
            patch_engine = PatchEngine(target_worktree=wt, allow_delete=False)
            change_items = []
            for fname, content in extracted.items():
                target_file = (wt / fname).resolve()
                op = FileOperation.MODIFY if target_file.exists() else FileOperation.CREATE
                change_items.append({
                    "file_path": fname,
                    "operation": op.value,
                    "proposed_content": content,
                    "authorized": True
                })

            manifest = patch_engine.prepare_manifest(
                changes=change_items,
                description=f"Autonomous build for {state.get('task_id')}",
                task_id=state.get("task_id", "")
            )
            patch_engine.apply_manifest(manifest)
            files_written = manifest.target_files
            state["manifest"] = manifest.to_dict()
            state["manifest_hash"] = manifest.manifest_hash
            state["diff"] = manifest.unified_diff or ""
            log_event(state, node, "INFO", f"Unified diff computed:\n{manifest.unified_diff[:400] if manifest.unified_diff else 'None'}")
        else:
            files_written = list(extracted.keys())
            mem_manifest = ChangeManifest(
                task_id=state.get("task_id", ""),
                description=f"In-memory build for {state.get('task_id')}",
                changes=[
                    FileChange(file_path=fname, operation=FileOperation.CREATE, proposed_content=content, authorized=True)
                    for fname, content in extracted.items()
                ]
            )
            DiffGenerator.generate_manifest_diff(mem_manifest)
            state["manifest"] = mem_manifest.to_dict()
            state["manifest_hash"] = mem_manifest.manifest_hash
            state["diff"] = mem_manifest.unified_diff or ""

        state["current_files"] = extracted
        state["target_files"] = files_written
        state["repair_count"] = 0
        state["retry_count"] = 0
        log_event(state, node, "SUCCESS", f"Built & import-healed artifacts ({used_model}): {files_written}")
    except Exception as e:
        log_event(state, node, "ERROR", f"Code builder failure: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
    return state


def node_sandbox_verifier(state: HydramedaState) -> HydramedaState:
    """Node 9: Executes independent deterministic verification across all 8 checks.

    Enforces: Non-root execution, network isolation, dropped capabilities, resource/timeout limits,
    read-only mounts, restricted tmpfs workspace, no docker socket, safe cleanup.
    Checks: Syntax, AST structural, Import resolution, Ruff, Mypy, Bandit, Pytest, Dependency security.

    Inputs:  worktree_path, target_files, task_id
    Outputs: verification_report, verifier_output, pytest_stderr, verifier_exit_code, status ("PASSED" | "FAILED")
    """
    node = "sandbox_verifier"
    attempt_label = f"Attempt {state.get('repair_count', 0) + 1}/4"
    log_event(state, node, "INFO", f"Running independent deterministic verification ({attempt_label})...")

    # Support mock verifier hook for deterministic unit tests
    if _VERIFIER_HANDLER is not None:
        exit_code, output = _VERIFIER_HANDLER(state)
        state["verifier_exit_code"] = exit_code
        state["verifier_output"] = output
        if exit_code == 0:
            state["pytest_stderr"] = ""
            state["status"] = "PASSED"
            state["verification_report"] = {
                "overall_status": "passed",
                "summary": output,
                "checks": {}
            }
            log_event(state, node, "SUCCESS", "Verification PASSED with 0 errors!")
        else:
            state["pytest_stderr"] = "\n".join(output.splitlines()[-60:])
            state["status"] = "FAILED"
            state["verification_report"] = {
                "overall_status": "failed",
                "summary": output,
                "failure_diagnostics": {"diagnostic": state["pytest_stderr"], "category": "ASSERTION_FAILURE"}
            }
            log_event(state, node, "WARN", f"Verification FAILED (Exit code {exit_code})")
        return state

    wt = Path(state.get("worktree_path", "")).resolve()
    if not wt.exists():
        state["status"] = "FAILED"
        state["verifier_exit_code"] = 1
        state["pytest_stderr"] = f"Worktree path does not exist: {wt}"
        state["verification_report"] = {
            "overall_status": "failed",
            "summary": f"Worktree path does not exist: {wt}",
            "checks": {}
        }
        return state

    pipeline = VerificationPipeline()
    report = pipeline.run_all_checks(worktree_path=wt, task_id=state.get("task_id", "task-verify"))

    state["verification_report"] = report.to_dict()
    state["verifier_output"] = report.summary
    state["verifier_exit_code"] = 0 if report.is_passed() else 1

    if report.is_passed():
        state["pytest_stderr"] = ""
        state["status"] = "PASSED"
        log_event(state, node, "SUCCESS", "All 8 independent verification checks PASSED!")
    else:
        state["status"] = "FAILED"
        diag = report.failure_diagnostics.get("diagnostic", "Verification check failed")
        state["pytest_stderr"] = diag
        state["error_analysis"] = report.failure_diagnostics
        log_event(state, node, "WARN", f"Verification FAILED on check: {report.failure_diagnostics.get('failed_check')}")

    return state


def node_error_analyzer(state: HydramedaState) -> HydramedaState:
    """Node 10: Neuro-symbolic failure diagnosis & root-cause categorization.

    Inputs:  pytest_stderr, verifier_output, current_files, repair_count
    Outputs: error_analysis
    """
    node = "error_analyzer"
    log_event(state, node, "INFO", "Diagnosing verifier failure traceback...")
    stderr = state.get("pytest_stderr", "")
    output = state.get("verifier_output", "")
    combined = (output + "\n" + stderr).lower()

    category = "RUNTIME_ERROR"
    if "syntaxerror" in combined or "indentationerror" in combined:
        category = "SYNTAX_ERROR"
    elif "modulenotfounderror" in combined or "importerror" in combined:
        category = "IMPORT_ERROR"
    elif "mypy" in combined or "incompatible return value" in combined or "arg-type" in combined:
        category = "TYPE_ERROR"
    elif "bandit" in combined or "security" in combined:
        category = "SECURITY_ERROR"
    elif "assertionerror" in combined or "assert" in combined or "failed" in combined:
        category = "ASSERTION_FAILURE"

    failed_tests = re.findall(r"FAILED\s+[^\s:]+::([a-zA-Z0-9_]+)", output)
    guidance_map = {
        "SYNTAX_ERROR": "Fix syntax/indentation errors. Validate proper colon usage and matching parentheses.",
        "IMPORT_ERROR": "Ensure all imported symbols exist in module.py and are exported. Use standard library modules only.",
        "TYPE_ERROR": "Ensure return types and parameter annotations match mypy strict checking. Use typing.Optional/Union/Any where needed.",
        "SECURITY_ERROR": "Avoid hardcoded passwords, insecure hashlib algorithms without salts, and unsafe eval/exec.",
        "ASSERTION_FAILURE": "Carefully trace logic and boundary conditions asserted in the failing unit tests.",
        "RUNTIME_ERROR": "Inspect traceback carefully and eliminate unexpected exceptions or undefined variables."
    }

    existing_diag = state.get("error_analysis") or {}
    if existing_diag.get("category"):
        category = existing_diag["category"]

    analysis = {
        "category": category,
        "summary": existing_diag.get("summary") or f"Detected {category} with {len(failed_tests)} failing test(s)",
        "failed_tests": failed_tests or existing_diag.get("failing_tests", []),
        "diagnostic_snippet": (stderr[:500] if stderr else existing_diag.get("diagnostic", "")[:500]),
        "actionable_guidance": existing_diag.get("actionable_guidance") or guidance_map.get(category, "Fix the identified issues while preserving working logic."),
        "failed_check": existing_diag.get("failed_check", "verifier")
    }

    state["error_analysis"] = analysis
    log_event(state, node, "SUCCESS", f"Categorized failure: {category} ({len(analysis['failed_tests'])} failing tests)")
    return state


def node_repair_agent(state: HydramedaState) -> HydramedaState:
    """Node 11: Targeted code and test repair agent. Increments repair_count (max 3).

    Inputs:  spec_md, current_files, error_analysis, pytest_stderr, repair_count, worktree_path
    Outputs: current_files, target_files, repair_count (incremented)
    """
    node = "repair_agent"
    current_repairs = state.get("repair_count", 0) + 1
    state["repair_count"] = current_repairs
    state["retry_count"] = current_repairs

    log_event(state, node, "INFO", f"Executing self-healing repair cycle ({current_repairs}/3)...")
    try:
        prev_files = state.get("current_files") or {}
        snippets = [f'<file name="{k}">\n{v}</file>' for k, v in prev_files.items()]
        existing_code_context = "\n=== PREVIOUS CODE (PRESERVE PROGRESS; FIX ONLY DETECTED FAILURES) ===\n" + "\n\n".join(snippets) + "\n======================================================================\n"

        analysis = state.get("error_analysis", {})
        guidance = analysis.get("actionable_guidance", "Fix the failing tests.")
        diag_snippet = state.get("pytest_stderr", "")

        prompt = (
            f"You are Hydrameda's Autonomous Repair Agent.\n"
            f"Specification:\n{state.get('spec_md')}\n\n"
            f"REPAIR ATTEMPT {current_repairs}/3:\n"
            f"Error Category: {analysis.get('category', 'UNKNOWN')}\n"
            f"Actionable Guidance: {guidance}\n"
            f"Failing Tests Traceback:\n{diag_snippet}\n"
            f"{existing_code_context}\n"
            "CRITICAL REPAIR RULES:\n"
            "1. Fix the specific failures identified above without breaking working code.\n"
            "2. In `test_module.py`, ALWAYS use direct top-level imports: `from module import ...`.\n"
            "3. Output ONLY raw <file> XML tags without markdown code fences:\n"
            '<file name="module.py">\n# repaired complete python code\n</file>\n\n'
            '<file name="test_module.py">\n# repaired unit tests\n</file>'
        )

        raw_content, used_model = call_llm(ModelRole.REPAIRER.value, prompt, max_tokens=8192)

        extracted = extract_files_from_response(raw_content)
        if not extracted:
            raise ValueError(f"Could not parse repaired <file> tags from Repair Agent. Snippet: {raw_content[:200]}")

        # Merge with existing files
        merged = dict(prev_files)
        merged.update(extracted)

        if "module.py" in merged:
            merged["module.py"] = heal_module_imports(merged["module.py"], is_test_file=False)
        if "test_module.py" in merged:
            merged["test_module.py"] = heal_module_imports(
                merged["test_module.py"],
                is_test_file=True,
                module_code_for_test=merged.get("module.py", "")
            )

        # Apply repairs safely inside isolated worktree via PatchEngine
        wt_str = state.get("worktree_path", "")
        wt = Path(wt_str).resolve() if wt_str and wt_str.strip() else None
        files_written = []
        if wt and wt.exists() and wt != Path.cwd().resolve():
            patch_engine = PatchEngine(target_worktree=wt, allow_delete=False)
            change_items = []
            for fname, content in merged.items():
                target_file = (wt / fname).resolve()
                op = FileOperation.MODIFY if target_file.exists() else FileOperation.CREATE
                change_items.append({
                    "file_path": fname,
                    "operation": op.value,
                    "proposed_content": content,
                    "authorized": True
                })

            manifest = patch_engine.prepare_manifest(
                changes=change_items,
                description=f"Repair cycle {current_repairs} for {state.get('task_id')}",
                task_id=state.get("task_id", "")
            )
            patch_engine.apply_manifest(manifest)
            files_written = manifest.target_files
            state["manifest"] = manifest.to_dict()
            state["manifest_hash"] = manifest.manifest_hash
            state["diff"] = manifest.unified_diff or ""
        else:
            files_written = list(merged.keys())
            mem_manifest = ChangeManifest(
                task_id=state.get("task_id", ""),
                description=f"In-memory repair cycle {current_repairs} for {state.get('task_id')}",
                changes=[
                    FileChange(file_path=fname, operation=FileOperation.CREATE, proposed_content=content, authorized=True)
                    for fname, content in merged.items()
                ]
            )
            DiffGenerator.generate_manifest_diff(mem_manifest)
            state["manifest"] = mem_manifest.to_dict()
            state["manifest_hash"] = mem_manifest.manifest_hash
            state["diff"] = mem_manifest.unified_diff or ""

        state["current_files"] = merged
        state["target_files"] = files_written
        log_event(state, node, "SUCCESS", f"Repaired artifacts ({used_model}, cycle {current_repairs}/3): {files_written}")
    except Exception as e:
        log_event(state, node, "ERROR", f"Repair agent failure: {e}")
        state["error"] = str(e)

    return state


def node_consolidator(state: HydramedaState) -> HydramedaState:
    """Node 12: Memory consolidation, Git auto-commit (on passed verification), and telemetry.

    Enforces:
    1. Human-in-the-loop review via LangGraph interrupt when approval is required.
    2. Cryptographic binding to exact change-set hash (SHA256).
    3. Automatic revocation on stale approvals (when manifest hash mutates).
    4. Destructive operation and main-branch merge authorization.
    5. Rollback on rejection or stale approval.

    Inputs:  status, approval_status, worktree_path, task_id, user_prompt, repair_count, target_files, manifest
    Outputs: commit_hash, final_report, status, consolidation_report, diff, risk_assessment
    """
    node = "consolidator"
    log_event(state, node, "INFO", "Consolidating memory, reports, and version control...")
    commit_hash = "N/A"

    wt_str = state.get("worktree_path")
    wt = Path(wt_str).resolve() if wt_str and wt_str.strip() else None
    main_repo = Path(state.get("repo_path") or str(HYDRAMEDA_ROOT)).resolve()
    manifest_dict = state.get("manifest")

    manifest = ChangeManifest.from_dict(manifest_dict) if manifest_dict else None
    if not manifest and state.get("current_files"):
        manifest = ChangeManifest(
            task_id=state.get("task_id", ""),
            description=f"Fallback manifest for {state.get('task_id')}",
            changes=[
                FileChange(file_path=f, operation=FileOperation.CREATE, proposed_content=c, authorized=True)
                for f, c in state["current_files"].items()
            ]
        )
        DiffGenerator.generate_manifest_diff(manifest)
        state["manifest"] = manifest.to_dict()
        state["manifest_hash"] = manifest.manifest_hash

    is_verified = (state.get("status") == "PASSED" and state.get("verifier_exit_code") == 0)

    if manifest and is_verified:
        risk = GovernanceEngine.assess_risk(manifest, verification_passed=True)
        state["risk_assessment"] = risk.to_dict()

        # Check if human approval gate is required
        existing_decision_dict = state.get("approval_decision")
        needs_interrupt = False
        decision: Optional[ApprovalDecision] = None

        if existing_decision_dict:
            decision = ApprovalDecision.from_dict(existing_decision_dict)
            if decision.approved_manifest_hash != manifest.manifest_hash:
                needs_interrupt = True  # Prior approval is stale!
        elif not state.get("auto_approve", True) or state.get("require_consolidation_approval", False):
            needs_interrupt = True

        if needs_interrupt:
            log_event(state, node, "INFO", "Interrupting for pre-consolidation human review & change approval...")
            review_display = GovernanceEngine.format_review_display(
                spec_md=state.get("spec_md", ""),
                manifest=manifest,
                risk=risk,
                verification_summary=state.get("verification_report")
            )
            resume_val = interrupt({
                "action": "review_and_consolidate",
                "task_id": state.get("task_id"),
                "review_display": review_display,
                "manifest_hash": manifest.manifest_hash,
                "risk_level": risk.level.value,
                "affected_files": manifest.target_files,
                "unified_diff": manifest.unified_diff,
                "verification_passed": is_verified,
                "question": "Approve change consolidation into repository? [approved: True/False, destructive_approved: True/False, merge_approved: True/False]"
            })

            # Parse resumed decision
            if isinstance(resume_val, dict):
                decision = ApprovalDecision(
                    approved=bool(resume_val.get("approved", False)),
                    approved_manifest_hash=resume_val.get("approved_manifest_hash", manifest.manifest_hash),
                    approver=resume_val.get("approver", "human"),
                    destructive_approved=bool(resume_val.get("destructive_approved", False)),
                    merge_approved=bool(resume_val.get("merge_approved", False)),
                    comments=resume_val.get("comments")
                )
            elif isinstance(resume_val, bool):
                decision = ApprovalDecision(
                    approved=resume_val,
                    approved_manifest_hash=manifest.manifest_hash if resume_val else None,
                    approver="human",
                    destructive_approved=state.get("destructive_approved", False),
                    merge_approved=state.get("merge_approved", False)
                )
            elif isinstance(resume_val, str):
                approved_str = resume_val.strip().lower() in ("yes", "y", "approved", "true")
                decision = ApprovalDecision(
                    approved=approved_str,
                    approved_manifest_hash=manifest.manifest_hash if approved_str else None,
                    approver="human",
                    destructive_approved=state.get("destructive_approved", False),
                    merge_approved=state.get("merge_approved", False)
                )
            else:
                decision = ApprovalDecision(approved=False)

        elif not decision:
            # Auto-approve mode: bind decision to current manifest hash
            decision = ApprovalDecision(
                approved=True,
                approved_manifest_hash=manifest.manifest_hash,
                approver="auto",
                destructive_approved=state.get("destructive_approved", True),
                merge_approved=state.get("merge_approved", False)
            )

        state["approval_decision"] = decision.to_dict()

        # Execute safe consolidation
        consolidation_report = SafeConsolidator.consolidate_changes(
            worktree_path=wt or main_repo,
            main_repo_path=main_repo,
            manifest=manifest,
            decision=decision,
            verification_passed=is_verified,
            author="Hydrameda Agent"
        )
        state["consolidation_report"] = consolidation_report.to_dict()
        state["diff"] = consolidation_report.final_diff or ""

        if not consolidation_report.approved:
            state["status"] = consolidation_report.status
            state["approval_status"] = "REJECTED"
            commit_hash = "N/A"
            log_event(state, node, "WARN", f"Consolidation halted: {consolidation_report.summary}")
        else:
            commit_hash = consolidation_report.worktree_commit or "committed"
            log_event(state, node, "SUCCESS", f"Consolidation completed ({consolidation_report.status}): {consolidation_report.summary}")

    else:
        log_event(state, node, "INFO", f"Skipping consolidation: status={state.get('status')}, verified={is_verified}")
        commit_hash = "N/A"

    state["commit_hash"] = commit_hash

    # Generate lesson report
    repairs_used = state.get("repair_count", 0)
    summary_text = (
        f"- Task `{state.get('task_id')}` finished with status **{state.get('status')}** after **{repairs_used}** repair cycles.\n"
        f"- Artifacts: `{', '.join(state.get('target_files', []))}`\n"
        f"- Isolated Worktree: `{state.get('worktree_path', 'none')}`\n"
        f"- Commit: `{commit_hash}`"
    )

    vault_root = Path(os.getenv("OBSIDIAN_VAULT_PATH", str(Path.home() / "ObsidianVault")))
    report_path = vault_root / "Lessons-Learned" / f"{state.get('task_id', 'task')}-report.md"
    report = (
        f"# Hydrameda Task Report: {state.get('task_id')}\n"
        f"- **Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Prompt:** {state.get('user_prompt')}\n"
        f"- **Status:** {state.get('status')}\n"
        f"- **Repair Cycles Used:** {repairs_used}/3\n"
        f"- **Git Commit:** `{commit_hash}`\n\n"
        f"## Summary\n{summary_text}\n"
    )

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        state["final_report"] = report

        rag = ObsidianRAG()
        rag.sync_vault()
        log_event(state, node, "SUCCESS", f"Report written to Obsidian and LanceDB synced")
    except Exception as obs_err:
        log_event(state, node, "WARN", f"Obsidian report sync warning: {obs_err}")

    return state


# ============================================================================
# Conditional Edge Routers
# ============================================================================

def route_after_task_router(state: HydramedaState) -> str:
    """Conditional edge after task_router: routes to execution or cancellation."""
    if not state.get("error") and state.get("approval_status") == "APPROVED" and state.get("route") == "execute":
        return "worktree_builder"
    return "consolidator"


def route_after_verifier(state: HydramedaState) -> str:
    """Conditional edge after sandbox_verifier: routes to success, repair, or max-fail.

    STRICT GUARANTEES:
    1. Verification failure can NEVER enter the success path.
    2. Maximum of 3 repair attempts is strictly enforced (repair_count < 3).
    3. Infinite execution loops are provably impossible.
    """
    if state.get("status") == "PASSED" and state.get("verifier_exit_code") == 0:
        return "consolidator"

    # Verifier failed: inspect repair count
    repairs = state.get("repair_count", 0)
    if repairs < 3:
        return "error_analyzer"

    # Max repair attempts reached: route to consolidator as FAILED
    state["status"] = "FAILED"
    return "consolidator"


# ============================================================================
# Graph Construction
# ============================================================================

def build_hydrameda_graph(checkpointer: Optional[Any] = None, db_path: Optional[Path] = None):
    """Builds and compiles the 12-node cyclic neuro-symbolic LangGraph engine."""
    workflow = StateGraph(HydramedaState)

    # Register all 12 nodes
    workflow.add_node("spec_analyzer", node_spec_analyzer)
    workflow.add_node("rag_retriever", node_rag_retriever)
    workflow.add_node("architect", node_architect)
    workflow.add_node("prompt_optimizer", node_prompt_optimizer)
    workflow.add_node("approval_gate", node_approval_gate)
    workflow.add_node("task_router", node_task_router)
    workflow.add_node("worktree_builder", node_worktree_builder)
    workflow.add_node("code_builder", node_code_builder)
    workflow.add_node("sandbox_verifier", node_sandbox_verifier)
    workflow.add_node("error_analyzer", node_error_analyzer)
    workflow.add_node("repair_agent", node_repair_agent)
    workflow.add_node("consolidator", node_consolidator)

    # Linear planning spine
    workflow.set_entry_point("spec_analyzer")
    workflow.add_edge("spec_analyzer", "rag_retriever")
    workflow.add_edge("rag_retriever", "architect")
    workflow.add_edge("architect", "prompt_optimizer")
    workflow.add_edge("prompt_optimizer", "approval_gate")
    workflow.add_edge("approval_gate", "task_router")

    # Governance conditional edge
    workflow.add_conditional_edges(
        "task_router",
        route_after_task_router,
        {
            "worktree_builder": "worktree_builder",
            "consolidator": "consolidator"
        }
    )

    # Execution spine
    workflow.add_edge("worktree_builder", "code_builder")
    workflow.add_edge("code_builder", "sandbox_verifier")

    # Verification conditional edge (Success / Repair Loop / Max Retries)
    workflow.add_conditional_edges(
        "sandbox_verifier",
        route_after_verifier,
        {
            "consolidator": "consolidator",
            "error_analyzer": "error_analyzer"
        }
    )

    # Cyclic self-healing loop
    workflow.add_edge("error_analyzer", "repair_agent")
    workflow.add_edge("repair_agent", "sandbox_verifier")

    # Terminal edge
    workflow.add_edge("consolidator", END)

    # Default persistent SQLite checkpointer
    if checkpointer is None:
        target_db = db_path or (HYDRAMEDA_ROOT / "state.db")
        conn = sqlite3.connect(str(target_db), check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    return workflow.compile(checkpointer=checkpointer)


# ============================================================================
# CLI Entrypoint
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hydrameda Autonomous LangGraph Engine")
    parser.add_argument("positional_prompt", nargs="?", default=None, help="Task prompt")
    parser.add_argument("--prompt", "-p", type=str, default=None, help="Task prompt (named flag)")
    parser.add_argument("--repo", "-r", type=str, default=str(HYDRAMEDA_ROOT), help="Target repository path")
    parser.add_argument("--task-id", "-t", type=str, default=None, help="Explicit task ID")
    parser.add_argument("--auto-approve", action="store_true", default=True, help="Auto-approve specification (default: True)")
    parser.add_argument("--interactive", action="store_true", default=False, help="Enable human review interrupt gate")
    args = parser.parse_args()

    prompt_arg = args.prompt or args.positional_prompt or "Create a JWT token validator with unit tests"
    slug = re.sub(r"[^a-z0-9]+", "-", prompt_arg.lower()).strip("-")[:24].rstrip("-")
    task_id = args.task_id or f"task-{slug}-{int(time.time()) % 10000}"
    auto_approve = False if args.interactive else args.auto_approve

    print(f"🚀 Starting Hydrameda Neuro-Symbolic Engine for task: '{task_id}' (auto_approve={auto_approve})")
    app = build_hydrameda_graph()

    initial_state: HydramedaState = {
        "task_id": task_id,
        "user_prompt": prompt_arg,
        "repo_path": args.repo,
        "auto_approve": auto_approve,
        "approval_status": "PENDING",
        "route": "execute",
        "task_analysis": {},
        "vault_context": "",
        "ast_skeleton": "",
        "spec_md": "",
        "optimized_prompt": "",
        "worktree_path": "",
        "target_files": [],
        "current_files": {},
        "verifier_output": "",
        "pytest_stderr": "",
        "verifier_exit_code": 0,
        "repair_count": 0,
        "retry_count": 0,
        "error_analysis": {},
        "status": "PENDING",
        "commit_hash": "N/A",
        "final_report": "",
        "structured_logs": [],
        "error": ""
    }

    config = {"configurable": {"thread_id": task_id}}
    result_state = app.invoke(initial_state, config)

    # Handle interactive interrupt if triggered
    snapshot = app.get_state(config)
    if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
        print("\n" + "=" * 60)
        print("📋 ARCHITECTURAL SPECIFICATION REVIEW")
        print("=" * 60)
        spec_preview = snapshot.values.get("spec_md", "")
        print(spec_preview[:1200] + ("\n... [truncated]" if len(spec_preview) > 1200 else ""))
        print("=" * 60)
        choice = input("\nApprove specification and proceed to build? [y/N]: ").strip().lower()
        is_approved = choice in ("y", "yes")
        result_state = app.invoke(Command(resume={"approved": is_approved}), config)

    final_status = result_state.get("status", "UNKNOWN")
    print(f"\n🏁 Hydrameda pipeline execution completed with status: {final_status}")
    sys.exit(0 if final_status == "PASSED" else 1)
