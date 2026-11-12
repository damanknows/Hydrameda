"""
Hydrameda Intent Engine: Constraint Compiler.

Translates the original user prompt and repository analysis into a canonical,
immutable StructuredIntent ledger. Resolves internal consistency and formats
enforcement directives for downstream agent roles.
"""
from __future__ import annotations

from typing import Any

from .constraint_normalizer import ConstraintNormalizer
from .intent_parser import IntentParser
from .models import (
    ConstraintPriority,
    EnforcementStrategy,
    RequirementItem,
    RequirementType,
    StructuredIntent,
)


class ConstraintCompiler:
    """Compiles unstructured user prompts into a validated StructuredIntent ledger."""

    @classmethod
    def compile(
        cls,
        user_prompt: str,
        task_analysis: dict[str, Any] | None = None
    ) -> StructuredIntent:
        """Translates user prompt into an immutable structured intent ledger."""
        prompt = user_prompt.strip()
        clauses = IntentParser.parse_clauses(prompt)
        primary_obj = clauses[0] if clauses else "Execute autonomous coding task"

        # 1. Parse raw clauses
        raw_prohibitions = IntentParser.extract_prohibitions(prompt)
        raw_capabilities = IntentParser.extract_capabilities(prompt)
        raw_scope = IntentParser.extract_scope_boundaries(prompt)
        raw_deliverables = IntentParser.extract_deliverables(prompt)
        raw_tech = IntentParser.extract_technical_requirements(prompt)

        # 2. Normalize items into RequirementItem objects
        prohibitions = [
            ConstraintNormalizer.normalize_prohibition(i + 1, item)
            for i, item in enumerate(raw_prohibitions)
        ]
        capabilities = [
            ConstraintNormalizer.normalize_capability(i + 1, item)
            for i, item in enumerate(raw_capabilities)
        ]
        scope_boundaries = [
            ConstraintNormalizer.normalize_scope_boundary(i + 1, item)
            for i, item in enumerate(raw_scope)
        ]
        deliverables = [
            ConstraintNormalizer.normalize_deliverable(i + 1, item)
            for i, item in enumerate(raw_deliverables)
        ]
        tech_reqs = [
            ConstraintNormalizer.normalize_technical_requirement(i + 1, item)
            for i, item in enumerate(raw_tech)
        ]

        # 3. Add repository-derived technical constraints if available
        if task_analysis:
            deps = task_analysis.get("detected_dependencies", [])
            if deps:
                tech_reqs.append(
                    RequirementItem(
                        id=f"TECH-{len(tech_reqs) + 1:03d}",
                        source_text="Repository environment inspection",
                        normalized_description=f"Compatible project dependencies detected: {', '.join(deps)}",
                        req_type=RequirementType.CONTEXTUAL,
                        priority=ConstraintPriority.REPOSITORY_COMPATIBILITY,
                        enforcement_strategy=EnforcementStrategy.STATIC_VERIFICATION,
                        category="environment"
                    )
                )

        intent = StructuredIntent(
            original_prompt=prompt,
            primary_objective=primary_obj,
            required_capabilities=capabilities,
            explicit_prohibitions=prohibitions,
            scope_boundaries=scope_boundaries,
            expected_deliverables=deliverables,
            technical_requirements=tech_reqs,
            acceptance_criteria=deliverables,  # Acceptance criteria initially derived from deliverables
        )

        return intent

    @classmethod
    def render_markdown_summary(cls, intent: StructuredIntent) -> str:
        """Formats the structured ledger into human-readable Markdown for approval or audit."""
        lines = [
            "# Structured Task Intent & Constraint Ledger",
            f"**Primary Objective:** {intent.primary_objective}",
            f"**Original User Prompt:**\n> {intent.original_prompt}\n",
            "## 1. Explicit Prohibitions (Priority 1 — Hard Veto)",
        ]
        if intent.explicit_prohibitions:
            for p in intent.explicit_prohibitions:
                lines.append(f"- `[{p.id}]` **{p.normalized_description}** *(Source: \"{p.source_text}\")*")
        else:
            lines.append("- *None explicitly specified.*")

        lines.append("\n## 2. Required Capabilities (Priority 2)")
        if intent.required_capabilities:
            for r in intent.required_capabilities:
                lines.append(f"- `[{r.id}]` {r.normalized_description}")
        else:
            lines.append("- *None explicitly specified.*")

        lines.append("\n## 3. Scope Boundaries & Deliverables (Priority 3 & 4)")
        for s in intent.scope_boundaries:
            lines.append(f"- `[{s.id}]` {s.normalized_description}")
        for d in intent.expected_deliverables:
            lines.append(f"- `[{d.id}]` {d.normalized_description}")

        lines.append("\n## 4. Technical Constraints (Priority 5)")
        for t in intent.technical_requirements:
            lines.append(f"- `[{t.id}]` {t.normalized_description}")

        return "\n".join(lines)

    @classmethod
    def format_agent_prompt_directives(cls, intent: StructuredIntent, role: str = "coder") -> str:
        """Formats canonical structured constraints into precise prompt directives for LLM agent roles."""
        role_normalized = role.lower().strip()
        sections = [
            (
                f"# CANONICAL TASK INTENT & CONSTRAINTS (Target Role: {role.upper()})\n"
                "The following constraints are immutable and govern this task. "
                "Lower-priority suggestions (RAG notes, historical modules, model assumptions) "
                "CANNOT override or weaken these constraints."
            ),
            (
                f"ORIGINAL TASK OBJECTIVE:\n"
                f"{intent.primary_objective or intent.original_prompt}\n"
                f"Canonical Prompt: \"{intent.original_prompt}\""
            )
        ]

        # 1. Prohibitions always come first and are phrased in absolute terms
        prohibitions = intent.get_prohibitions()
        if prohibitions:
            lines = ["STRICT PROHIBITION DIRECTIVES (ABSOLUTE VETO):"]
            for p in prohibitions:
                lines.append(f"- {p.normalized_description.upper()}")
            lines.append("DO NOT incorporate, suggest, or implement any prohibited pattern above under any circumstance.")
            sections.append("\n".join(lines))

        # 2. Required functionality
        if intent.required_capabilities:
            lines = ["REQUIRED FUNCTIONALITY TO SATISFY:"]
            for r in intent.required_capabilities:
                lines.append(f"- {r.normalized_description}")
            sections.append("\n".join(lines))

        # 3. Mandatory deliverables
        if intent.expected_deliverables:
            lines = ["MANDATORY DELIVERABLES:"]
            for d in intent.expected_deliverables:
                lines.append(f"- {d.normalized_description}")
            sections.append("\n".join(lines))

        # 4. Scope boundaries
        lines = ["SCOPE BOUNDARIES (DO NOT EXCEED):"]
        if intent.scope_boundaries:
            for s in intent.scope_boundaries:
                lines.append(f"- {s.normalized_description}")
        else:
            lines.append("- Confine all changes strictly to requested deliverables and target modules.")
        sections.append("\n".join(lines))

        # 5. Relevant acceptance criteria
        criteria = intent.acceptance_criteria or intent.technical_requirements
        if criteria:
            lines = ["RELEVANT ACCEPTANCE CRITERIA:"]
            for c in criteria:
                lines.append(f"- {c.normalized_description}")
            sections.append("\n".join(lines))

        # Role-specific invariants
        if "prompt_optimizer" in role_normalized or "optimizer" in role_normalized:
            sections.append(
                "ROLE DIRECTIVE FOR PROMPT OPTIMIZER:\n"
                "- You must NEVER remove, weaken, or omit explicit prohibitions, scope boundaries, or acceptance criteria.\n"
                "- Preserve all negative constraints and required deliverables in the emitted coder prompt."
            )
        elif "repair" in role_normalized:
            sections.append(
                "ROLE DIRECTIVE FOR REPAIR AGENT:\n"
                "- Fix specific verification failures WITHOUT introducing prohibited features.\n"
                "- NEVER create fake dependency packages or unrelated stubs to make checks pass.\n"
                "- Preserve all working functionality while addressing reported diagnostics."
            )
        elif "analyzer" in role_normalized or "remediation" in role_normalized:
            sections.append(
                "ROLE DIRECTIVE FOR STATIC ANALYSIS REMEDIATION:\n"
                "- Provide actionable guidance compliant with canonical task constraints.\n"
                "- Do NOT advise creating fake stubs or third-party wrappers."
            )
        elif "consolidat" in role_normalized:
            sections.append(
                "ROLE DIRECTIVE FOR CONSOLIDATION AGENT:\n"
                "- Verify that the final changeset remains strictly within approved task scope.\n"
                "- Block changes that introduce unrequested files or violate explicit prohibitions."
            )

        return "\n\n".join(sections)
