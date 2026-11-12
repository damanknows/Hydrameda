"""Tests for the Experience Engine subsystem (Phase 2 & Phase 3).

Verifies:
- Three knowledge categories: SKILL, LESSON, and REPO_CONTEXT.
- Tamper-evident Provenance tracking and cryptographic hash verification.
- LearningPolicy: strictly separates learning from code reuse, rejecting raw code dumps.
- SkillExtractor & ExperienceExtractor: extracts procedural guides and failure lessons from execution traces.
- KnowledgeStore: indexing, confidence decay, and revalidation.
- RelevanceFilter: hard veto of prohibited concepts (e.g. JWT prohibited rejects JWT skills).
- ContextFirewall: segregated, advisory-labeled prompt formatting.
- CRITICAL TEST: An old JWT implementation or JWT-related skill CANNOT cause JWT to appear in a new HMAC-only specification.
"""
from __future__ import annotations

from experience_engine.context_firewall import ContextFirewall
from experience_engine.experience_extractor import ExperienceExtractor
from experience_engine.experience_retriever import ExperienceRetriever
from experience_engine.knowledge_store import KnowledgeStore
from experience_engine.learning_policy import LearningPolicy
from experience_engine.models import (
    ExecutionOutcome,
    KnowledgeCategory,
    KnowledgeRecord,
    ReusePolicy,
)
from experience_engine.provenance import ProvenanceTracker
from experience_engine.relevance_filter import RelevanceFilter
from experience_engine.skill_extractor import SkillExtractor
from intent_engine.constraint_compiler import ConstraintCompiler


class TestModelsAndProvenance:
    """Verifies knowledge category definitions and cryptographic provenance verification."""

    def test_three_distinct_categories(self) -> None:
        assert KnowledgeCategory.SKILL == "SKILL"
        assert KnowledgeCategory.LESSON == "LESSON"
        assert KnowledgeCategory.REPO_CONTEXT == "REPO_CONTEXT"

    def test_provenance_hash_generation_and_verification(self) -> None:
        payload = {"skill": "constant_time_hmac", "algorithm": "sha256"}
        prov = ProvenanceTracker.create_provenance(
            source_task_id="task-hmac-01",
            outcome=ExecutionOutcome.VERIFIED_SUCCESS,
            content_payload=payload,
            context_source="test_runner"
        )

        assert prov.source_task_id == "task-hmac-01"
        assert prov.outcome == ExecutionOutcome.VERIFIED_SUCCESS
        assert len(prov.validation_hash) == 64
        assert prov.authoritative is False  # By default advisory

        # Tamper verification
        assert ProvenanceTracker.verify_provenance(prov, payload) is True
        tampered_payload = {"skill": "constant_time_hmac", "algorithm": "md5"}
        assert ProvenanceTracker.verify_provenance(prov, tampered_payload) is False

    def test_confidence_decay_calculation(self) -> None:
        rec = KnowledgeRecord(
            knowledge_id="SKILL-TEST-DECAY",
            category=KnowledgeCategory.SKILL,
            title="Decay Test Skill",
            description="Testing half-life confidence decay",
            procedure=["Step 1: Test decay"],
            confidence=1.0,
            last_validated_at="2020-01-01T00:00:00+00:00"  # Long ago
        )
        decayed = rec.calculate_decayed_confidence(half_life_days=30.0)
        assert decayed <= 0.15  # Decayed towards minimum floor

    def test_revalidation_updates_confidence_and_timestamp(self) -> None:
        rec = KnowledgeRecord(
            knowledge_id="SKILL-TEST-REVAL",
            category=KnowledgeCategory.SKILL,
            title="Revalidation Test Skill",
            description="Testing confidence adjustment",
            procedure=["Step 1"],
            confidence=0.80,
            last_validated_at="2020-01-01T00:00:00+00:00"
        )
        rec.revalidate(success=True)
        assert rec.confidence == 0.85
        assert "2020" not in rec.last_validated_at

        rec.revalidate(success=False)
        assert rec.confidence == 0.65


