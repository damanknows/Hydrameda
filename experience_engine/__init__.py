"""Hydrameda Experience Engine: Autonomous Experience Learning & Context Firewall Subsystem.

Enforces:
1. Separation of learning from code reuse.
2. Segregated knowledge categories: SKILL, LESSON, and REPO_CONTEXT.
3. Provenance and validation auditing for all learned knowledge.
4. Impermeable Context Firewall protecting the architect from scope expansion.
"""
from __future__ import annotations

from .context_firewall import ContextFirewall
from .experience_extractor import ExperienceExtractor
from .experience_retriever import ExperienceRetriever, RetrievedExperienceContext
from .knowledge_store import KnowledgeStore
from .learning_policy import LearningPolicy
from .models import (
    ExecutionOutcome,
    KnowledgeCategory,
    KnowledgeRecord,
    ProvenanceRecord,
    ReusePolicy,
)
from .provenance import ProvenanceTracker
from .relevance_filter import RelevanceFilter
from .skill_extractor import SkillExtractor

__all__ = [
    "ContextFirewall",
    "ExecutionOutcome",
    "ExperienceExtractor",
    "ExperienceRetriever",
    "KnowledgeCategory",
    "KnowledgeRecord",
    "KnowledgeStore",
    "LearningPolicy",
    "ProvenanceRecord",
    "ProvenanceTracker",
    "RelevanceFilter",
    "RetrievedExperienceContext",
    "ReusePolicy",
    "SkillExtractor",
]
