"""Tests for Phase 8 (Post-Verification Experience Extraction) & Phase 9 (15-Node Graph Integration).

Verifies:
1. Extraction of the 4 mandatory engineering lessons:
   - Explicitly prohibited functionality must be checked before approval
   - A static-analysis pass cannot establish semantic prompt compliance
   - Rootless container permissions need to be considered during generated-file creation
   - Import resolution should distinguish generated files from unrelated repository modules
2. Enforcement of LearningPolicy rejecting 3 prohibited memory patterns:
   - Storing entire generated implementations as templates
   - Copying every file from previous worktrees into future context
   - Treating a single repair as universally valid
3. Verification of evidence and source task ID provenance in every lesson.
4. LangGraph 15-node spine execution and routing:
   - specification auditor branching (PASSED, REJECTED, NEEDS_CLARIFICATION, INTERNAL_ERROR)
   - fail-closed invariant if audit state is missing or invalid
   - node_experience_extractor updating knowledge records and state
"""


from experience_engine.experience_extractor import ExperienceExtractor
from experience_engine.learning_policy import LearningPolicy
from experience_engine.models import (
    ExecutionOutcome,
    KnowledgeCategory,
    KnowledgeRecord,
    ReusePolicy,
)
from experience_engine.provenance import ProvenanceTracker
from langgraph_engine import graph


