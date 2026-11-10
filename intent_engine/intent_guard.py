"""
Hydrameda Intent Engine: Context and Artifact Intent Guard.

Acts as an active firewall preventing out-of-scope context, prohibited patterns,
or poisoned historical files from entering agent prompts or generated worktrees.
"""
from __future__ import annotations

import re

from .models import StructuredIntent
from .policy import PrecedencePolicy


class IntentGuard:
    """Guards prompts, context injection, and worktree artifacts against intent contamination."""

    @classmethod
    def filter_vault_context(cls, intent: StructuredIntent, vault_context: str) -> str:
        """Filters retrieved Obsidian RAG notes so that notes describing prohibited features cannot contaminate the prompt."""
        if not vault_context:
            return ""

        filtered_text, _redacting_notes = PrecedencePolicy.filter_safe_context(
            intent=intent,
            context_text=vault_context,
            context_source="ObsidianVault"
        )
        return filtered_text

    @classmethod
    def filter_repository_context(
        cls,
        intent: StructuredIntent,
        relevant_files: list[str],
        detected_deps: list[str]
    ) -> tuple[list[str], list[str]]:
        """Removes repository files and dependencies that conflict with explicit user prohibitions."""
        prohibitions = intent.get_prohibitions()
        if not prohibitions:
            return relevant_files, detected_deps

        prohib_words: set[str] = set()
        for p in prohibitions:
            words = re.findall(r"\b[A-Za-z0-9_\-]{2,}\b", p.normalized_description.lower())
            for w in words:
                if w not in ("not", "use", "implement", "create", "feature", "features", "unrelated", "package", "packages"):
                    prohib_words.add(w)

        # Filter relevant files
        safe_files = []
        for f in relevant_files:
            f_lower = f.lower()
            if any(pw in f_lower for pw in prohib_words):
                continue
            safe_files.append(f)

        # Filter detected dependencies
        safe_deps = []
        for d in detected_deps:
            d_lower = d.lower()
            if any(pw in d_lower for pw in prohib_words):
                continue
            safe_deps.append(d)

        return safe_files, safe_deps

    @classmethod
    def validate_code_artifacts(
        cls,
        intent: StructuredIntent,
        files: dict[str, str]
    ) -> list[str]:
        """Scans generated or repaired code files for prohibited symbols, classes, or imports."""
        violations: list[str] = []
        prohibitions = intent.get_prohibitions()
        is_jwt_prohibited = any("jwt" in p.normalized_description.lower() for p in prohibitions)

        for fname, content in files.items():
            # Prohibited mock/stub files
            if fname in ("litellm.py", "langgraph.py", "tree_sitter_language_pack.py"):
                violations.append(f"File '{fname}' violates prohibition on stub/mock packages.")

            if is_jwt_prohibited:
                if re.search(r"\bclass\s+JWT[A-Za-z0-9_]*", content):
                    violations.append(f"File '{fname}' declares prohibited JWT exception/class.")
                if re.search(r"\bdef\s+validate_jwt\b", content):
                    violations.append(f"File '{fname}' defines prohibited 'validate_jwt' function.")
                if re.search(r"\bimport\s+jwt\b|\bfrom\s+jwt\b", content):
                    violations.append(f"File '{fname}' imports prohibited third-party 'jwt' library.")

        return violations
