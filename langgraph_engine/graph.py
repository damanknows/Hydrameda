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
import logging
import os
import re
import sqlite3

logger = logging.getLogger(__name__)
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from preprocessor import ObsidianRAG, create_isolated_worktree, extract_ast_skeleton, validate_task_id

try:
    from fidelity import (
        TaskRequirements,
        extract_task_requirements,
        validate_specification_fidelity,
    )
except ImportError:
    from langgraph_engine.fidelity import (
        TaskRequirements,
        extract_task_requirements,
        validate_specification_fidelity,
    )

# Code Engine, HydraRouter, and Verification Engine integrations
try:
    from code_engine import (
        ApprovalDecision,
        ChangeManifest,
        DiffGenerator,
        FileChange,
        FileOperation,
        FileSelector,
        GovernanceEngine,
        PatchEngine,
        RepositoryMapper,
        SafeConsolidator,
    )
    from hydra_router import ModelRole, get_router
    from verification_engine import VerificationPipeline
except ImportError:
    # Handle sys.path when running graph.py standalone
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from code_engine import (
        ApprovalDecision,
        ChangeManifest,
        DiffGenerator,
        FileChange,
        FileOperation,
        FileSelector,
        GovernanceEngine,
        PatchEngine,
        RepositoryMapper,
        SafeConsolidator,
    )
    from hydra_router import ModelRole, get_router
    from verification_engine import VerificationPipeline

# Strip proxy variables that interfere with local endpoints
for env_var in (
    'OPENAI_BASE_URL', 'OPENAI_API_BASE',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy'
):
    os.environ.pop(env_var, None)

HYDRAMEDA_ROOT = Path(os.getenv("HYDRAMEDA_ROOT", Path.home() / "hydrameda")).resolve()


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
        if re.search(rf"\b{mod}\.", code) and not re.search(
            rf"^\s*(?:import\s+.*\b{mod}\b|from\s+{mod}\s+import)", code, re.MULTILINE
        ):
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
        except (SyntaxError, ValueError) as exc:
            logger.debug("Import auto-injection parse error: %s", exc)

    if not missing_lines:
        return code

    lines_out = code.splitlines()
    insert_idx = 0
    for i, line in enumerate(lines_out):
        if line.strip().startswith("from __future__ import"):
            insert_idx = i + 1
    lines_out[insert_idx:insert_idx] = missing_lines
    return "\n".join(lines_out) + "\n"


def extract_files_from_response(raw_text: str) -> dict[str, str]:
    """Extracts code blocks tagged with <file name="...">...</file> from LLM output."""
    pattern = re.compile(r'<file\s+name=["\']([^"\']+)["\']\s*>(.*?)(?:</file>|$)', re.DOTALL)
    matches = pattern.findall(raw_text)
    extracted: dict[str, str] = {}
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
# Normalized Task States & Strongly-Structured Shared State (Requirement B1)
# ============================================================================

class TaskState(str, Enum):
    """Normalized task lifecycle states across Hydrameda."""
    CREATED = "CREATED"
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    REPAIRING = "REPAIRING"
    CONSOLIDATING = "CONSOLIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HydramedaState(TypedDict, total=False):
    # Core identifiers & inputs
    task_id: str
    user_prompt: str
    repo_path: str
    auto_approve: bool
    trusted_policy: str        # Explicit trusted policy authorization ("ci_automation", "eval_harness", etc.)
    starting_commit: str

    # Task lifecycle state (Requirement B1)
    task_state: str            # TaskState enum value

    # Node 1 & 2 outputs: analysis & context
    task_analysis: dict[str, Any]
    vault_context: str
    ast_skeleton: str

    # Node 3 & 4 outputs: spec & optimized prompt
    spec_md: str
    optimized_prompt: str

    # Specification fidelity & constraint enforcement (Fidelity stage)
    task_requirements: dict[str, Any]
    spec_validation_attempts: int
    specification_fidelity_status: str       # "PASSED" | "FAILED"
    specification_constraint_violations: list[str]
    specification_rejection_reason: str
    fidelity_feedback: str
    original_requirements_preserved: bool

    # Node 5 & 6 outputs: governance & routing
    approval_status: str       # "PENDING" | "APPROVED" | "REJECTED"
    approval_mode: str         # "trusted_policy", "interactive_interrupt", "auto_approved"
    authorization_source: str
    route: str                 # "execute" | "cancel"

    # Node 7 & 8 outputs: worktree & files
    worktree_path: str
    target_files: list[str]
    current_files: dict[str, str]

    # Node 9 output: verification results
    verifier_output: str
    pytest_stderr: str
    verifier_exit_code: int
    verification_report: dict[str, Any]

    # Neuro-symbolic repair loop tracking (Max 3 repairs)
    repair_count: int
    retry_count: int           # Alias for backward compatibility
    error_analysis: dict[str, Any]

    # Final outputs & telemetry
    status: str                # "PENDING" | "IN_PROGRESS" | "PASSED" | "FAILED" | "REJECTED" | "ERROR"
    commit_hash: str
    final_report: str
    final_report_json: dict[str, Any]
    execution_report: dict[str, Any]
    structured_logs: list[dict[str, Any]]
    error: str

    # Governance, Manifest, and Consolidation
    manifest: dict[str, Any]
    manifest_hash: str
    diff: str
    risk_assessment: dict[str, Any]
    approval_decision: dict[str, Any]
    specification_approval_status: str
    specification_approved_at: float
    consolidation_status: str
    consolidation_skip_reason: str
    destructive_approved: bool
    merge_approved: bool
    require_consolidation_approval: bool
    consolidation_report: dict[str, Any]
    start_time: float
    end_time: float