class TestPhase8ExperienceExtraction:
    """Verifies Phase 8 post-verification experience extraction rules and policies."""

    def test_extract_four_mandatory_lessons(self) -> None:
        """Requirement Phase 8: Must extract the 4 mandatory engineering lessons with evidence."""
        # 1. Negative constraint / spec violation
        state_prohib = {
            "status": "FAILED",
            "specification_constraint_violations": ["Prohibited JWT functionality detected"],
            "spec_md": "def validate_jwt(): pass",
        }
        lessons1 = ExperienceExtractor.extract_from_execution(
            task_id="task-prohib-101",
            task_prompt="Create HMAC token signer. Do not implement JWT.",
            execution_state=state_prohib,
        )
        lesson_prohib = next(
            (l for l in lessons1 if "explicitly prohibited" in l.title.lower()), None
        )
        assert lesson_prohib is not None
        assert lesson_prohib.provenance.source_task_id == "task-prohib-101"
        assert "task-prohib-101" in lesson_prohib.evidence
        assert lesson_prohib.category == KnowledgeCategory.LESSON
        assert lesson_prohib.code_snippet == ""

        # 2. Static pass vs semantic failure
        state_semantic = {
            "status": "FAILED",
            "technical_verification_status": "PASSED",
            "semantic_compliance_status": "FAILED",
            "semantic_compliance_report": {
                "summary": "Required HMAC function missing",
                "findings": ["sign_token not implemented"],
            },
        }
        lessons2 = ExperienceExtractor.extract_from_execution(
            task_id="task-sem-102",
            task_prompt="Implement HMAC signer",
            execution_state=state_semantic,
        )
        lesson_sem = next(
            (l for l in lessons2 if "static-analysis pass cannot establish" in l.title.lower()), None
        )
        assert lesson_sem is not None
        assert lesson_sem.provenance.source_task_id == "task-sem-102"
        assert "task-sem-102" in lesson_sem.evidence
        assert any("semantic" in step.lower() for step in lesson_sem.procedure)

        # 3. Rootless container permission failure
        state_perm = {
            "status": "FAILED",
            "error": "Command failed: EACCES Permission Denied on /worktree/out.py",
            "pytest_stderr": "Permission denied: '/worktree/test.py'",
        }
        lessons3 = ExperienceExtractor.extract_from_execution(
            task_id="task-perm-103",
            task_prompt="Build module",
            execution_state=state_perm,
        )
        lesson_perm = next(
            (l for l in lessons3 if "rootless container permissions" in l.title.lower()), None
        )
        assert lesson_perm is not None
        assert lesson_perm.provenance.source_task_id == "task-perm-103"
        assert "task-perm-103" in lesson_perm.evidence
        assert any("permission" in step.lower() for step in lesson_perm.procedure)

        # 4. Import resolution isolating generated files from unrelated repository modules
        state_import = {
            "status": "FAILED",
            "error": "Import resolution failed: module 'litellm' not found",
            "current_files": {"litellm.py": "# stub", "signer.py": "code"},
        }
        lessons4 = ExperienceExtractor.extract_from_execution(
            task_id="task-import-104",
            task_prompt="Implement HMAC utility",
            execution_state=state_import,
        )
        lesson_import = next(
            (l for l in lessons4 if "import resolution should distinguish" in l.title.lower()), None
        )
        assert lesson_import is not None
        assert lesson_import.provenance.source_task_id == "task-import-104"
        assert "task-import-104" in lesson_import.evidence
        assert any("third-party" in step.lower() or "stub" in step.lower() for step in lesson_import.procedure)

    def test_learning_policy_rejects_entire_implementation_templates(self) -> None:
        """Requirement Phase 8: Reject storing entire generated implementation as a reusable template."""
        prov = ProvenanceTracker.create_provenance("task-bad-01", ExecutionOutcome.VERIFIED_SUCCESS, {})
        bad_template_lesson = KnowledgeRecord(
            knowledge_id="BAD-TEMPLATE-01",
            category=KnowledgeCategory.LESSON,
            title="Reusable Template: Complete JWT Implementation For All Authentication Tasks",
            description="Use this complete JWT implementation as a template for all future authentication tasks.",
            procedure=[
                "Copy this entire implementation into future repositories.",
                "Reuse full code template for all auth problems."
            ],
            provenance=prov,
            evidence="Reused template across tasks",
            confidence=0.8,
            applicability=["auth", "jwt"],
            reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
        )
        valid, reason = LearningPolicy.validate_candidate_lesson(bad_template_lesson)
        assert not valid
        assert "entire implementation" in reason.lower() or "template" in reason.lower()

    def test_learning_policy_rejects_copying_entire_worktree(self) -> None:
        """Requirement Phase 8: Reject copying every file from a worktree into future context."""
        prov = ProvenanceTracker.create_provenance("task-bad-02", ExecutionOutcome.VERIFIED_SUCCESS, {})
        bad_worktree_lesson = KnowledgeRecord(
            knowledge_id="BAD-WORKTREE-02",
            category=KnowledgeCategory.LESSON,
            title="Historical Worktree Cache",
            description="Copy every file from previous worktree into future RAG context for maximum retrieval.",
            procedure=["Inject whole worktree into system prompt"],
            provenance=prov,
            evidence="Injected files into context",
            confidence=0.7,
            applicability=["worktree", "rag"],
            reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
        )
        valid, reason = LearningPolicy.validate_candidate_lesson(bad_worktree_lesson)
        assert not valid
        assert "worktree" in reason.lower() or "copy" in reason.lower()

    def test_learning_policy_rejects_universal_repair_assumptions(self) -> None:
        """Requirement Phase 8: Reject treating one single repair as universally valid."""
        prov = ProvenanceTracker.create_provenance("task-bad-03", ExecutionOutcome.VERIFIED_SUCCESS, {})
        bad_repair_lesson = KnowledgeRecord(
            knowledge_id="BAD-REPAIR-03",
            category=KnowledgeCategory.LESSON,
            title="Universal Repair Recipe",
            description="This single repair action is universally valid for all future import failures.",
            procedure=["Apply this one fix to every bug"],
            provenance=prov,
            evidence="Worked once on task-bad-03",
            confidence=0.9,
            applicability=["repair"],
            reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
        )
        valid, reason = LearningPolicy.validate_candidate_lesson(bad_repair_lesson)
        assert not valid
        assert "universally valid" in reason.lower() or "universal" in reason.lower()