class TestLearningPolicy:
    """Verifies strict boundaries between learning and code reuse."""

    def test_default_policy_forbids_historical_code_retrieval(self) -> None:
        prompt = "Create HMAC-SHA256 signature verifier"
        allowed, reason = LearningPolicy.allow_code_retrieval(prompt)
        assert allowed is False
        assert "Policy Violation" in reason

    def test_continuation_without_justification_rejected(self) -> None:
        prompt = "Continue previous implementation of task-123"
        allowed, reason = LearningPolicy.allow_code_retrieval(prompt, justification="")
        assert allowed is False
        assert "missing an explicit" in reason

    def test_continuation_with_justification_allowed(self) -> None:
        prompt = "Continue previous implementation of task-123"
        justification = "Refactoring existing database connection layer for backward compatibility."
        allowed, reason = LearningPolicy.allow_code_retrieval(prompt, justification=justification)
        assert allowed is True
        assert "authorized" in reason.lower()

    def test_candidate_skill_rejects_raw_code_snippets(self) -> None:
        rec = KnowledgeRecord(
            knowledge_id="SKILL-RAW-CODE",
            category=KnowledgeCategory.SKILL,
            title="Raw Code Dump",
            description="Attempt to store code as skill",
            procedure=["Step 1: copy this file"],
            code_snippet="def add(a, b): return a + b"
        )
        valid, reason = LearningPolicy.validate_candidate_skill(rec)
        assert valid is False
        assert "cannot contain raw source code" in reason

    def test_candidate_skill_rejects_code_definitions_in_steps(self) -> None:
        rec = KnowledgeRecord(
            knowledge_id="SKILL-CODE-STEPS",
            category=KnowledgeCategory.SKILL,
            title="Code in steps",
            description="Attempt to put code in steps",
            procedure=["class BadSkill:\n    def run(self): pass"]
        )
        valid, reason = LearningPolicy.validate_candidate_skill(rec)
        assert valid is False
        assert "raw code definitions" in reason


class TestExtractors:
    """Verifies extraction of procedural skills and post-execution lessons."""

    def test_skill_extractor_hmac(self) -> None:
        state = {
            "status": "PASSED",
            "verifier_exit_code": 0,
            "current_files": {"module.py": "code", "test_module.py": "tests"}
        }
        skills = SkillExtractor.extract_from_execution(
            task_id="task-hmac-99",
            task_prompt="Create HMAC-SHA256 token signer with pytest",
            execution_state=state
        )
        assert len(skills) >= 1
        hmac_skill = next(s for s in skills if "hmac" in s.title.lower())
        assert hmac_skill.category == KnowledgeCategory.SKILL
        assert any("compare_digest" in step for step in hmac_skill.procedure)
        assert hmac_skill.code_snippet == ""

    def test_experience_extractor_semantic_jwt_violation(self) -> None:
        state = {
            "status": "FAILED",
            "specification_constraint_violations": ["Specification implements prohibited JWT headers and claims"],
            "spec_md": "# Spec\nvalidate_jwt()"
        }
        lessons = ExperienceExtractor.extract_from_execution(
            task_id="task-fail-01",
            task_prompt="Create HMAC token signer. Do not implement JWT.",
            execution_state=state
        )
        assert len(lessons) >= 1
        jwt_lesson = next(l for l in lessons if "jwt" in l.title.lower())
        assert jwt_lesson.category == KnowledgeCategory.LESSON
        assert jwt_lesson.reuse_policy == ReusePolicy.FAILURE_WARNING
        assert "prohibition" in jwt_lesson.procedure[0].lower() or "veto" in jwt_lesson.procedure[0].lower()

    def test_experience_extractor_fake_stub_anti_pattern(self) -> None:
        state = {
            "status": "FAILED",
            "error": "Import resolution failed",
            "current_files": {"litellm.py": "# stub", "module.py": "code"}
        }
        lessons = ExperienceExtractor.extract_from_execution(
            task_id="task-stub-02",
            task_prompt="Implement utility",
            execution_state=state
        )
        assert len(lessons) >= 1
        stub_lesson = next(l for l in lessons if "stub" in l.title.lower() or "mock" in l.title.lower())
        assert stub_lesson.reuse_policy == ReusePolicy.FAILURE_WARNING


class TestKnowledgeStoreAndRetriever:
    """Verifies persistent store querying and segregated retrieval."""

    def test_store_preloaded_with_core_skills_and_lessons(self, tmp_path) -> None:
        store_path = tmp_path / "test_store.json"
        store = KnowledgeStore(storage_path=store_path)

        skills = store.list_records(category=KnowledgeCategory.SKILL)
        lessons = store.list_records(category=KnowledgeCategory.LESSON)

        assert len(skills) >= 2
        assert len(lessons) >= 2
        assert any("hmac" in s.title.lower() for s in skills)
        assert any("jwt" in l.title.lower() for l in lessons)

    def test_retriever_segregates_skills_and_lessons(self, tmp_path) -> None:
        store = KnowledgeStore(storage_path=tmp_path / "store.json")
        retriever = ExperienceRetriever(store=store)
        prompt = "Create HMAC-SHA256 token signer"
        intent = ConstraintCompiler.compile(prompt)

        ctx = retriever.retrieve(intent=intent)
        assert len(ctx.skills) >= 1
        assert len(ctx.lessons) >= 1
        assert all(s.category == KnowledgeCategory.SKILL for s in ctx.skills)
        assert all(l.category == KnowledgeCategory.LESSON for l in ctx.lessons)