def log_event(state: HydramedaState, node: str, level: str, message: str, metadata: dict[str, Any] | None = None) -> None:
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
    Outputs: task_analysis, ast_skeleton, starting_commit, task_state
    """
    node = "spec_analyzer"
    state["task_state"] = TaskState.ANALYZING.value
    if not state.get("start_time"):
        state["start_time"] = time.time()

    log_event(state, node, "INFO", f"Analyzing task specification for '{state.get('task_id')}'...")
    try:
        prompt = state.get("user_prompt", "").strip()
        if not prompt:
            raise ValueError("user_prompt cannot be empty")

        repo_path = state.get("repo_path") or str(HYDRAMEDA_ROOT)
        ast_map = extract_ast_skeleton(repo_path)

        # Retrieve starting commit SHA
        starting_commit = "N/A"
        try:
            r = subprocess.run(["git", "-C", repo_path, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)
            if r.returncode == 0 and r.stdout.strip():
                starting_commit = r.stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("Git rev-parse failed: %s", exc)
        state["starting_commit"] = starting_commit

        # Dynamic repository mapping (Requirement B3)
        relevant_files = []
        existing_tests = []
        detected_deps = set()
        repo_files_count = 0
        try:
            repo_p = Path(repo_path).resolve()
            if repo_p.exists():
                mapper = RepositoryMapper(repo_p)
                all_files = mapper.list_files()
                repo_files_count = len(all_files)
                existing_tests = [f for f in all_files if "test" in f.lower()][:5]
                selector = FileSelector(mapper)
                scored = selector.select_relevant_files(prompt, top_k=5)
                relevant_files = [s["file"] for s in scored]
                for s in scored:
                    for d in s.get("symbols", {}).get("dependencies", []):
                        detected_deps.add(d)
        except Exception as repo_err:  # noqa: BLE001 - graceful fallback on arbitrary repository mapper failure
            log_event(state, node, "WARN", f"Repository mapping fallback: {repo_err}")

        # Extract symbols and intent keywords
        keywords = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", prompt)
        analysis = {
            "intent": prompt[:120],
            "keywords": keywords[:15],
            "target_files": relevant_files if relevant_files else ["module.py", "test_module.py"],
            "relevant_files": relevant_files,
            "existing_tests": existing_tests,
            "detected_dependencies": sorted(detected_deps),
            "repository_files_count": repo_files_count,
            "constraints": [
                "pure python standard library only" if not detected_deps else "use detected project dependencies",
                "full type hints",
                "pytest test suite with top-level imports",
                "no NotImplementedError stubs"
            ]
        }

        state["task_analysis"] = analysis
        state["ast_skeleton"] = ast_map

        # Structured task requirement extraction (Fidelity stage)
        reqs = extract_task_requirements(prompt, analysis)
        state["task_requirements"] = reqs.to_dict()

        log_event(state, node, "SUCCESS", f"Task analyzed ({len(keywords)} keywords, {repo_files_count} repo files, AST map {len(ast_map)} chars)")
    except Exception as e:  # noqa: BLE001 - Node boundary catches unexpected execution errors to ensure robust failure handling
        log_event(state, node, "ERROR", f"Failed to analyze spec: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
        state["task_state"] = TaskState.FAILED.value
    return state


def node_rag_retriever(state: HydramedaState) -> HydramedaState:
    """Node 2: Queries Obsidian Vault via LanceDB for architectural invariants.

    Inputs:  user_prompt, task_analysis
    Outputs: vault_context
    """
    node = "rag_retriever"
    state["task_state"] = TaskState.ANALYZING.value
    log_event(state, node, "INFO", "Querying Obsidian Vault memory & LanceDB embeddings...")
    try:
        rag = ObsidianRAG()
        query = state.get("user_prompt", "")
        vault_hits = rag.query(query, top_k=3)
        state["vault_context"] = vault_hits
        log_event(state, node, "SUCCESS", f"Retrieved Obsidian RAG context ({len(vault_hits)} chars)")
    except Exception as e:  # noqa: BLE001 - graceful fallback on vault query failure
        log_event(state, node, "WARN", f"Obsidian RAG retrieval warning (using empty context): {e}")
        state["vault_context"] = ""
    return state


def node_architect(state: HydramedaState) -> HydramedaState:
    """Node 3: Generates formal Markdown technical specification.

    Inputs:  user_prompt, task_analysis, vault_context, ast_skeleton, task_id, task_requirements, fidelity_feedback
    Outputs: spec_md
    """
    node = "architect"
    state["task_state"] = TaskState.PLANNING.value
    state.setdefault("spec_validation_attempts", 0)
    log_event(state, node, "INFO", "Synthesizing architectural specification via hybrid-architect...")
    try:
        reqs = TaskRequirements.from_dict(state.get("task_requirements"))
        prohibited = reqs.prohibited_capabilities
        prohibitions_str = ""
        if prohibited:
            prohibitions_str = "STRICT PROHIBITION DIRECTIVE: Do NOT implement or introduce:\n" + "\n".join(f" - {p}" for p in prohibited) + "\nSatisfy ONLY the requested capabilities.\n"

        feedback_str = ""
        if state.get("fidelity_feedback"):
            feedback_str = (
                f"PREVIOUS ATTEMPT REJECTED DUE TO CONSTRAINT VIOLATIONS:\n"
                f"{state.get('fidelity_feedback')}\n"
                "You MUST eliminate all violations listed above.\n\n"
            )

        user_prompt_lower = (state.get("user_prompt", "")).lower()
        all_candidate_libs = ["flask", "yaml", "pyyaml", "cryptography", "requests", "fastapi", "jwt"]
        candidate_libs = [lib for lib in all_candidate_libs if not any(lib in p.lower() for p in prohibited)]

        if any(lib in user_prompt_lower for lib in candidate_libs):
            lib_constraint = "1. Rely on Python standard library and explicitly requested packages (e.g. Flask, PyYAML, cryptography, pytest). Avoid unrequested external dependencies.\n"
        else:
            lib_constraint = "1. Use ONLY Python standard library modules (hashlib, hmac, os, secrets, base64, json, time, re, typing, dataclasses) and pytest.\n"

        # Repository-aware context injection (Requirement B3.6)
        repo_ctx_lines = []
        analysis = state.get("task_analysis", {})
        if analysis.get("relevant_files"):
            repo_ctx_lines.append(f"Relevant Existing Repository Files: {analysis.get('relevant_files')}")
        if analysis.get("existing_tests"):
            repo_ctx_lines.append(f"Existing Tests in Repository: {analysis.get('existing_tests')}")
        if analysis.get("detected_dependencies"):
            repo_ctx_lines.append(f"Detected Dependencies: {analysis.get('detected_dependencies')}")
        repo_context_str = ("\n" + "\n".join(repo_ctx_lines)) if repo_ctx_lines else ""

        prompt = (
            f"You are Hydrameda's Epistemic Architect.\n"
            f"{feedback_str}"
            f"User Prompt: {state.get('user_prompt')}\n\n"
            f"Relevant Obsidian Notes:\n{state.get('vault_context')}\n\n"
            f"Codebase AST Map:\n{state.get('ast_skeleton')}\n\n"
            f"Repository Context:{repo_context_str}\n\n"
            "Write a concise technical specification (in Markdown) defining required functions/classes and unit tests.\n"
            "STRICT DESIGN CONSTRAINTS:\n"
            "0. PRIMARY DIRECTIVE: Strictly satisfy the User Prompt. Background notes and vault context are reference material ONLY; do NOT substitute, expand, or alter the user's requested scope, protocol, or design based on background notes.\n"
            f"{prohibitions_str}"
            f"{lib_constraint}"
            "2. Raise ONLY built-in `ValueError` or `TypeError` (or custom exceptions that subclass `ValueError`).\n"
            "3. Do NOT write `raise NotImplementedError` stubs in your specification."
        )
        spec, used_model = call_llm(ModelRole.ARCHITECT.value, prompt, max_tokens=4096)

        if not spec:
            spec = f"# Spec Generation Failed for {state.get('task_id')}"

        vault_path_env = os.getenv("OBSIDIAN_VAULT_PATH", str(Path.home() / "ObsidianVault"))
        spec_path = Path(vault_path_env) / "Specs" / f"{state.get('task_id', 'task')}.md"
        try:
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(spec, encoding="utf-8")
        except OSError as write_err:
            log_event(state, node, "WARN", f"Could not write spec to vault: {write_err}")

        state["spec_md"] = spec
        log_event(state, node, "SUCCESS", f"Specification generated ({used_model}, {len(spec)} chars)")
    except Exception as e:  # noqa: BLE001 - Node boundary catches unexpected execution errors to ensure robust failure handling
        log_event(state, node, "ERROR", f"Architect failure: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
        state["task_state"] = TaskState.FAILED.value
        state["spec_md"] = "# Architectural Synthesis Failed"
    return state


def node_spec_validator(state: HydramedaState) -> HydramedaState:
    """Node 3.5: Deterministic specification fidelity validation stage.

    Validates that the generated architectural specification complies strictly
    with user intent, negative constraints, and scope boundaries before reaching
    the human approval gate or code builder. Bounded to a maximum of 3 attempts.

    Inputs:  user_prompt, spec_md, task_requirements, vault_context, spec_validation_attempts
    Outputs: spec_validation_attempts, specification_fidelity_status,
             specification_constraint_violations, specification_rejection_reason,
             fidelity_feedback, original_requirements_preserved
    """
    node = "spec_validator"
    attempts = state.get("spec_validation_attempts", 0) + 1
    state["spec_validation_attempts"] = attempts

    log_event(state, node, "INFO", f"Validating specification fidelity (attempt {attempts}/3)...")
    prompt = state.get("user_prompt", "")
    spec = state.get("spec_md", "")
    reqs = TaskRequirements.from_dict(state.get("task_requirements"))
    vault_ctx = state.get("vault_context", "")

    report = validate_specification_fidelity(
        user_prompt=prompt,
        spec_md=spec,
        task_requirements=reqs,
        vault_context=vault_ctx
    )

    if report.passed:
        state["specification_fidelity_status"] = "PASSED"
        state["specification_constraint_violations"] = []
        state["specification_rejection_reason"] = ""
        state["fidelity_feedback"] = ""
        state["original_requirements_preserved"] = True
        log_event(state, node, "SUCCESS", f"Specification fidelity verified on attempt {attempts}/3.")
    else:
        state["specification_fidelity_status"] = "FAILED"
        state["specification_constraint_violations"] = report.violations
        state["specification_rejection_reason"] = "; ".join(report.violations)
        state["fidelity_feedback"] = report.actionable_feedback
        state["original_requirements_preserved"] = False
        log_event(state, node, "WARN", f"Specification fidelity rejected (attempt {attempts}/3): {'; '.join(report.violations)}")

        if attempts >= 3:
            state["task_state"] = TaskState.FAILED.value
            state["status"] = "FAILED"
            state["error"] = f"Specification fidelity validation failed after 3 attempts: {state['specification_rejection_reason']}"
            log_event(state, node, "ERROR", "Max specification validation attempts reached (3/3). Halting execution.")

    return state


def route_after_spec_validator(state: HydramedaState) -> str:
    """Conditional edge after spec_validator: routes to prompt_optimizer, architect retry, or consolidator halt."""
    if state.get("specification_fidelity_status") == "PASSED":
        return "prompt_optimizer"

    attempts = state.get("spec_validation_attempts", 0)
    if attempts < 3:
        return "architect"

    # Max attempts reached: halt execution and route directly to consolidator (skip approval gate & code builder)
    return "consolidator"


def node_prompt_optimizer(state: HydramedaState) -> HydramedaState:
    """Node 4: Compiles and optimizes the code generation prompt with epistemic rules.

    Inputs:  spec_md, task_analysis, ast_skeleton, task_requirements
    Outputs: optimized_prompt
    """
    node = "prompt_optimizer"
    state["task_state"] = TaskState.PLANNING.value
    log_event(state, node, "INFO", "Optimizing code-generation prompt with epistemic rules...")
    try:
        spec = state.get("spec_md", "")
        prompt_lower = (state.get("user_prompt", "") + " " + spec).lower()
        reqs = TaskRequirements.from_dict(state.get("task_requirements"))
        prohibited = reqs.prohibited_capabilities
        all_candidate_libs = ["flask", "yaml", "pyyaml", "cryptography", "requests", "fastapi", "jwt"]
        candidate_libs = [lib for lib in all_candidate_libs if not any(lib in p.lower() for p in prohibited)]

        if any(lib in prompt_lower for lib in candidate_libs):
            lib_rule = "2. Rely on Python standard library and explicitly requested packages (e.g. Flask, PyYAML, cryptography, pytest). Avoid unrequested external dependencies.\n"
        else:
            lib_rule = "2. Prefer Python standard library modules (hashlib, hmac, os, secrets, base64, json, time, re, typing, dataclasses) and pytest.\n"

        prohibitions_rule = ""
        if prohibited:
            prohibitions_rule = "CRITICAL PROHIBITIONS (DO NOT IMPLEMENT):\n" + "\n".join(f"- {p}" for p in prohibited) + "\n"

        prompt = (
            f"You are Hydrameda's Autonomous Repository Coder.\n"
            f"Specification:\n{spec}\n\n"
            "CRITICAL RULES FOR REPOSITORY CODE & TESTS:\n"
            "1. Write COMPLETE, FULLY IMPLEMENTED production code. NEVER leave `raise NotImplementedError` or `pass` stubs!\n"
            f"{lib_rule}"
            f"{prohibitions_rule}"
            "3. Multi-file generation is fully supported. Specify each created or modified file with its clean relative path in <file name=\"...\"> tags.\n"
            "4. For tests, ensure all imports of local project modules use top-level or clean package imports.\n"
            "5. Output ONLY raw <file> XML tags without markdown code fences:\n"
            '<file name="relative/path/to/file.py">\n# complete working python code here\n</file>\n\n'
            '<file name="tests/test_file.py">\n# complete pytest unit tests here\n</file>'
        )
        state["optimized_prompt"] = prompt
        log_event(state, node, "SUCCESS", f"Prompt optimized ({len(prompt)} chars)")
    except Exception as e:  # noqa: BLE001 - Node boundary catches unexpected execution errors to ensure robust failure handling
        log_event(state, node, "ERROR", f"Prompt optimizer failure: {e}")
        state["error"] = str(e)
        state["status"] = "ERROR"
        state["task_state"] = TaskState.FAILED.value
    return state


def node_approval_gate(state: HydramedaState) -> HydramedaState:
    """Node 5: Human-in-the-loop governance gate.

    Supports resumable human review via LangGraph interrupt/Command(resume=...).
    Enforces explicit trusted execution policy for automated approvals.

    Inputs:  auto_approve, trusted_policy, approval_status, spec_md, task_id
    Outputs: approval_status ("APPROVED" | "REJECTED"), task_state
    """
    node = "approval_gate"
    current_status = state.get("approval_status")
    auto_approve = state.get("auto_approve", False)

    if current_status == "REJECTED":
        log_event(state, node, "WARN", "Specification previously rejected")
        state["task_state"] = TaskState.CANCELLED.value
        return state

    if current_status == "APPROVED":
        state["task_state"] = TaskState.PLANNING.value
        return state

    # Requirement A2.5/A2.6: Auto-approval requires an explicit trusted execution policy
    ALLOWED_TRUSTED_POLICIES = {"ci_automation", "eval_harness", "internal_trusted", "batch_backlog", "cli_authorized"}
    trusted_policy = state.get("trusted_policy") or os.getenv("HYDRAMEDA_TRUSTED_POLICY")

    if auto_approve:
        if trusted_policy in ALLOWED_TRUSTED_POLICIES:
            state["approval_status"] = "APPROVED"
            state["specification_approval_status"] = "APPROVED"
            state["specification_approved_at"] = time.time()
            state["task_state"] = TaskState.PLANNING.value
            state["approval_mode"] = "auto_approved"
            state["authorization_source"] = f"policy:{trusted_policy}"
            log_event(state, node, "SUCCESS", f"Specification auto-approved under trusted policy: '{trusted_policy}'")
            return state
        else:
            log_event(state, node, "WARN", f"Untrusted auto-approval attempt rejected (trusted_policy='{trusted_policy}'). Awaiting human review.")

    # Trigger LangGraph interrupt for human-in-the-loop review
    state["task_state"] = TaskState.AWAITING_APPROVAL.value
    log_event(state, node, "INFO", "Interrupting for human review...")
    resume_val = interrupt({
        "action": "review_specification",
        "task_id": state.get("task_id"),
        "spec_preview": state.get("spec_md", "")[:500],
        "question": "Approve technical specification for autonomous build? [approved: True/False]"
    })

    # Requirement A1.1/A1.2: Require actual boolean True; reject strings like "yes", "true", "approved"
    approved = False
    if isinstance(resume_val, dict):
        raw_approved = resume_val.get("approved")
        res_task_id = resume_val.get("task_id")
        if raw_approved is True:
            if res_task_id and res_task_id != state.get("task_id"):
                approved = False
                log_event(state, node, "ERROR", f"Task ID mismatch in specification approval: {res_task_id} != {state.get('task_id')}")
            else:
                approved = True
        else:
            approved = False
    elif isinstance(resume_val, bool) and resume_val is True:
        approved = True
    else:
        approved = False

    status_str = "APPROVED" if approved else "REJECTED"
    state["approval_status"] = status_str
    state["specification_approval_status"] = status_str
    state["specification_approved_at"] = time.time()
    state["task_state"] = TaskState.PLANNING.value if approved else TaskState.CANCELLED.value
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
    except Exception as e:  # noqa: BLE001 - Node boundary catches unexpected execution errors to ensure robust failure handling
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
    except Exception as e:  # noqa: BLE001 - Node boundary catches unexpected execution errors to ensure robust failure handling
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
    report = pipeline.run_all_checks(
        worktree_path=wt,
        task_id=state.get("task_id", "task-verify"),
        target_files=state.get("target_files")
    )

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

        reqs = TaskRequirements.from_dict(state.get("task_requirements"))
        prohibited = reqs.prohibited_capabilities
        prohibitions_rule = ""
        if prohibited:
            prohibitions_rule = "CRITICAL PROHIBITIONS (DO NOT INTRODUCE DURING REPAIR):\n" + "\n".join(f"- {p}" for p in prohibited) + "\n"

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
            f"{prohibitions_rule}"
            "3. NEVER create stub or mock files for external or framework libraries (e.g. litellm.py, langgraph/, tree_sitter_language_pack/). Repair ONLY the target task code.\n"
            "4. Output ONLY raw <file> XML tags without markdown code fences:\n"
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
        log_event(state, node, "INFO", f"Applied repair patch ({used_model}, cycle {current_repairs}/3): {files_written}. Awaiting verification...")
    except Exception as e:  # noqa: BLE001 - Node boundary catches unexpected execution errors to ensure robust failure handling
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
    decision: ApprovalDecision | None = None
    existing_decision_dict = state.get("approval_decision")
    if existing_decision_dict:
        decision = ApprovalDecision.from_dict(existing_decision_dict)

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
        needs_interrupt = False
        if decision:
            if decision.approved_manifest_hash != manifest.manifest_hash:
                needs_interrupt = True  # Prior approval is stale!
        elif not state.get("auto_approve", False) or state.get("require_consolidation_approval", False):
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

            # Parse resumed decision - NEVER interpret missing approval data as consent
            if isinstance(resume_val, dict):
                raw_approved = resume_val.get("approved")
                supplied_hash = resume_val.get("approved_manifest_hash")
                res_task_id = resume_val.get("task_id") or state.get("task_id")

                if raw_approved is True and isinstance(supplied_hash, str) and supplied_hash.strip():
                    decision = ApprovalDecision(
                        approved=True,
                        approved_manifest_hash=supplied_hash.strip(),
                        task_id=res_task_id,
                        approver=resume_val.get("approver", "human"),
                        approval_mode=resume_val.get("approval_mode", "interactive_interrupt"),
                        authorization_source=resume_val.get("authorization_source", "user_prompt"),
                        destructive_approved=bool(resume_val.get("destructive_approved", False)),
                        merge_approved=bool(resume_val.get("merge_approved", False)),
                        comments=resume_val.get("comments")
                    )
                else:
                    reason = "Approval was not boolean True" if raw_approved is not True else "Missing explicit approved_manifest_hash"
                    decision = ApprovalDecision(
                        approved=False,
                        approved_manifest_hash=supplied_hash if isinstance(supplied_hash, str) else None,
                        task_id=res_task_id,
                        approver=resume_val.get("approver", "human") if isinstance(resume_val.get("approver"), str) else "human",
                        comments=f"Approval rejected: {reason}"
                    )
            else:
                decision = ApprovalDecision(
                    approved=False,
                    approved_manifest_hash=None,
                    task_id=state.get("task_id", ""),
                    approver="human",
                    comments="Approval rejected: resume payload must be a dict with boolean 'approved' and explicit 'approved_manifest_hash'"
                )

        elif not decision:
            # Trusted automation mode (explicitly enabled under authorized policy)
            trusted_policy = state.get("trusted_policy") or os.getenv("HYDRAMEDA_TRUSTED_POLICY")
            ALLOWED_TRUSTED_POLICIES = {"ci_automation", "eval_harness", "internal_trusted", "batch_backlog", "cli_authorized"}
            if state.get("auto_approve") and trusted_policy in ALLOWED_TRUSTED_POLICIES:
                decision = ApprovalDecision(
                    approved=True,
                    approved_manifest_hash=manifest.manifest_hash,
                    task_id=manifest.task_id,
                    approver="trusted_automation",
                    approval_mode="trusted_policy",
                    authorization_source=f"policy:{trusted_policy}",
                    destructive_approved=bool(state.get("destructive_approved", False)),
                    merge_approved=bool(state.get("merge_approved", False))
                )
            else:
                decision = ApprovalDecision(
                    approved=False,
                    approved_manifest_hash=None,
                    task_id=manifest.task_id,
                    approver="untrusted",
                    comments="Auto-approval rejected: no authorized trusted policy"
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
            state["consolidation_status"] = "HALTED"
            state["consolidation_skip_reason"] = consolidation_report.summary
            state["status"] = consolidation_report.status
            state["approval_status"] = "REJECTED"
            state["task_state"] = TaskState.CANCELLED.value if consolidation_report.status in ("REJECTED", "CANCELLED") else TaskState.FAILED.value
            commit_hash = "N/A"
            log_event(state, node, "WARN", f"Consolidation halted: {consolidation_report.summary}")
        else:
            state["consolidation_status"] = "COMPLETED"
            commit_hash = consolidation_report.worktree_commit or "committed"
            state["task_state"] = TaskState.COMPLETED.value
            log_event(state, node, "SUCCESS", f"Consolidation completed ({consolidation_report.status}): {consolidation_report.summary}")

    else:
        consolidation_status = "NOT_REACHED"
        consolidation_skip_reason = f"Verification did not pass (status={state.get('status')}, verified={is_verified})"
        state["consolidation_status"] = consolidation_status
        state["consolidation_skip_reason"] = consolidation_skip_reason
        log_event(state, node, "INFO", f"Skipping consolidation: status={state.get('status')}, verified={is_verified}")
        commit_hash = "N/A"
        if state.get("status") in ("REJECTED", "CANCELLED"):
            state["task_state"] = TaskState.CANCELLED.value
        else:
            state["task_state"] = TaskState.FAILED.value

    state["commit_hash"] = commit_hash

    # Invariant B1.10: Ensure graph state cannot claim COMPLETED while approval or verification remains pending
    if state.get("task_state") == TaskState.COMPLETED.value and (
        not is_verified or state.get("status") != "PASSED" or not (decision and decision.approved)
    ):
        state["task_state"] = TaskState.FAILED.value

    # Requirement B4: Comprehensive structured execution report
    files_created = []
    files_modified = []
    files_deleted = []
    if manifest:
        for ch in manifest.changes:
            if ch.operation == FileOperation.CREATE:
                files_created.append(ch.file_path)
            elif ch.operation == FileOperation.MODIFY:
                files_modified.append(ch.file_path)
            elif ch.operation == FileOperation.DELETE:
                files_deleted.append(ch.file_path)
    elif state.get("target_files"):
        files_created = list(state.get("target_files", []))

    v_report = state.get("verification_report") or {}
    checks_summary = {}
    if isinstance(v_report.get("checks"), dict):
        for cname, cdata in v_report["checks"].items():
            checks_summary[cname] = cdata.get("status") if isinstance(cdata, dict) else str(cdata)

    start_t = state.get("start_time", time.time())
    end_t = time.time()
    state["end_time"] = end_t
    duration_s = round(end_t - start_t, 2)

    exec_report = {
        "task_id": state.get("task_id", ""),
        "task_state": state.get("task_state", TaskState.FAILED.value),
        "status": state.get("status", "UNKNOWN"),
        "specification_fidelity_status": state.get("specification_fidelity_status", "NOT_EVALUATED"),
        "specification_validation_attempts": state.get("spec_validation_attempts", 0),
        "specification_constraint_violations": state.get("specification_constraint_violations", []),
        "specification_rejection_reason": state.get("specification_rejection_reason", ""),
        "original_requirements_preserved": state.get("original_requirements_preserved", True),
        "specification_approval_status": state.get("specification_approval_status") or state.get("approval_status"),
        "consolidation_status": state.get("consolidation_status", "COMPLETED" if (decision and decision.approved) else "HALTED"),
        "consolidation_skip_reason": state.get("consolidation_skip_reason"),
        "repo_path": state.get("repo_path") or str(HYDRAMEDA_ROOT),
        "starting_commit": state.get("starting_commit", "N/A"),
        "final_commit": commit_hash,
        "files_created": files_created,
        "files_modified": files_modified,
        "files_deleted": files_deleted,
        "verification_checks": checks_summary,
        "sandbox_config": v_report.get("sandbox_config", {}),
        "approval_decision": state.get("approval_decision"),
        "repair_cycles_used": state.get("repair_count", 0),
        "duration_seconds": duration_s,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    state["execution_report"] = exec_report
    state["final_report_json"] = exec_report

    # Generate lesson report
    repairs_used = state.get("repair_count", 0)
    summary_text = (
        f"- Task `{state.get('task_id')}` finished with state **{state.get('task_state')}** (status: **{state.get('status')}**) after **{repairs_used}** repair cycles.\n"
        f"- Artifacts: `{', '.join(state.get('target_files', []))}`\n"
        f"- Isolated Worktree: `{state.get('worktree_path', 'none')}`\n"
        f"- Commit: `{commit_hash}`\n"
        f"- Duration: {duration_s}s"
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
        log_event(state, node, "SUCCESS", "Report written to Obsidian and LanceDB synced")
    except Exception as obs_err:  # noqa: BLE001 - graceful fallback on vault sync failure
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


def route_after_worktree_builder(state: HydramedaState) -> str:
    """Conditional edge after worktree_builder: routes to code_builder or consolidator on failure."""
    if state.get("error") or state.get("status") == "ERROR":
        return "consolidator"
    return "code_builder"


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

def build_hydrameda_graph(checkpointer: Any | None = None, db_path: Path | None = None):
    """Builds and compiles the 12-node cyclic neuro-symbolic LangGraph engine."""
    workflow = StateGraph(HydramedaState)

    # Register nodes
    workflow.add_node("spec_analyzer", node_spec_analyzer)
    workflow.add_node("rag_retriever", node_rag_retriever)
    workflow.add_node("architect", node_architect)
    workflow.add_node("spec_validator", node_spec_validator)
    workflow.add_node("prompt_optimizer", node_prompt_optimizer)
    workflow.add_node("approval_gate", node_approval_gate)
    workflow.add_node("task_router", node_task_router)
    workflow.add_node("worktree_builder", node_worktree_builder)
    workflow.add_node("code_builder", node_code_builder)
    workflow.add_node("sandbox_verifier", node_sandbox_verifier)
    workflow.add_node("error_analyzer", node_error_analyzer)
    workflow.add_node("repair_agent", node_repair_agent)
    workflow.add_node("consolidator", node_consolidator)

    # Linear planning spine with bounded fidelity revision loop
    workflow.set_entry_point("spec_analyzer")
    workflow.add_edge("spec_analyzer", "rag_retriever")
    workflow.add_edge("rag_retriever", "architect")
    workflow.add_edge("architect", "spec_validator")
    workflow.add_conditional_edges(
        "spec_validator",
        route_after_spec_validator,
        {
            "prompt_optimizer": "prompt_optimizer",
            "architect": "architect",
            "consolidator": "consolidator"
        }
    )
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

    # Execution spine with error propagation (Requirement B1.6)
    workflow.add_conditional_edges(
        "worktree_builder",
        route_after_worktree_builder,
        {
            "code_builder": "code_builder",
            "consolidator": "consolidator"
        }
    )
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
# CLI Entrypoint & Subcommands (Requirements A1, A2, B2, B4)
# ============================================================================

def main():
    import json
    parser = argparse.ArgumentParser(description="Hydrameda Autonomous Neuro-Symbolic Coding Engine")
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to run")

    # Subcommand: approve
    approve_parser = subparsers.add_parser("approve", help="Explicitly approve a paused task")
    approve_parser.add_argument("task_id", help="Task ID to approve")
    approve_parser.add_argument("--manifest-hash", required=True, help="Explicit manifest hash to approve")
    approve_parser.add_argument("--destructive-approved", action="store_true", default=False, help="Authorize file deletions")
    approve_parser.add_argument("--merge-approved", action="store_true", default=False, help="Authorize merge into main branch")

    # Subcommand: reject
    reject_parser = subparsers.add_parser("reject", help="Explicitly reject a paused task")
    reject_parser.add_argument("task_id", help="Task ID to reject")

    # Subcommand: status
    status_parser = subparsers.add_parser("status", help="Get status of a task")
    status_parser.add_argument("task_id", help="Task ID to inspect")

    # Subcommand: run (or default root parser)
    run_parser = subparsers.add_parser("run", help="Run a new coding task")
    for p in (parser, run_parser):
        p.add_argument("positional_prompt", nargs="?", default=None, help="Task prompt")
        p.add_argument("--prompt", "-p", type=str, default=None, help="Task prompt (named flag)")
        p.add_argument("--repo", "-r", type=str, default=str(HYDRAMEDA_ROOT), help="Target repository path")
        p.add_argument("--task-id", "-t", type=str, default=None, help="Explicit task ID")
        p.add_argument("--auto-approve", action="store_true", default=False, help="Explicitly enable trusted auto-approval (default: False)")
        p.add_argument("--trusted-policy", type=str, default=None, help="Explicit trusted authorization policy (e.g. ci_automation, eval_harness)")
        p.add_argument("--destructive-approved", action="store_true", default=False, help="Explicitly authorize destructive file deletions (default: False)")
        p.add_argument("--merge-approved", action="store_true", default=False, help="Explicitly authorize merging into main branch (default: False)")
        p.add_argument("--interactive", action="store_true", default=False, help="Enable human review interrupt gate")
        p.add_argument("--non-interactive", action="store_true", default=False, help="Disable terminal prompt blocking on interrupts")

    args = parser.parse_args()

    # Handle 'status' subcommand
    if args.subcommand == "status":
        tid = validate_task_id(args.task_id)
        app = build_hydrameda_graph()
        config = {"configurable": {"thread_id": tid}}
        snapshot = app.get_state(config)
        if not snapshot.values:
            print(f"❌ Task '{tid}' not found in state database.")
            sys.exit(1)
        print(f"Task ID: {tid}")
        print(f"Task State: {snapshot.values.get('task_state', 'UNKNOWN')}")
        print(f"Status: {snapshot.values.get('status', 'UNKNOWN')}")
        print(f"Next Nodes: {snapshot.next}")
        if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
            print("⚠️ Awaiting Approval Interrupt:")
            for t in snapshot.tasks:
                for intr in t.interrupts:
                    print(f"   Action: {intr.value.get('action')}")
                    print(f"   Manifest Hash: {intr.value.get('manifest_hash')}")
        sys.exit(0)

    # Handle 'approve' subcommand
    if args.subcommand == "approve":
        tid = validate_task_id(args.task_id)
        app = build_hydrameda_graph()
        config = {"configurable": {"thread_id": tid}}
        snapshot = app.get_state(config)
        if not snapshot.next:
            print(f"❌ Task '{tid}' is not currently awaiting resumption.")
            sys.exit(1)

        resume_payload = {
            "approved": True,
            "approved_manifest_hash": args.manifest_hash.strip(),
            "task_id": tid,
            "approver": "cli_user",
            "approval_mode": "cli_command",
            "authorization_source": "hydrameda_approve",
            "destructive_approved": args.destructive_approved,
            "merge_approved": args.merge_approved
        }
        print(f"✅ Resuming task '{tid}' with explicit approval (manifest_hash: {args.manifest_hash})...")
        result_state = app.invoke(Command(resume=resume_payload), config)
        final_state = result_state.get("task_state", result_state.get("status", "UNKNOWN"))
        print(f"🏁 Task '{tid}' finished with state: {final_state}")
        sys.exit(0 if final_state in ("COMPLETED", "PASSED") else 1)

    # Handle 'reject' subcommand
    if args.subcommand == "reject":
        tid = validate_task_id(args.task_id)
        app = build_hydrameda_graph()
        config = {"configurable": {"thread_id": tid}}
        snapshot = app.get_state(config)
        if not snapshot.next:
            print(f"❌ Task '{tid}' is not currently awaiting resumption.")
            sys.exit(1)

        resume_payload = {
            "approved": False,
            "task_id": tid,
            "approver": "cli_user",
            "comments": "Explicitly rejected via CLI"
        }
        print(f"🛑 Rejecting task '{tid}'...")
        result_state = app.invoke(Command(resume=resume_payload), config)
        print(f"Task '{tid}' rejected and cancelled.")
        sys.exit(0)

    # Default 'run' execution flow
    prompt_arg = args.prompt or args.positional_prompt or "Create a JWT token validator with unit tests"
    slug = re.sub(r"[^a-z0-9]+", "-", prompt_arg.lower()).strip("-")[:24].rstrip("-")
    raw_task_id = args.task_id or f"task-{slug}-{int(time.time()) % 10000}"
    task_id = validate_task_id(raw_task_id)

    # Authorization policy check (Requirement A2.5/A2.6)
    trusted_policy = args.trusted_policy or os.getenv("HYDRAMEDA_TRUSTED_POLICY")
    auto_approve = False
    if args.auto_approve and not args.interactive:
        ALLOWED_TRUSTED_POLICIES = {"ci_automation", "eval_harness", "internal_trusted", "batch_backlog", "cli_authorized"}
        if trusted_policy in ALLOWED_TRUSTED_POLICIES:
            auto_approve = True
        else:
            print(f"⚠️ Untrusted auto-approval rejected: trusted policy '{trusted_policy}' is not authorized. Falling back to review gate.")

    is_non_interactive = args.non_interactive or not sys.stdin.isatty()

    print(f"🚀 Starting Hydrameda Neuro-Symbolic Engine for task: '{task_id}' (auto_approve={auto_approve}, policy={trusted_policy})")
    app = build_hydrameda_graph()

    initial_state: HydramedaState = {
        "task_id": task_id,
        "task_state": TaskState.CREATED.value,
        "user_prompt": prompt_arg,
        "repo_path": args.repo,
        "auto_approve": auto_approve,
        "trusted_policy": trusted_policy or "",
        "approval_status": "PENDING",
        "destructive_approved": args.destructive_approved,
        "merge_approved": args.merge_approved,
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

    # Multi-interrupt interactive CLI loop (Requirement B2)
    while True:
        snapshot = app.get_state(config)
        if not snapshot.tasks or not any(t.interrupts for t in snapshot.tasks):
            break

        active_interrupt = None
        for task in snapshot.tasks:
            if task.interrupts:
                active_interrupt = task.interrupts[0].value
                break

        if not active_interrupt:
            break

        action = active_interrupt.get("action") if isinstance(active_interrupt, dict) else ""

        # Non-interactive handling: pause cleanly without stdin blocking (Requirement A2.2)
        if is_non_interactive:
            m_hash = active_interrupt.get("manifest_hash", "N/A")
            print(f"\n⏸️ Task '{task_id}' paused in AWAITING_APPROVAL at '{action}'.")
            print(f"   Manifest Hash: {m_hash}")
            print(f"   To approve: hydrameda approve {task_id} --manifest-hash {m_hash}")
            sys.exit(0)

        if action == "review_specification":
            print("\n" + "=" * 60)
            print("📋 ARCHITECTURAL SPECIFICATION REVIEW")
            print("=" * 60)
            spec_preview = snapshot.values.get("spec_md", "")
            print(spec_preview[:1200] + ("\n... [truncated]" if len(spec_preview) > 1200 else ""))
            print("=" * 60)
            choice = input("\nApprove specification and proceed to build? [y/N]: ").strip().lower()
            is_approved = choice in ("y", "yes")
            result_state = app.invoke(Command(resume={"approved": is_approved, "task_id": task_id}), config)
        elif action == "review_and_consolidate":
            print("\n" + "=" * 60)
            print("🔒 REPOSITORY CONSOLIDATION GOVERNANCE REVIEW")
            print("=" * 60)
            review_display = active_interrupt.get("review_display", "")
            if review_display:
                print(review_display[:1500] + ("\n... [truncated]" if len(review_display) > 1500 else ""))
            else:
                print(f"Task ID: {active_interrupt.get('task_id')}")
                print(f"Manifest Hash: {active_interrupt.get('manifest_hash')}")
                print(f"Risk Level: {active_interrupt.get('risk_level')}")
                print(f"Affected Files: {active_interrupt.get('affected_files')}")
            print("=" * 60)
            choice = input("\nApprove change consolidation into repository? [y/N]: ").strip().lower()
            is_approved = choice in ("y", "yes")
            resume_data = {
                "approved": is_approved,
                "approved_manifest_hash": active_interrupt.get("manifest_hash"),
                "task_id": task_id,
                "destructive_approved": args.destructive_approved,
                "merge_approved": args.merge_approved,
                "approver": "human"
            }
            result_state = app.invoke(Command(resume=resume_data), config)
        else:
            question = active_interrupt.get("question", "Approve? [y/N]") if isinstance(active_interrupt, dict) else str(active_interrupt)
            choice = input(f"\n{question}: ").strip().lower()
            is_approved = choice in ("y", "yes")
            result_state = app.invoke(Command(resume={"approved": is_approved, "task_id": task_id}), config)

    # Print structured report (Requirement B4)
    exec_report = result_state.get("execution_report")
    if exec_report:
        print("\n" + "=" * 60)
        print("📊 HYDRAMEDA STRUCTURED EXECUTION REPORT")
        print("=" * 60)
        print(json.dumps(exec_report, indent=2))
        print("=" * 60)

    final_task_state = result_state.get("task_state", TaskState.FAILED.value)
    final_status = result_state.get("status", "UNKNOWN")
    print(f"\n🏁 Hydrameda pipeline execution completed with state: {final_task_state} (status: {final_status})")
    sys.exit(0 if final_task_state == TaskState.COMPLETED.value or final_status == "PASSED" else 1)


if __name__ == "__main__":
    main()

