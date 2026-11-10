"""Hydrameda Experience Engine: Relevance and Safety Filter.

Filters candidate knowledge items, skills, lessons, and RAG notes against:
1. Canonical task intent and explicit negative prohibitions (Hard Veto).
2. Domain and task relevance.
3. Unrequested scope and protocol expansion.
4. Raw implementation code (enforcing procedural-only knowledge).
"""
from __future__ import annotations

import re

from intent_engine.models import StructuredIntent

from .models import KnowledgeCategory, KnowledgeRecord, ReusePolicy


class RelevanceFilter:
    """Evaluates whether retrieved knowledge items safely and accurately support the current task."""

    @classmethod
    def evaluate_record(
        cls,
        intent: StructuredIntent,
        record: KnowledgeRecord,
        task_keywords: list[str] | None = None
    ) -> tuple[bool, str]:
        """Evaluates a single KnowledgeRecord for relevance, safety, and constraint adherence.

        Returns (is_acceptable, explanation).
        """
        rec_title_desc = f"{record.title} {record.description}".lower()

        # 1. Enforcement of Explicit Prohibitions (Priority 1 Hard Veto)
        prohibitions = intent.get_prohibitions()
        for p in prohibitions:
            p_desc = p.normalized_description.lower()
            p_words = [
                w for w in re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", p_desc)
                if w not in ("not", "use", "implement", "create", "feature", "features", "unrelated", "package", "packages")
            ]
            for kw in p_words:
                if kw in rec_title_desc:
                    # If this is a Failure Warning warning AGAINST the prohibited feature, it reinforces intent!
                    is_warning_against = (
                        record.reuse_policy == ReusePolicy.FAILURE_WARNING or
                        "warning" in record.title.lower() or
                        "anti-pattern" in record.title.lower() or
                        "prohibited" in record.title.lower() or
                        "contamination" in record.title.lower()
                    )
                    # If it's a procedural skill suggesting how to implement the prohibited item, VETO IT!
                    if record.category == KnowledgeCategory.SKILL and not is_warning_against:
                        return (
                            False,
                            (
                                f"Hard Veto: Skill '{record.title}' advocates or implements prohibited keyword '{kw}' "
                                f"(violates constraint {p.id}: {p.normalized_description})."
                            )
                        )
                    # Check procedure steps for affirmative implementation of prohibited feature
                    if not is_warning_against:
                        for step in record.procedure:
                            if kw in step.lower():
                                return (
                                    False,
                                    f"Hard Veto: Step in '{record.title}' introduces prohibited feature '{kw}'."
                                )
                    else:
                        for step in record.procedure:
                            step_lower = step.lower()
                            if kw in step_lower:
                                is_disclaiming = bool(re.search(r"\b(?:no|not|never|avoid|without|do not|disclaim|prohibit)\b", step_lower))
                                if not is_disclaiming:
                                    return (
                                        False,
                                        f"Hard Veto: Step in '{record.title}' mentions '{kw}' without negative disclaiming."
                                    )

        # 2. Reject Raw Implementation Code
        if record.code_snippet:
            return (
                False,
                (
                    f"Policy Rejection: Record '{record.title}' contains raw source code. "
                    "Only procedural guidance is permitted."
                )
            )

        # 3. Unrequested Protocol & Scope Expansion Detection
        user_prompt_lower = intent.original_prompt.lower()
        protocols_to_check = [("jwt", "JSON Web Token"), ("oauth", "OAuth"), ("saml", "SAML")]
        for proto_kw, proto_label in protocols_to_check:
            if proto_kw not in user_prompt_lower and proto_kw in rec_title_desc and record.reuse_policy != ReusePolicy.FAILURE_WARNING:
                return (
                    False,
                    f"Scope Expansion: Record '{record.title}' introduces unrequested protocol '{proto_label}'."
                )

        # 4. Relevance & Domain Compatibility
        if task_keywords:
            task_kw_set = {k.lower() for k in task_keywords}
            rec_terms = {a.lower() for a in record.applicability}
            rec_words = set(re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", rec_title_desc))

            # Record must share at least one tag or keyword, or have high general relevance
            has_tag_overlap = bool(task_kw_set.intersection(rec_terms))
            has_word_overlap = bool(task_kw_set.intersection(rec_words))

            if not (has_tag_overlap or has_word_overlap):
                return (
                    False,
                    f"Irrelevant: Record '{record.title}' does not match current task domain or keywords."
                )

        return True, "Record is relevant and adheres strictly to task intent and constraints."

    @classmethod
    def filter_knowledge_records(
        cls,
        intent: StructuredIntent,
        records: list[KnowledgeRecord],
        task_keywords: list[str] | None = None
    ) -> tuple[list[KnowledgeRecord], list[str]]:
        """Filters a list of knowledge records, returning accepted records and audit rejection logs."""
        accepted: list[KnowledgeRecord] = []
        rejections: list[str] = []

        for r in records:
            ok, reason = cls.evaluate_record(intent, r, task_keywords)
            if ok:
                accepted.append(r)
            else:
                rejections.append(f"[{r.knowledge_id}] Rejected: {reason}")

        return accepted, rejections
