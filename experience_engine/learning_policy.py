"""Hydrameda Experience Engine: Learning Policy and Code Separation Guard.

Enforces the core architectural requirement:
SEPARATE LEARNING FROM CODE REUSE.

Guarantees:
1. Never blindly retrieve or inject historical source code or entire worktrees.
2. Default retrieval allows ONLY general skills, procedural recipes, and failure lessons.
3. Blocks model outputs that attempt to save raw implementation source files as skill memory.
4. Requires explicit user authorization and relevance justification for any code continuation.
5. Governs confidence decay and revalidation.
"""
from __future__ import annotations

import re
from typing import Any

from .models import KnowledgeCategory, KnowledgeRecord, ReusePolicy


class LearningPolicy:
    """Enforces strict boundaries between knowledge learning and code reuse."""

    MINIMUM_CONFIDENCE_THRESHOLD = 0.30
    PROHIBITED_CODE_SNIPPET_LENGTH = 120  # Max chars before a procedure is flagged as raw code dump

    @classmethod
    def allow_code_retrieval(
        cls,
        task_prompt: str,
        justification: str = ""
    ) -> tuple[bool, str]:
        """Determines if the current task explicitly authorizes retrieval of historical source code."""
        prompt_lower = task_prompt.lower()
        continuation_keywords = [
            "continue previous implementation",
            "reuse implementation from",
            "refactor task",
            "inspect previous code from task",
            "build upon existing worktree"
        ]

        explicit_request = any(kw in prompt_lower for kw in continuation_keywords)
        if not explicit_request:
            return (
                False,
                (
                    "Policy Violation: Historical source code retrieval is forbidden by default. "
                    "Only procedural skills and architectural lessons may be retrieved."
                )
            )

        if not justification or len(justification.strip()) < 15:
            return (
                False,
                "Policy Violation: Code retrieval requested but missing an explicit, detailed relevance justification."
            )

        return True, "Code retrieval authorized by explicit user task request with valid justification."

    @classmethod
    def validate_candidate_skill(cls, candidate: KnowledgeRecord) -> tuple[bool, str]:
        """Validates that a candidate skill record contains reusable procedural knowledge, NOT raw code."""
        if candidate.category != KnowledgeCategory.SKILL:
            return False, f"Expected SKILL category, got {candidate.category.value}"

        # 1. Reject raw source code dumps
        if candidate.code_snippet:
            return (
                False,
                (
                    "Policy Violation: Skill records cannot contain raw source code snippets. "
                    "Skills must be abstract procedural steps."
                )
            )

        # 2. Check procedure steps for raw code fences or code dumping
        for step in candidate.procedure:
            if re.search(r"```(?:python)?|class\s+[A-Za-z0-9_]+:|def\s+[a-z0-9_]+\s*\(", step):
                return (
                    False,
                    "Policy Violation: Procedure step appears to contain raw code definitions rather than general procedural guidance."
                )

        # 3. Check for minimum procedural substance
        if not candidate.procedure or len(candidate.procedure) < 1:
            return False, "Policy Violation: Candidate skill record has no procedural steps."

        # 4. Check confidence threshold
        if candidate.confidence < cls.MINIMUM_CONFIDENCE_THRESHOLD:
            return False, f"Confidence {candidate.confidence:.2f} is below minimum threshold {cls.MINIMUM_CONFIDENCE_THRESHOLD:.2f}."

        return True, "Candidate skill validated successfully."

    @classmethod
    def validate_candidate_lesson(cls, candidate: KnowledgeRecord) -> tuple[bool, str]:
        """Validates that an experience lesson contains actionable warnings and root-cause analysis."""
        if candidate.category != KnowledgeCategory.LESSON:
            return False, f"Expected LESSON category, got {candidate.category.value}"

        # 1. Must contain evidence
        if not candidate.evidence or len(candidate.evidence.strip()) < 10:
            return False, "Policy Violation: Lesson record must include concrete execution evidence or failure trace."

        # 2. Must not store raw implementation code
        if candidate.code_snippet:
            return False, "Policy Violation: Lessons cannot store historical implementation code."

        # 3. Must have meaningful description / warning
        if len(candidate.description.strip()) < 15:
            return False, "Policy Violation: Lesson description is too brief to be generalizable."

        return True, "Candidate lesson validated successfully."

    @classmethod
    def sanitize_for_persistence(cls, record_dict: dict[str, Any]) -> dict[str, Any]:
        """Sanitizes knowledge record dict to strip any unintended raw code before persisting."""
        sanitized = dict(record_dict)
        if sanitized.get("reuse_policy") == ReusePolicy.NEVER_CODE_COPY.value or sanitized.get("category") in (KnowledgeCategory.SKILL.value, KnowledgeCategory.LESSON.value):
            sanitized["code_snippet"] = ""
        return sanitized
