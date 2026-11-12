"""Hydrameda Experience Engine: Structured Experience & Skill Retriever.

Retrieves vetted skills, lessons, and repository architectural context for a task.
Applies relevance filtering and provenance tracking, formatting the results into
strictly segregated, non-authoritative advisory categories.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from intent_engine.models import StructuredIntent

from .knowledge_store import KnowledgeStore
from .models import KnowledgeCategory, KnowledgeRecord
from .relevance_filter import RelevanceFilter


@dataclass
class RetrievedExperienceContext:
    """Segregated and vetted contextual memory prepared for the epistemic architect."""
    skills: list[KnowledgeRecord] = field(default_factory=list)
    lessons: list[KnowledgeRecord] = field(default_factory=list)
    rejected_items: list[str] = field(default_factory=list)

    def format_skills_section(self) -> str:
        """Formats vetted skills with provenance and advisory notices."""
        if not self.skills:
            return "- *No specific procedural skills retrieved for this domain.*"
        parts = []
        for s in self.skills:
            parts.append(s.format_for_prompt())
        return "\n\n".join(parts)

    def format_lessons_section(self) -> str:
        """Formats vetted failure lessons with provenance and advisory notices."""
        if not self.lessons:
            return "- *No specific failure warnings retrieved for this domain.*"
        parts = []
        for l in self.lessons:
            parts.append(l.format_for_prompt())
        return "\n\n".join(parts)


class ExperienceRetriever:
    """Coordinates skill and experience retrieval, relevance filtering, and context formatting."""

    def __init__(self, store: KnowledgeStore | None = None):
        self.store = store or KnowledgeStore()

    def retrieve(
        self,
        intent: StructuredIntent,
        task_keywords: list[str] | None = None,
        top_k_skills: int = 2,
        top_k_lessons: int = 2
    ) -> RetrievedExperienceContext:
        """Retrieves and filters skills and lessons relevant to the task intent."""
        # Extract keywords from prompt if not supplied
        if not task_keywords:
            task_keywords = re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", intent.original_prompt)

        # 1. Query candidate skills
        candidate_skills = self.store.query(
            category=KnowledgeCategory.SKILL,
            tags=task_keywords,
            keywords=task_keywords
        )

        # 2. Query candidate lessons
        candidate_lessons = self.store.query(
            category=KnowledgeCategory.LESSON,
            tags=task_keywords,
            keywords=task_keywords
        )

        # 3. Apply Relevance & Safety Filter (Hard Veto on prohibited concepts)
        vetted_skills, rejected_skills = RelevanceFilter.filter_knowledge_records(
            intent=intent,
            records=candidate_skills,
            task_keywords=task_keywords
        )
        vetted_lessons, rejected_lessons = RelevanceFilter.filter_knowledge_records(
            intent=intent,
            records=candidate_lessons,
            task_keywords=task_keywords
        )

        return RetrievedExperienceContext(
            skills=vetted_skills[:top_k_skills],
            lessons=vetted_lessons[:top_k_lessons],
            rejected_items=rejected_skills + rejected_lessons
        )