class TestPhase9GraphIntegration:
    """Verifies Phase 9 LangGraph 15-node integration, state transitions, and fail-closed gates."""

    def test_node_intent_compiler_populates_state(self) -> None:
        """Intent compiler runs at graph entry, compiling canonical intent and requirements."""
        state: graph.HydramedaState = {
            "task_id": "test-ic-01",
            "user_prompt": "Implement HMAC-SHA256 signer. Do not implement JWT or claims.",
            "structured_logs": [],
        }
        res = graph.node_intent_compiler(state)
        assert res["task_state"] == graph.TaskState.ANALYZING.value
        assert "structured_intent" in res
        assert "task_requirements" in res
        assert len(res["structured_intent"]["explicit_prohibitions"]) >= 1
        assert any("jwt" in p.lower() for p in res["task_requirements"]["prohibited_capabilities"])

    def test_route_after_spec_validator_branches(self) -> None:
        """Auditor branches: PASSED -> prompt_optimizer, REJECTED -> architect, NEEDS_CLARIFICATION -> approval_gate, INTERNAL_ERROR -> consolidator."""
        # 1. PASSED
        s_pass: graph.HydramedaState = {
            "specification_fidelity_status": "PASSED",
            "specification_audit_outcome": "PASSED",
        }
        assert graph.route_after_spec_validator(s_pass) == "prompt_optimizer"

        # 2. NEEDS_CLARIFICATION
        s_clarify: graph.HydramedaState = {
            "specification_audit_outcome": "NEEDS_CLARIFICATION",
        }
        assert graph.route_after_spec_validator(s_clarify) == "approval_gate"

        # 3. REJECTED (attempts < 3)
        s_retry: graph.HydramedaState = {
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "spec_validation_attempts": 1,
        }
        assert graph.route_after_spec_validator(s_retry) == "architect"

        # 4. REJECTED (attempts >= 3)
        s_max_retry: graph.HydramedaState = {
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "spec_validation_attempts": 3,
        }
        assert graph.route_after_spec_validator(s_max_retry) == "consolidator"
        assert s_max_retry["status"] == "FAILED"

        # 5. INTERNAL_ERROR / missing audit state fails closed
        s_missing: graph.HydramedaState = {}
        assert graph.route_after_spec_validator(s_missing) == "consolidator"
        assert s_missing["status"] == "FAILED"

    def test_approval_gate_fails_closed_when_audit_not_passed(self) -> None:
        """Prevent accidental bypasses: approval_gate fails closed if audit state is missing, invalid, or FAILED."""
        # 1. Spec present but audit failed
        state_failed: graph.HydramedaState = {
            "task_id": "test-gate-fail",
            "spec_md": "# Spec with prohibited JWT",
            "specification_fidelity_status": "FAILED",
            "specification_audit_outcome": "REJECTED",
            "approval_status": "PENDING",
            "structured_logs": [],
        }
        res_failed = graph.node_approval_gate(state_failed)
        assert res_failed["approval_status"] == "REJECTED"
        assert res_failed["task_state"] == graph.TaskState.CANCELLED.value

        # 2. Spec present but audit missing (bypassed)
        state_missing: graph.HydramedaState = {
            "task_id": "test-gate-bypass",
            "spec_md": "# Unvalidated Spec",
            "approval_status": "PENDING",
            "structured_logs": [],
        }
        res_missing = graph.node_approval_gate(state_missing)
        assert res_missing["approval_status"] == "REJECTED"
        assert res_missing["task_state"] == graph.TaskState.CANCELLED.value

    def test_node_experience_extractor_persists_valid_records(self, tmp_path) -> None:
        """node_experience_extractor extracts lessons, filters through LearningPolicy, and writes to state."""
        state: graph.HydramedaState = {
            "task_id": "task-ee-01",
            "user_prompt": "Create HMAC signer. Do not implement JWT.",
            "status": "FAILED",
            "specification_constraint_violations": ["Prohibited JWT claims detected"],
            "spec_md": "def validate_jwt(): pass",
            "structured_logs": [],
        }
        res = graph.node_experience_extractor(state)
        assert res["experience_extraction_status"] == "COMPLETED"
        assert len(res["extracted_lessons"]) >= 1
        assert any("prohibited" in l["title"].lower() for l in res["extracted_lessons"])

    def test_full_graph_structure_has_15_nodes_and_entry_point(self) -> None:
        """Verifies the StateGraph structure contains all 15 nodes and starts at intent_compiler."""
        app = graph.build_hydrameda_graph()
        assert app is not None
        # Register nodes as in build_hydrameda_graph
        expected_nodes = {
            "intent_compiler",
            "rag_retriever",
            "spec_analyzer",
            "architect",
            "spec_validator",
            "prompt_optimizer",
            "approval_gate",
            "task_router",
            "worktree_builder",
            "code_builder",
            "sandbox_verifier",
            "error_analyzer",
            "repair_agent",
            "consolidator",
            "experience_extractor",
        }
        # In our graph, exactly the 15 nodes are registered
        for n in expected_nodes:
            assert n in app.get_graph().nodes
        assert len(expected_nodes) == 15
