"""
Hydrameda Intent Engine: Precedence Policy and Conflict Resolver.

Enforces strict priority ordering:
 1. Explicit user prohibitions (absolute veto; Priority 1)
 2. Explicit user requirements (Priority 2)
 3. User-defined scope boundaries (Priority 3)
 4. Current task acceptance criteria (Priority 4)
 5. Repository compatibility requirements (Priority 5)
 6. Retrieved engineering experience (Priority 6)
 7. RAG contextual suggestions (Priority 7)
 8. Model-generated assumptions (Priority 8)

Lower-priority context can NEVER override or weaken higher-priority instructions.
A hard prohibition cannot be outweighed by an LLM confidence score or semantic similarity score.
"""
from __future__ import annotations

import re

from .models import ConstraintPriority, RequirementItem, StructuredIntent


class PrecedencePolicy:
    """Evaluates constraint precedence and enforces priority conflict resolution."""

    @staticmethod
    def get_priority_rank(item: RequirementItem) -> int:
        """Returns the integer priority value (1 is highest priority)."""
        return int(item.priority)

    @staticmethod
    def is_strictly_higher_priority(item_a: RequirementItem, item_b: RequirementItem) -> bool:
        """Returns True if item_a has strictly higher precedence than item_b."""
        return int(item_a.priority) < int(item_b.priority)

    @staticmethod
    def has_higher_precedence(priority_a: int | ConstraintPriority, priority_b: int | ConstraintPriority) -> bool:
        """Returns True if priority_a has higher precedence than priority_b (lower numeric value)."""
        return int(priority_a) < int(priority_b)

    @classmethod
    def overrides(cls, item_a: RequirementItem, item_b: RequirementItem) -> bool:
        """Returns True if item_a has strictly higher precedence than item_b and overrides it."""
        return cls.is_strictly_higher_priority(item_a, item_b)

    @staticmethod
    def is_prohibited(intent: StructuredIntent, candidate_term: str) -> tuple[bool, RequirementItem | None]:
        """Checks if a term, symbol, or feature is vetoed by an explicit prohibition (Priority 1)."""
        cand_clean = candidate_term.strip().lower()
        if not cand_clean:
            return False, None

        for item in intent.get_prohibitions():
            prohib_desc = item.normalized_description.lower()
            prohib_src = item.source_text.lower()

            # Exact or word boundary match
            pattern = rf"\b{re.escape(cand_clean)}\b"
            if re.search(pattern, prohib_desc) or re.search(pattern, prohib_src):
                return True, item

            # Inverse check: candidate matches prohibition keyword
            for word in re.findall(r"\b[A-Za-z0-9_\-]{2,}\b", prohib_desc):
                if word not in ("not", "use", "implement", "create", "with", "without", "any") and word == cand_clean:
                    return True, item

        return False, None

    @classmethod
    def resolve_context_conflict(
        cls,
        intent: StructuredIntent,
        context_source: str,
        context_text: str,
        context_priority: ConstraintPriority = ConstraintPriority.RAG_SUGGESTION
    ) -> list[str]:
        """Scans retrieved context or suggestions and returns any detected violations of higher-priority constraints."""
        violations: list[str] = []
        context_lower = context_text.lower()

        # Check against all explicit prohibitions (Priority 1)
        for prohib in intent.get_prohibitions():
            desc_words = [
                w.lower() for w in re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", prohib.normalized_description)
                if w.lower() not in ("not", "use", "implement", "create", "feature", "features", "unrelated", "package", "packages")
            ]
            for kw in desc_words:
                # Check if context advocates or defines the prohibited term
                if kw in context_lower:
                    # Filter out negative statements within context
                    negated_pattern = rf"(?:no|not|without|never|avoid|do not implement)\s+{re.escape(kw)}"
                    if not re.search(negated_pattern, context_lower):
                        violations.append(
                            f"Context from '{context_source}' (Priority {context_priority.value}) suggests or implements '{kw}', "
                            f"which violates explicit user prohibition '{prohib.normalized_description}' (Priority {prohib.priority.value})."
                        )
                        break

        return violations

    check_context_conflict = resolve_context_conflict

    @classmethod
    def filter_safe_context(
        cls,
        intent: StructuredIntent,
        context_text: str,
        context_source: str = "context"
    ) -> tuple[str, list[str]]:
        """Filters or redacts conflicting sections from retrieved context to prevent LLM context poisoning."""
        prohibitions = intent.get_prohibitions()
        if not prohibitions or not context_text:
            return context_text, []

        redacted_notes: list[str] = []
        safe_lines: list[str] = []

        for line in context_text.splitlines():
            line_lower = line.lower()
            line_violates = False

            for prohib in prohibitions:
                desc_words = [
                    w.lower() for w in re.findall(r"\b[A-Za-z0-9_\-]{3,}\b", prohib.normalized_description)
                    if w.lower() not in ("not", "use", "implement", "create", "feature", "features", "unrelated", "package", "packages")
                ]
                for kw in desc_words:
                    if kw in line_lower and not re.search(rf"(?:no|not|without|never|avoid|non[- ]?{re.escape(kw)})\b", line_lower):
                        line_violates = True
                        redacted_notes.append(f"Redacted conflicting line mentioning '{kw}' (violates {prohib.id}: {prohib.normalized_description})")
                        break
                if line_violates:
                    break

            if not line_violates:
                safe_lines.append(line)

        return "\n".join(safe_lines), redacted_notes
