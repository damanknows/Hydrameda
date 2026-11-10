"""Hydrameda Experience Engine: Procedural Skill Extractor.

Extracts general engineering skills and reusable procedural recipes from task execution traces.
Ensures skills contain high-level engineering methodologies rather than task-specific code.
"""
from __future__ import annotations

from typing import Any

from .learning_policy import LearningPolicy
from .models import (
    ExecutionOutcome,
    KnowledgeCategory,
    KnowledgeRecord,
    ReusePolicy,
)
from .provenance import ProvenanceTracker


class SkillExtractor:
    """Analyzes task outcomes and extracts reusable procedural engineering skills."""

    @classmethod
    def extract_from_execution(
        cls,
        task_id: str,
        task_prompt: str,
        execution_state: dict[str, Any]
    ) -> list[KnowledgeRecord]:
        """Extracts candidate skills from a successful or partially successful execution."""
        skills: list[KnowledgeRecord] = []
        status = execution_state.get("status", "")
        verified = execution_state.get("verifier_exit_code") == 0 or status == "PASSED"

        prompt_lower = task_prompt.lower()

        # 1. HMAC-SHA256 Safe Implementation Skill
        if "hmac" in prompt_lower and verified:
            payload = {
                "skill": "hmac_sha256_safe_implementation",
                "domain": "cryptography"
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.VERIFIED_SUCCESS,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"SKILL-HMAC-{task_id[:8]}",
                category=KnowledgeCategory.SKILL,
                title="Safe HMAC-SHA256 Token Signing & Verification Procedure",
                description=(
                    "Standard engineering methodology to construct, sign, and verify non-JWT HMAC tokens "
                    "using Python standard library modules with constant-time verification."
                ),
                procedure=[
                    "Use Python stdlib `hmac` and `hashlib` modules directly; avoid third-party crypto wrappers unless requested.",
                    "Serialize token as `<urlsafe_b64_payload>.<urlsafe_b64_signature>` with no JWT headers or claims.",
                    "Compute HMAC signature using `hashlib.sha256` over the raw payload bytes.",
                    "Always verify signatures using `hmac.compare_digest()` to prevent timing side-channel attacks.",
                    "Validate token structural integrity (exactly one delimiter) and raise ValueError on malformed inputs."
                ],
                provenance=prov,
                evidence=f"Task {task_id} passed verification with constant-time HMAC comparison.",
                confidence=0.95,
                applicability=["crypto", "hmac", "token", "security"],
                reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
            )
            is_valid, _ = LearningPolicy.validate_candidate_skill(candidate)
            if is_valid:
                skills.append(candidate)

        # 2. Python Import Resolution and stdlib Healing Recipe
        current_files = execution_state.get("current_files", {})
        if "test_module.py" in current_files and verified:
            payload = {
                "skill": "import_resolution_healing",
                "domain": "toolchain"
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.VERIFIED_SUCCESS,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"SKILL-IMPORT-{task_id[:8]}",
                category=KnowledgeCategory.SKILL,
                title="Python Module Import Resolution & Test Packaging Procedure",
                description="Procedure for structuring tests and modules to avoid relative import and circular import errors.",
                procedure=[
                    "Ensure test files import implementation modules using direct top-level module names: `from module import ...`.",
                    "Avoid intra-package relative imports (`from .module import ...`) in standalone test runners.",
                    "Inspect AST imports to guarantee all standard library modules referenced (e.g. `hmac`, `secrets`) are explicitly imported.",
                    "Index module function and class definitions before parsing test suites to verify symbol availability."
                ],
                provenance=prov,
                evidence="Verified test execution with clean top-level import resolution.",
                confidence=0.90,
                applicability=["python", "imports", "pytest", "testing"],
                reuse_policy=ReusePolicy.VERIFICATION_RECIPE,
            )
            is_valid, _ = LearningPolicy.validate_candidate_skill(candidate)
            if is_valid:
                skills.append(candidate)

        # 3. Isolated Git Worktree Lifecycle Recipe
        if execution_state.get("worktree_path") and verified:
            payload = {
                "skill": "git_worktree_isolation",
                "domain": "infrastructure"
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.VERIFIED_SUCCESS,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"SKILL-WORKTREE-{task_id[:8]}",
                category=KnowledgeCategory.SKILL,
                title="Isolated Git Worktree Management Procedure",
                description="Protocol for creating and executing verification builds in disposable, isolated worktrees.",
                procedure=[
                    "Validate task identifier against strict allowlist before creating branch or path.",
                    "Spawn isolated worktree from target commit without touching the active repository working tree.",
                    "Apply changes exclusively via cryptographic PatchEngine manifests.",
                    "Execute verification in rootless container sandbox mounted strictly to the worktree path.",
                    "Prune stale worktrees after execution unless explicitly preserved for debugging."
                ],
                provenance=prov,
                evidence="Autonomous build executed and verified in isolated worktree.",
                confidence=0.92,
                applicability=["git", "worktree", "isolation", "sandbox"],
                reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
            )
            is_valid, _ = LearningPolicy.validate_candidate_skill(candidate)
            if is_valid:
                skills.append(candidate)

        return skills
