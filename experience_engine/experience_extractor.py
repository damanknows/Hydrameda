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

        # 1. Lesson: Explicitly prohibited functionality must be checked before approval
        spec_violations = execution_state.get("specification_constraint_violations", [])
        prohibitions = execution_state.get("task_requirements", {}).get("prohibitions", [])
        if spec_violations or any("jwt" in p.lower() for p in prohibitions) or (
            "not implement jwt" in prompt_lower and "validate_jwt" in execution_state.get("spec_md", "").lower()
        ):
            payload: dict[str, Any] = {
                "lesson_type": "prohibition_audit_before_approval",
                "violations": spec_violations,
                "task_prompt": task_prompt
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.SEMANTIC_VIOLATION if spec_violations else ExecutionOutcome.VERIFIED_SUCCESS,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"LESSON-PROHIB-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="Explicitly Prohibited Functionality Must Be Checked Before Approval (e.g. Prohibited JWT)",
                description=(
                    "Negative constraints and explicitly prohibited features must be deterministically checked "
                    "and audited before presenting a proposed specification or changeset for human approval."
                ),
                procedure=[
                    "Compile and isolate user-specified negative constraints into an immutable priority ledger and enforce strict prohibition checks before approval.",
                    "Audit generated specifications against prohibitions using deterministic AST and keyword detection.",
                    "Reject non-compliant specifications early before presentation to human review gates.",
                    "Never rely on human approval alone to catch prohibited architectural contamination."
                ],
                provenance=prov,
                evidence=f"Task {task_id} audited explicit prohibitions against user requirements: {spec_violations or 'verified strict negative constraints'}",
                confidence=0.98,
                applicability=["prohibitions", "negative_constraints", "governance", "approval_gate", "fidelity"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        # 2. Lesson: A static-analysis pass cannot establish semantic prompt compliance
        tech_status = execution_state.get("technical_verification_status", "")
        sem_status = execution_state.get("semantic_compliance_status", "")
        sem_report = execution_state.get("semantic_compliance_report", {})
        err_cat = execution_state.get("error_analysis", {}).get("category", "")
        if (tech_status == "PASSED" and sem_status in ("FAILED", "WARNING")) or err_cat == "SEMANTIC_COMPLIANCE_FAILURE" or sem_report.get("findings"):
            payload = {
                "lesson_type": "static_vs_semantic_compliance",
                "technical_status": tech_status,
                "semantic_status": sem_status,
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.SEMANTIC_VIOLATION,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"LESSON-SEMANTIC-COMPLIANCE-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="A Static-Analysis Pass Cannot Establish Semantic Prompt Compliance",
                description=(
                    "Passing static analysis (Ruff, Mypy) and technical tests does not guarantee semantic compliance. "
                    "Code may pass all technical checks while completely missing required features or implementing prohibited ones."
                ),
                procedure=[
                    "Maintain separate technical verification and semantic task compliance verification layers.",
                    "Inspect generated ASTs to ensure requested functions have substantive non-stub implementations.",
                    "Verify test effectiveness to confirm test assertions actually exercise the requested code.",
                    "Never treat a technical verification pass as an automatic indication of overall task success."
                ],
                provenance=prov,
                evidence=f"Task {task_id} demonstrated technical verification pass ({tech_status}) with semantic compliance evaluation ({sem_status}): {sem_report.get('summary', 'semantic compliance findings')}",
                confidence=0.97,
                applicability=["verification", "semantic_compliance", "static_analysis", "ast", "invariants"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        # 3. Lesson: Rootless container permissions need to be considered during generated-file creation
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
                knowledge_id=f"LESSON-ROOTLESS-PERM-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="Rootless Container Permissions Need To Be Considered During Generated-File Creation",
                description=(
                    "Files created in host worktrees with restrictive umask (e.g. 0700 or non-world-readable) "
                    "cause rootless Docker verification containers to fail with EACCES Permission Denied errors."
                ),
                procedure=[
                    "Ensure worktree files and directories grant read and execute permissions compatible with container users.",
                    "Avoid mounting sensitive host paths into rootless verification sandboxes.",
                    "Inspect and normalize file permission bits when creating new files and directories."
                ],
                provenance=prov,
                evidence=f"Permission diagnostic observed in task {task_id}: {error_msg or stderr[:200]}",
                confidence=0.94,
                applicability=["docker", "permissions", "sandbox", "rootless", "container"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        # 4. Lesson: Import resolution should distinguish generated files from unrelated repository modules
        current_files = execution_state.get("current_files", {})
        stub_files = [f for f in current_files if f in ("litellm.py", "langgraph.py", "tree_sitter_language_pack.py", "pytest.py")]
        diag = str(execution_state.get("error_analysis", {}))
        if stub_files or "import" in error_msg.lower() or "import_resolution" in diag.lower() or "stub" in error_msg.lower():
            payload = {
                "lesson_type": "scoped_import_resolution",
                "files": stub_files,
                "error": error_msg
            }
            prov = ProvenanceTracker.create_provenance(
                source_task_id=task_id,
                outcome=ExecutionOutcome.VERIFICATION_FAILURE,
                content_payload=payload
            )
            candidate = KnowledgeRecord(
                knowledge_id=f"LESSON-IMPORT-RES-{task_id[:8]}",
                category=KnowledgeCategory.LESSON,
                title="Import Resolution Should Distinguish Generated Files From Unrelated Repository Modules (Avoid Mock Stubs)",
                description=(
                    "Import resolution must isolate task-generated files from unrelated pre-existing repository modules. "
                    "Unscoped repository scans cause false-positive baseline failures and prompt repair agents to synthesize fake stubs."
                ),
                procedure=[
                    "Scope import resolution strictly to files created or modified by the task.",
                    "Do not scan unrelated repository modules during task-level import verification.",
                    "Never synthesize fake third-party dependency packages (e.g. litellm.py) to satisfy import checks.",
                    "Fail early with clear dependency guidance rather than generating mock stubs."
                ],
                provenance=prov,
                evidence=f"Import resolution diagnostics in task {task_id}: {stub_files or error_msg or 'import isolation check'}",
                confidence=0.96,
                applicability=["imports", "dependencies", "isolation", "verification", "stubs"],
                reuse_policy=ReusePolicy.FAILURE_WARNING,
            )
            is_valid, _ = LearningPolicy.validate_candidate_lesson(candidate)
            if is_valid:
                lessons.append(candidate)

        return lessons