class TestRelevanceFilterAndContextFirewall:
    """Verifies Phase 3 Context Firewall and constraint hard veto."""

    def test_prohibited_jwt_vetoes_affirmative_jwt_skills(self) -> None:
        """CRITICAL TEST: Proving that an affirmative JWT skill is rejected when JWT is prohibited."""
        prompt = "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)

        affirmative_jwt_skill = KnowledgeRecord(
            knowledge_id="SKILL-JWT-AFFIRMATIVE",
            category=KnowledgeCategory.SKILL,
            title="How to Generate and Sign JWT Tokens with Expiration",
            description="Guide on formatting JWT header alg HS256 and standard claims dictionary.",
            procedure=[
                "Create JWT header with alg HS256",
                "Encode payload claims with exp timestamp",
                "Sign JWT with secret"
            ],
            applicability=["jwt", "crypto", "auth"],
            reuse_policy=ReusePolicy.PROCEDURAL_GUIDE,
        )

        ok, reason = RelevanceFilter.evaluate_record(intent, affirmative_jwt_skill)
        assert ok is False
        assert "Hard Veto" in reason
        assert "jwt" in reason.lower()

    def test_prohibited_jwt_allows_jwt_failure_warning_lesson(self) -> None:
        """Ensures that a lesson warning AGAINST JWT is preserved to help the agent avoid the trap."""
        prompt = "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)

        anti_jwt_lesson = KnowledgeRecord(
            knowledge_id="LESSON-ANTI-JWT",
            category=KnowledgeCategory.LESSON,
            title="Failure Warning: Contamination of Non-JWT HMAC Requests with JWT Protocols",
            description="Warning against introducing JWT when user requested non-JWT HMAC.",
            procedure=[
                "Do not introduce JWT headers or claims",
                "Strictly avoid validate_jwt()"
            ],
            evidence="Task hydrameda-hmac-integration-002 failure",
            applicability=["jwt", "hmac", "crypto"],
            reuse_policy=ReusePolicy.FAILURE_WARNING,
        )

        ok, reason = RelevanceFilter.evaluate_record(intent, anti_jwt_lesson, task_keywords=["hmac", "crypto"])
        assert ok is True
        assert "relevant" in reason.lower()

    def test_context_firewall_assembles_five_segregated_categories(self, tmp_path) -> None:
        """Verifies the 5 distinct context categories with explicit advisory and authoritative headers."""
        prompt = "Create an HMAC-SHA256 token signer and verifier. Do not implement JWT or claims."
        intent = ConstraintCompiler.compile(prompt)

        store = KnowledgeStore(storage_path=tmp_path / "firewall_store.json")
        retriever = ExperienceRetriever(store=store)
        experience_ctx = retriever.retrieve(intent=intent)

        assembled = ContextFirewall.assemble_architect_prompt_context(
            intent=intent,
            repo_context_str="Existing Files: crypto/hasher.py",
            experience_context=experience_ctx,
            vault_context="# Historical Note\nSome background on signing."
        )

        assert "=== CATEGORY 1: ORIGINAL TASK & IMMUTABLE CONSTRAINTS [AUTHORITATIVE - MUST SATISFY] ===" in assembled
        assert "=== CATEGORY 2: CURRENT REPOSITORY ARCHITECTURE [INSPECTED REALITY] ===" in assembled
        assert "=== CATEGORY 3: RETRIEVED ENGINEERING SKILLS [ADVISORY - PROCEDURAL GUIDELINES] ===" in assembled
        assert "=== CATEGORY 4: RETRIEVED LESSONS LEARNED [ADVISORY - FAILURE WARNINGS] ===" in assembled
        assert "=== CATEGORY 5: SUPPORTING DOCUMENTATION [OPTIONAL REFERENCE MATERIAL] ===" in assembled

        # Check explicit advisory notice in categories 3 and 4
        assert "ADVISORY" in assembled
        assert "NOT PERMISSION TO OVERRIDE" in assembled
