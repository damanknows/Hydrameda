"""Hydrameda Experience Engine: Post-Execution Lesson Extractor.

Extracts generalizable lessons, anti-pattern warnings, and hazard advisories from task execution traces,
especially from verification failures, semantic deviations, or governance interruptions.
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


class ExperienceExtractor:
    """Analyzes failures, deviations, and repair loops to extract reusable engineering lessons."""

    @classmethod
    def extract_from_execution(
        cls,
        task_id: str,
        task_prompt: str,
        execution_state: dict[str, Any]
    ) -> list[KnowledgeRecord]:
        """Extracts lessons and failure warnings from task execution state."""
        lessons: list[KnowledgeRecord] = []
        error_msg = execution_state.get("error", "")
        prompt_lower = task_prompt.lower()

        # 1. Lesson: Semantic Constraint Contradiction (e.g. JWT introduced when HMAC requested)
        spec_violations = execution_state.get("specification_constraint_violations", [])
        if any("jwt" in v.lower() for v in spec_violations) or (
            "not implement jwt" in prompt_lower and "validate_jwt" in execution_state.get("spec_md", "").lower()
        ):
            payload: dict[str, Any] = {
                "lesson_type": "semantic_constraint_violation",
                "prohibited": "JWT",
                "requested": "HMAC"
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.SEMANTIC_VIOLATION,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"LESSON-SEMANTIC-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="Negative Constraint Contamination: Do Not Introduce Prohibited Protocols (JWT)",
                description=(
                    "Architect agents can be primed by historical notes or training biases to introduce "
                    "complex protocols (like JWT) when the user explicitly asked for non-JWT HMAC tokens."
                ),
                procedure=[
                    "Always check explicit prohibitions before reviewing background RAG notes.",
                    "Disregard historical specs that recommend claims, exp, or JWT headers when negative constraints apply.",
                    "Verify that specification functions strictly use basic payload signing (e.g. sign_token, verify_token).",
                    "Do not allow semantic tests to be satisfied by renaming JWT functions."
                ],
                provenance=prov,
                evidence=f"Violations detected in task {task_id}: {'; '.join(spec_violations) if spec_violations else 'Contradiction observed in generated spec'}",
                confidence=0.98,
                applicability=["crypto", "jwt", "hmac", "constraints", "fidelity"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        # 2. Lesson: Fake Stub Packages to satisfy import checks
        current_files = execution_state.get("current_files", {})
        stub_files = [f for f in current_files if f in ("litellm.py", "langgraph.py", "tree_sitter_language_pack.py")]
        if stub_files or "stub" in error_msg.lower():
            payload = {
                "lesson_type": "fake_stub_anti_pattern",
                "files": stub_files
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.VERIFICATION_FAILURE,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"LESSON-STUB-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="Mock Package Anti-Pattern: Never Create Stubs to Bypass Import Resolution",
                description=(
                    "Repair agents may attempt to pass verification checks by synthesizing fake wrapper "
                    "files (e.g. litellm.py) inside the worktree instead of repairing the target task module."
                ),
                procedure=[
                    "Inspect import errors to identify whether the missing dependency is internal or third-party.",
                    "Never create mock top-level files matching framework or third-party library names.",
                    "Configure verification environment to use standard library or configured dependencies only.",
                    "Fail early with an environment error rather than hallucinating stub modules."
                ],
                provenance=prov,
                evidence=f"Task {task_id} attempted or detected fake stub modules: {stub_files}",
                confidence=0.96,
                applicability=["dependencies", "imports", "verification", "sandbox"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        # 3. Lesson: Rootless Container Restrictive Permission Failure
        stderr = execution_state.get("pytest_stderr", "")
        if "permission denied" in stderr.lower() or "read-only" in stderr.lower() or "permission denied" in error_msg.lower():
            payload = {
                "lesson_type": "rootless_container_permissions",
                "error": error_msg
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.VERIFICATION_FAILURE,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"LESSON-PERM-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="Rootless Container File Permission Traps",
                description=(
                    "Files created in worktrees with restrictive umask (e.g. 0700 or non-world-readable) "
                    "will cause rootless Docker verification containers to fail with EACCES Permission Denied."
                ),
                procedure=[
                    "Ensure worktree files and directories grant read/execute permissions to container UID/GID.",
                    "Avoid mounting sensitive host paths into verification containers.",
                    "Verify file permissions prior to invoking Docker subprocess runners."
                ],
                provenance=prov,
                evidence=f"Permission error observed in task {task_id}: {error_msg or stderr[:200]}",
                confidence=0.92,
                applicability=["docker", "permissions", "sandbox", "rootless"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        return lessons
